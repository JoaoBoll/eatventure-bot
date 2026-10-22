"""Snapshots e limpeza de lotes sem interromper a gravação da coleta."""

import json
import os
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from dataset.layout import INDEX_NAME, shard_roots


@contextmanager
def _file_lock(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as arquivo:
        if os.name == "nt":
            import msvcrt

            # Pode bloquear além do EOF; ler o byte antes falha se outro acesso já o bloqueou.
            while True:
                try:
                    arquivo.seek(0)
                    msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (11, 13, 36):
                        raise
                    if not blocking:
                        raise BlockingIOError("Já existe um treino em andamento.") from error
                    time.sleep(0.02)
            try:
                yield
            finally:
                arquivo.seek(0)
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(arquivo.fileno(), flags)
            try:
                yield
            finally:
                fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)


def index_lock(root):
    return _file_lock(Path(root).resolve() / ".index.lock")


def training_lock(root):
    return _file_lock(Path(root).resolve() / ".training.lock", blocking=False)


def _inside(path, root):
    return path.resolve().is_relative_to(root)


def snapshot_records(root, limit=None):
    root = Path(root).resolve()
    resume_cleanup(root)
    registros = []
    aviso = {"invalidas": 0, "sem_imagem": 0, "raizes": {}}
    with index_lock(root):
        for pasta in shard_roots(root):
            if not _inside(pasta, root):
                continue
            rotulo = "." if pasta == root else pasta.name
            aviso["raizes"][rotulo] = 0
            with (pasta / INDEX_NAME).open(encoding="utf-8") as arquivo:
                for linha in arquivo:
                    if not linha.strip():
                        continue
                    try:
                        registro = json.loads(linha)
                    except ValueError:
                        aviso["invalidas"] += 1
                        continue
                    if not isinstance(registro, dict):
                        aviso["invalidas"] += 1
                        continue
                    imagem = registro.get("image")
                    if not isinstance(imagem, str) or not imagem:
                        aviso["sem_imagem"] += 1
                        continue
                    caminho = pasta / imagem
                    if not _inside(caminho, root) or not caminho.is_file():
                        aviso["sem_imagem"] += 1
                        continue
                    registro.update(
                        _root=pasta,
                        _shard=rotulo,
                        _path=caminho,
                        _source_line=linha,
                    )
                    registros.append(registro)
                    aviso["raizes"][rotulo] += 1
                    if limit and len(registros) >= limit:
                        return registros, aviso
    return registros, aviso


def _replace_index(path, lines):
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=".samples-", suffix=".tmp", delete=False,
        ) as arquivo:
            temporario = Path(arquivo.name)
            arquivo.writelines(lines)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, path)
    finally:
        if temporario is not None and temporario.exists():
            temporario.unlink()


