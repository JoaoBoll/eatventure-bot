"""Layout do dataset em disco: uma raiz por resolução de coleta.

    dataset/data/<LARGURAxALTURA>/samples.jsonl
    dataset/data/<LARGURAxALTURA>/images/<AAAA-MM-DD>/<id>.<ext>

A resolução separa a coleta e os treinos automáticos. Cada raiz é
auto-contida (índice + imagens) e o campo
`image` de cada registro é relativo à raiz DELE, então dá para mover, copiar
ou apagar uma resolução sem invalidar as outras.
"""

import re
from pathlib import Path

DATA_DIRNAME = "data"
INDEX_NAME = "samples.jsonl"
IMAGES_DIRNAME = "images"
REPLAY_ROWS = 12_000
MIN_FRESH_TARGETS = 10


def confirmed_training_sample(record):
    """Dados antigos usam o sinal de resultado; novas coletas registram a evidência."""
    evidence = record.get("label_evidence")
    boxes = record.get("boxes") or []
    if not record.get("action"):
        return not boxes and record.get("outcome") is None and evidence in (None, "detector_empty")
    if record.get("outcome") != "changed" or evidence not in (None, "target_changed"):
        return False
    return any(
        isinstance(box, dict) and box.get("acted") is True and box.get("category")
        and isinstance(box.get("width"), int) and box["width"] > 0
        and isinstance(box.get("height"), int) and box["height"] > 0
        for box in boxes
    )


def automatic_minimum(root, resolution, requested):
    shard = Path(root) if resolution == "legacy" else Path(root) / DATA_DIRNAME / resolution
    if (shard / ".collection-pressure").is_file():
        return min(requested, 200, max(20, (requested + 9) // 10))
    return requested


def shard_name(width, height):
    return f"{width}x{height}"


def shard_root(dataset_root, width, height):
    return (
        Path(dataset_root)
        / DATA_DIRNAME
        / shard_name(width, height)
    )


def index_path(shard):
    return Path(shard) / INDEX_NAME


def images_dir(shard):
    return Path(shard) / IMAGES_DIRNAME


def shard_roots(dataset_root):
    """Raízes de índice sob dataset_root, em ordem estável.

    Inclui a raiz antiga (índice direto em dataset/) para o dado já coletado
    continuar treinável sem precisar de migração."""

    raiz = Path(dataset_root)

    encontradas = []

    data_dir = raiz / DATA_DIRNAME

    if data_dir.is_dir():

        for pasta in sorted(data_dir.iterdir()):

            if index_path(pasta).exists():
                encontradas.append(pasta)

    if index_path(raiz).exists():
        encontradas.append(raiz)

    return encontradas


def record_resolution(record):
    shard = record.get("_shard")
    if isinstance(shard, str) and re.fullmatch(r"[1-9]\d*x[1-9]\d*", shard):
        return shard
    width, height = record.get("frame_width"), record.get("frame_height")
    if type(width) is int and width > 0 and type(height) is int and height > 0:
        return f"{width}x{height}"
    return "legacy"
