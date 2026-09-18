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
invalida o cache, porque os vetores antigos deixam de ser comparáveis.
"""

import json
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
        """Apaga os chunks desta resolução (assinatura mudou)."""

        for chunk in self._chunks():
            chunk.unlink()

        if self.meta_path.exists():
            self.meta_path.unlink()

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

    def append(self, X, y, ids):
        """Grava um chunk novo. Não reescreve nada do que já estava."""

        if len(X) == 0:
            return None

        self.dir.mkdir(parents=True, exist_ok=True)

        self.meta_path.write_text(
            json.dumps(self.assinatura, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        existentes = self._chunks()

        proximo = len(existentes) + 1

        destino = self.dir / f"{proximo:06d}.npz"

        while destino.exists():
            proximo += 1
            destino = self.dir / f"{proximo:06d}.npz"

        np.savez_compressed(
            destino,
            X=np.asarray(X, dtype=DISK_DTYPE),
            y=np.asarray(y),
            ids=np.asarray(ids),
        )

        return destino

    def stats(self):
        chunks = self._chunks()

        return {
            "chunks": len(chunks),
            "bytes": sum(c.stat().st_size for c in chunks),
        }