def _cleanup_records(root, registros, consumed_ids):
    root = Path(root).resolve()
    consumidos = {str(identificador) for identificador in consumed_ids}
    por_indice = defaultdict(list)
    resultado = {"records_removed": 0, "images_removed": 0, "bytes_freed": 0, "errors": []}
    for registro in registros:
        identificador = str(registro.get("id") or Path(registro["_path"]).stem)
        if identificador not in consumidos or "_source_line" not in registro:
            continue
        indice = Path(registro["_root"]) / INDEX_NAME
        imagem = Path(registro["_path"])
        if _inside(indice, root) and _inside(imagem, root):
            por_indice[indice].append(registro)

    imagens = {Path(registro["_path"]).resolve() for lote in por_indice.values() for registro in lote}
    with index_lock(root):
        for indice, lote in por_indice.items():
            if not indice.is_file():
                continue
            ocorrencias = Counter(registro["_source_line"] for registro in lote)
            mantidas = []
            removidas = 0
            try:
                with indice.open(encoding="utf-8", newline="") as arquivo:
                    for linha in arquivo:
                        # Compara o conteúdo do snapshot, preservando linhas novas ou editadas.
                        normalizada = linha.replace("\r\n", "\n")
                        if ocorrencias[normalizada] > 0:
                            ocorrencias[normalizada] -= 1
                            removidas += 1
                        else:
                            mantidas.append(linha)
                if removidas:
                    _replace_index(indice, mantidas)
                    resultado["records_removed"] += removidas
                    for registro in lote:
                        if ocorrencias[registro["_source_line"]] == 0:
                            imagens.add(Path(registro["_path"]).resolve())
            except OSError as error:
                resultado["errors"].append(f"{indice}: {error}")

        referenciadas = set()
        for pasta in shard_roots(root):
            if not _inside(pasta, root):
                continue
            try:
                with (pasta / INDEX_NAME).open(encoding="utf-8") as arquivo:
                    for linha in arquivo:
                        try:
                            registro = json.loads(linha)
                        except ValueError:
                            continue
                        imagem = registro.get("image") if isinstance(registro, dict) else None
                        if isinstance(imagem, str) and imagem:
                            referenciadas.add((pasta / imagem).resolve())
            except OSError as error:
                resultado["errors"].append(f"{pasta}: {error}")
                # Sem conferir as referências restantes, mantém todas as imagens.
                return resultado

        for imagem in imagens - referenciadas:
            if not _inside(imagem, root) or not imagem.is_file():
                continue
            try:
                tamanho = imagem.stat().st_size
                imagem.unlink()
                resultado["images_removed"] += 1
                resultado["bytes_freed"] += tamanho
            except OSError as error:
                resultado["errors"].append(f"{imagem}: {error}")
    return resultado


def _write_json(path, data):
    _replace_index(path, [json.dumps(data, ensure_ascii=False, indent=2) + "\n"])


def cleanup_records(root, registros, consumed_ids):
    root = Path(root).resolve()
    consumidos = {str(value) for value in consumed_ids}
    lote = []
    for registro in registros:
        imagem = Path(registro["_path"]).resolve()
        pasta = Path(registro["_root"]).resolve()
        identificador = str(registro.get("id") or imagem.stem)
        if identificador not in consumidos or "_source_line" not in registro:
            continue
        if not _inside(imagem, root) or not _inside(pasta / INDEX_NAME, root):
            continue
        lote.append({
            "id": identificador,
            "root": pasta.relative_to(root).as_posix(),
            "image": imagem.relative_to(root).as_posix(),
            "source_line": registro["_source_line"],
        })
    pasta = root / ".cleanup"
    pasta.mkdir(parents=True, exist_ok=True)
    journal = pasta / f"{uuid.uuid4().hex}.json"
    _write_json(journal, lote)
    return _finish_cleanup(root, journal, lote)


def _finish_cleanup(root, journal, lote):
    registros = [
        {
            "id": item["id"],
            "_root": root / item["root"],
            "_path": root / item["image"],
            "_source_line": item["source_line"],
        }
        for item in lote
    ]
    resultado = _cleanup_records(root, registros, {r["id"] for r in registros})
    if not resultado["errors"]:
        journal.unlink(missing_ok=True)
    else:
        resultado["pending_journal"] = str(journal)
    return resultado


def resume_cleanup(root):
    root = Path(root).resolve()
    resultados = []
    for journal in sorted((root / ".cleanup").glob("*.json")):
        try:
            lote = json.loads(journal.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        resultados.append(_finish_cleanup(root, journal, lote))
    return resultados


def trained_sample_ids(root, cache_root=None):
    cache = (Path(cache_root).expanduser().resolve() if cache_root else Path(root) / "cache") / "category"
    ids = set()
    for path in [cache / "trained.json", *cache.glob("*/trained.json")]:
        if not path.is_file():
            continue
        shard = path.parent.relative_to(cache).as_posix()
        data = json.loads(path.read_text(encoding="utf-8"))
        ids.update(f"{shard}/{value}" for value in data.get("ids", []))
    return ids


def raw_dataset_bytes(root):
    registros, _ = snapshot_records(root)
    total = 0
    for caminho in {Path(registro["_path"]).resolve() for registro in registros}:
        try:
            total += caminho.stat().st_size
        except FileNotFoundError:
            continue
    return total
