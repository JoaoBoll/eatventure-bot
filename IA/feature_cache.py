"""Cache de features: cada amostra é extraída uma vez na vida.

O treino gastava o tempo re-decodificando os JPGs a cada execução para
recalcular sempre os mesmos FEATURE_SIZE floats por caixa. Aqui o vetor e o
rótulo ficam guardados, então uma execução nova só extrai o que é novo — e,
com o cache formado, a imagem original não é mais necessária para treinar.

Os chunks são append-only de propósito: nada é reescrito, então dado novo
custa só o próprio tamanho e a execução não fica mais lenta conforme o
dataset cresce.

    dataset/cache/<kind>/<resolucao>/meta.json
    dataset/cache/<kind>/<resolucao>/000001.npz   (X, y, ids)

`meta.json` guarda a assinatura das features. Mexer em features.py (tamanho
do patch, margem, bins do HSV) ou no número de negativos muda a assinatura e
torna o cache incompatível, porque os vetores deixam de ser comparáveis.
O treino preserva esse cache e interrompe a execução nesse caso.
"""

from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np

_source_root = str(Path(__file__).resolve().parent.parent / "src")
if _source_root not in sys.path:
    sys.path.insert(0, _source_root)

from core.i18n import t
from dataset.layout import REPLAY_ROWS

META_NAME = "meta.json"

# float16 em disco: metade do tamanho, e as features são pixels k/255 e
# histogramas normalizados — ficam dentro da precisão do float16. Em memória
# volta para float32, que é o que o sklearn usa.
DISK_DTYPE = np.float16


def signature(kind, negativos, feature_size, patch_size, patch_margin, hsv_bins, label_source="all"):
    return {
        "kind": kind,
        "negativos": int(negativos),
        "feature_size": int(feature_size),
        "patch_size": list(patch_size)
        if isinstance(patch_size, (list, tuple))
        else int(patch_size),
        "patch_margin": float(patch_margin),
        "hsv_bins": int(hsv_bins),
        "label_source": label_source,
    }


