"""Layout do dataset em disco: uma raiz por resolução de coleta.

    dataset/data/<LARGURAxALTURA>/samples.jsonl
    dataset/data/<LARGURAxALTURA>/images/<AAAA-MM-DD>/<id>.<ext>

A resolução separa só a COLETA. Para o treino ela não significa nada: ele
junta todas as raízes. Cada raiz é auto-contida (índice + imagens) e o campo
`image` de cada registro é relativo à raiz DELE, então dá para mover, copiar
ou apagar uma resolução sem invalidar as outras.
"""

from pathlib import Path

DATA_DIRNAME = "data"
INDEX_NAME = "samples.jsonl"
IMAGES_DIRNAME = "images"


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
