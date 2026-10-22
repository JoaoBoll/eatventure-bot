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

import json
import os
import tempfile
from pathlib import Path

import numpy as np

META_NAME = "meta.json"

# float16 em disco: metade do tamanho, e as features são pixels k/255 e
# histogramas normalizados — ficam dentro da precisão do float16. Em memória
# volta para float32, que é o que o sklearn usa.
DISK_DTYPE = np.float16


def signature(kind, negativos, feature_size, patch_size, patch_margin, hsv_bins):
    return {
        "kind": kind,
        "negativos": int(negativos),
        "feature_size": int(feature_size),
        "patch_size": list(patch_size)
        if isinstance(patch_size, (list, tuple))
        else int(patch_size),
        "patch_margin": float(patch_margin),
        "hsv_bins": int(hsv_bins),
    }


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
            f"O cache {self.dir} contém memória acumulada e não é apagado "
            "automaticamente. Use outro --cache-root para features incompatíveis."
        )

    def metadata(self):
        registros = {}
        for arquivo in sorted(self.dir.glob("*.records.json")):
            lote = json.loads(arquivo.read_text(encoding="utf-8"))
            if not isinstance(lote, dict):
                raise ValueError(f"Metadados de cache inválidos: {arquivo}")
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
                f"Cache legado em {self.dir}: faltam metadados de "
                f"{len(ausentes)} amostra(s). Preserve o cache e restaure os "
                "dados originais, ou use outro --cache-root."
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
            raise RuntimeError(f"Cache incompatível preservado: {self.dir}")

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
                    raise ValueError("Há features sem metadados de amostra.")
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