class BoundedReplay:
    """Memória persistente por resolução. Cada linha representa um recorte."""

    def __init__(self, cache_root, shard, assinatura, max_rows=REPLAY_ROWS):
        folder = "_legacy" if shard == "." else shard
        if not folder or folder in (".", "..") or Path(folder).name != folder:
            raise ValueError("Invalid replay shard")
        if not 1 <= max_rows <= 100_000:
            raise ValueError("Invalid replay capacity")
        digest = hashlib.sha256(json.dumps(assinatura, sort_keys=True).encode()).hexdigest()[:20]
        self.dir = Path(cache_root) / "continuous" / folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{digest}.sqlite3"
        self.width = assinatura["feature_size"]
        self.max_rows = max_rows
        with self._db() as db:
            db.execute("PRAGMA auto_vacuum=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS frames (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS crops (owner TEXT NOT NULL, label TEXT NOT NULL, feature BLOB NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS crops_owner ON crops(owner)")
            db.execute("CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY)")
            self._trim(db)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _trim(self, db):
        if db.execute("SELECT COUNT(*) FROM crops").fetchone()[0] <= self.max_rows:
            return
        # Reserva espaço para categorias raras. Dentro de cada categoria,
        # alterna exemplos históricos e recentes para acompanhar mudanças.
        db.execute("""
            DELETE FROM crops WHERE rowid IN (
                SELECT rid FROM (
                    SELECT rowid AS rid,
                        row_number() OVER (PARTITION BY label ORDER BY rowid DESC) AS newest,
                        row_number() OVER (PARTITION BY label ORDER BY rowid ASC) AS oldest
                    FROM crops
                ) ORDER BY min(oldest, newest), rid DESC LIMIT -1 OFFSET ?
            )""", (self.max_rows,))
        db.execute("DELETE FROM frames WHERE NOT EXISTS (SELECT 1 FROM crops WHERE owner=frames.id)")

    def ids(self):
        with self._db() as db:
            return {row[0] for row in db.execute("SELECT id FROM frames")}

    def metadata(self):
        with self._db() as db:
            return {key: json.loads(value) for key, value in db.execute("SELECT id,metadata FROM frames")}

    def ensure_metadata(self, _records):
        return self.metadata()

    def append(self, X, y, ids, records):
        if not len(X):
            return
        if X.shape != (len(ids), self.width) or len(y) != len(ids):
            raise ValueError("Invalid replay feature dimensions")
        with self._db() as db:
            # Uma amostra reprocessada substitui seus recortes anteriores.
            owners = {str(identifier) for identifier in ids}
            db.executemany("DELETE FROM crops WHERE owner=?", ((owner,) for owner in owners))
            db.executemany("INSERT OR REPLACE INTO frames VALUES (?,?)", (
                (owner, json.dumps(records[owner], ensure_ascii=False)) for owner in owners
            ))
            for start in range(0, len(X), self.max_rows):
                stop = min(start + self.max_rows, len(X))
                db.executemany("INSERT INTO crops VALUES (?,?,?)", (
                    (str(ids[i]), str(y[i]), np.asarray(X[i], dtype=DISK_DTYPE).tobytes())
                    for i in range(start, stop)
                ))
                if stop < len(X):
                    # Metadados podem pertencer a um bloco posterior.
                    self._trim_crops_only(db)
            self._trim(db)
            db.execute("DELETE FROM frames WHERE NOT EXISTS (SELECT 1 FROM crops WHERE owner=frames.id)")

    def _trim_crops_only(self, db):
        db.execute("""
            DELETE FROM crops WHERE rowid IN (
                SELECT rid FROM (
                    SELECT rowid AS rid, row_number() OVER (PARTITION BY label ORDER BY rowid DESC) AS rank
                    FROM crops
                ) ORDER BY rank, rid DESC LIMIT -1 OFFSET ?
            )""", (self.max_rows,))

    def load(self):
        with self._db() as db:
            rows = db.execute("SELECT owner,label,feature FROM crops ORDER BY rowid").fetchall()
        if not rows:
            return np.empty((0, self.width), dtype=np.float32), np.asarray([], dtype=str), np.asarray([], dtype=str)
        X = np.stack([np.frombuffer(row[2], dtype=DISK_DTYPE) for row in rows]).astype(np.float32)
        return X, np.asarray([row[1] for row in rows]), np.asarray([row[0] for row in rows])

    def trained_ids(self):
        with self._db() as db:
            return {row[0] for row in db.execute("SELECT id FROM processed")}

    def mark_trained(self, ids, live_ids):
        # Os recibos só precisam sobreviver enquanto existir a imagem bruta.
        live = {str(identifier) for identifier in live_ids}
        with self._db() as db:
            db.execute("CREATE TEMP TABLE live(id TEXT PRIMARY KEY)")
            db.executemany("INSERT INTO live VALUES (?)", ((key,) for key in live))
            db.execute("DELETE FROM processed WHERE id NOT IN (SELECT id FROM live)")
            db.executemany("INSERT OR IGNORE INTO processed VALUES (?)", (
                (key,) for key in set(ids) & live
            ))


class FeatureCache:
    """Um cache por (kind, resolução). `shard` é o nome da pasta da resolução."""

    def __init__(self, cache_root, kind, shard, assinatura):
        self.dir = Path(cache_root) / kind / shard
        self.assinatura = assinatura

    @property
    def meta_path(self):
        return self.dir / META_NAME

    def _chunks(self):
        return sorted(self.dir.glob("*.npz"))

    def valido(self):
        """False quando a assinatura mudou — os vetores guardados não servem mais."""

        if not self.meta_path.exists():
            return not self._chunks()

        try:
            gravada = json.loads(
                self.meta_path.read_text(encoding="utf-8")
            )

        except (OSError, ValueError):
            return False

        return gravada == self.assinatura

    def descartar(self):
        raise RuntimeError(
            t("messages.cache_preserved_warning", value_1=self.dir)
        )

    def metadata(self):
        registros = {}
        for arquivo in sorted(self.dir.glob("*.records.json")):
            lote = json.loads(arquivo.read_text(encoding="utf-8"))
            if not isinstance(lote, dict):
                raise ValueError(t("messages.invalid_cache_metadata", arquivo=arquivo))
            registros.update(lote)
        return registros

    def ensure_metadata(self, registros):
        from training_run import atomic_json

        conhecidos = self.ids()
        existentes = self.metadata()
        faltantes = conhecidos - existentes.keys()
        recuperados = {
            dono: registros[dono]
            for dono in faltantes
            if dono in registros
        }
        if recuperados:
            destino = self.dir / "recovered.records.json"
            anteriores = (
                json.loads(destino.read_text(encoding="utf-8"))
                if destino.exists() else {}
            )
            atomic_json(destino, {**anteriores, **recuperados})
            existentes.update(recuperados)
        ausentes = conhecidos - existentes.keys()
        if ausentes:
            raise RuntimeError(
                t("messages.legacy_cache_metadata_missing", value_1=self.dir, value_2=len(ausentes))
            )
        return existentes

    def trained_ids(self):
        arquivo = self.dir / "trained.json"
        if not arquivo.exists():
            return set()
        return set(json.loads(arquivo.read_text(encoding="utf-8"))["ids"])

    def mark_trained(self, ids):
        from training_run import atomic_json

        atomic_json(
            self.dir / "trained.json",
            {"ids": sorted(self.trained_ids() | set(ids))},
        )

    def ids(self):
        """Ids de amostra já extraídos — o que não está aqui é trabalho novo."""

        encontrados = set()

        for chunk in self._chunks():

            with np.load(chunk, allow_pickle=False) as dados:
                encontrados.update(dados["ids"].tolist())

        return encontrados

    def load(self):
        """(X float32, y, ids) de todos os chunks juntos."""

        partes_x = []
        partes_y = []
        partes_id = []

        for chunk in self._chunks():

            with np.load(chunk, allow_pickle=False) as dados:
                partes_x.append(dados["X"])
                partes_y.append(dados["y"])
                partes_id.append(dados["ids"])

        if not partes_x:

            return (
                np.empty((0, self.assinatura["feature_size"]), np.float32),
                np.empty((0,), dtype="<U32"),
                np.empty((0,), dtype="<U40"),
            )

        return (
            np.concatenate(partes_x).astype(np.float32),
            np.concatenate(partes_y),
            np.concatenate(partes_id),
        )

    def append(self, X, y, ids, records=None):
        """Grava um chunk novo. Não reescreve nada do que já estava."""

        if len(X) == 0:
            return None

        self.dir.mkdir(parents=True, exist_ok=True)

        from training_run import atomic_json

        if not self.valido():
            raise RuntimeError(t("messages.incompatible_cache_preserved", value_1=self.dir))

        atomic_json(self.meta_path, self.assinatura)

        existentes = self._chunks()

        proximo = len(existentes) + 1

        destino = self.dir / f"{proximo:06d}.npz"

        while destino.exists():
            proximo += 1
            destino = self.dir / f"{proximo:06d}.npz"

        handle, temporario = tempfile.mkstemp(
            prefix=".chunk-", suffix=".tmp", dir=self.dir
        )
        try:
            with os.fdopen(handle, "wb") as arquivo:
                np.savez_compressed(
                    arquivo,
                    X=np.asarray(X, dtype=DISK_DTYPE),
                    y=np.asarray(y),
                    ids=np.asarray(ids),
                )
                arquivo.flush()
                os.fsync(arquivo.fileno())
            if records is not None:
                utilizados = set(str(dono) for dono in ids)
                if utilizados - records.keys():
                    raise ValueError(t("messages.sample_metadata_missing"))
                atomic_json(
                    destino.with_suffix(".records.json"),
                    {dono: records[dono] for dono in sorted(utilizados)},
                )
            os.replace(temporario, destino)
        finally:
            if os.path.exists(temporario):
                os.unlink(temporario)

        return destino

    def stats(self):
        chunks = self._chunks()

        return {
            "chunks": len(chunks),
            "bytes": sum(c.stat().st_size for c in chunks),
        }
