"""Snapshots e limpeza de lotes sem interromper a gravação da coleta."""

import json
from core.i18n import t
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from dataset.layout import (
    INDEX_NAME,
    POST_TRAINING_TEST_DIRNAME,
    shard_roots,
)


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
                        raise BlockingIOError(t("messages.training_in_progress")) from error
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


def post_training_test_root(root, resolution):
    """Raiz persistente que fica fora das fontes de treino e do replay."""
    root = Path(root).resolve()
    if not isinstance(resolution, str) or not resolution or Path(resolution).name != resolution:
        raise ValueError("Invalid test resolution")
    return root / POST_TRAINING_TEST_DIRNAME / resolution


def snapshot_post_training_test(root, resolution):
    """Lê o conjunto reservado sem misturá-lo ao snapshot treinável."""
    root = Path(root).resolve()
    shard = post_training_test_root(root, resolution)
    index = shard / INDEX_NAME
    warnings = {"invalidas": 0, "sem_imagem": 0}
    if not index.is_file():
        return [], warnings

    records = []
    with index.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                warnings["invalidas"] += 1
                continue
            if not isinstance(record, dict) or not isinstance(record.get("image"), str):
                warnings["invalidas"] += 1
                continue
            image = shard / record["image"]
            if not _inside(image, shard) or not image.is_file():
                warnings["sem_imagem"] += 1
                continue
            records.append({
                **record,
                "_root": shard,
                "_shard": "." if resolution == "legacy" else resolution,
                "_path": image,
                "_source_line": line,
            })
    return records, warnings


def _post_training_test_candidates(records, size):
    """Escolhe por amostra, nunca por sessão, para cada execução ter sua reserva."""
    known = set()
    candidates = []
    for record in records:
        identifier = str(record.get("id") or Path(record.get("_path", "")).stem)
        if not identifier or identifier in known or "_source_line" not in record:
            continue
        known.add(identifier)
        candidates.append(record)
    return sorted(
        candidates,
        key=lambda record: hashlib.sha256(
            str(record.get("id") or record["_path"]).encode("utf-8")
        ).digest(),
    )[:size]


def exclude_cached_test_candidates(records, cache_root):
    """Não reserva como teste frames já guardados em caches contínuos."""
    from contextlib import closing

    cache_root = Path(cache_root)
    by_shard = defaultdict(set)
    for record in records:
        shard = record.get("_shard") or "."
        identifier = str(record.get("id") or Path(record["_path"]).stem)
        by_shard[shard].add(identifier)
    cached = set()
    for shard, identifiers in by_shard.items():
        folder = "_legacy" if shard == "." else shard
        if Path(folder).name != folder or folder in (".", ".."):
            raise ValueError("Invalid cache shard")
        for path in (cache_root / "continuous" / folder).glob("*.sqlite3"):
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                table = "archive_frames" if "archive_frames" in tables else "frames"
                if table not in tables:
                    continue
                ids = list(identifiers)
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    cached.update((shard, row[0]) for row in db.execute(
                        f"SELECT id FROM {table} WHERE id IN ({placeholders})", chunk
                    ))
    return [record for record in records if (
        record.get("_shard") or ".", str(record.get("id") or Path(record["_path"]).stem)
    ) not in cached]


def _source_image_is_referenced(root, image):
    """Não remove um arquivo ainda apontado por outro índice de treino."""
    image = Path(image).resolve()
    for shard in shard_roots(root):
        index = shard / INDEX_NAME
        try:
            with index.open(encoding="utf-8") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    relative = record.get("image") if isinstance(record, dict) else None
                    if isinstance(relative, str) and (shard / relative).resolve() == image:
                        return True
        except OSError:
            return True
    return False


def reserve_post_training_test(root, resolution, records, size):
    """Move imagens elegíveis para o conjunto de teste persistente da resolução."""
    if size <= 0:
        return {"requested": int(size), "reserved": 0, "skipped": 0}

    root = Path(root).resolve()
    target = post_training_test_root(root, resolution)
    selected = _post_training_test_candidates(records, size)
    if not selected:
        return {"requested": int(size), "reserved": 0, "skipped": 0, "root": str(target)}

    with index_lock(root):
        target.mkdir(parents=True, exist_ok=True)
        target_index = target / INDEX_NAME
        previous = target_index.read_text(encoding="utf-8").splitlines(keepends=True) if target_index.exists() else []
        existing_ids = set()
        for line in previous:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("id") is not None:
                existing_ids.add(str(record["id"]))
        movable = []
        for record in selected:
            identifier = str(record.get("id") or record["_path"].stem)
            source = Path(record["_path"]).resolve()
            relative = Path(record["image"])
            destination = (target / relative).resolve()
            if (
                not _inside(source, root)
                or not _inside(destination, target)
                or not source.is_file()
            ):
                continue
            if identifier in existing_ids:
                # Retoma uma reserva interrompida depois de publicar o índice de teste.
                if destination.is_file():
                    movable.append((record, source, destination, False))
                continue
            if destination.exists():
                continue
            movable.append((record, source, destination, True))

        copied = []
        try:
            for _record, source, destination, is_new in movable:
                if not is_new:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied.append(destination)
        except OSError:
            for destination in copied:
                destination.unlink(missing_ok=True)
            raise

        lines = previous + [
            record["_source_line"]
            for record, _source, _destination, is_new in movable if is_new
        ]
        _replace_index(target_index, lines)

        by_index = defaultdict(list)
        for record, _source, _destination, _is_new in movable:
            by_index[Path(record["_root"]) / INDEX_NAME].append(record)
        for index, batch in by_index.items():
            occurrences = Counter(record["_source_line"] for record in batch)
            kept = []
            with index.open(encoding="utf-8", newline="") as file:
                for line in file:
                    normalized = line.replace("\r\n", "\n")
                    if occurrences[normalized] > 0:
                        occurrences[normalized] -= 1
                    else:
                        kept.append(line)
            _replace_index(index, kept)

        for _record, source, _destination, _is_new in movable:
            if source.is_file() and not _source_image_is_referenced(root, source):
                source.unlink()

    return {
        "requested": int(size),
        "reserved": len(movable),
        "skipped": len(selected) - len(movable),
        "root": str(target),
    }


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


def trained_sample_ids(root, cache_root=None, confirmed=False):
    cache_root = Path(cache_root).expanduser().resolve() if cache_root else Path(root) / "cache"
    if confirmed:
        ids = set()
        for path in (cache_root / "continuous").glob("*/*.sqlite3"):
            shard = "." if path.parent.name == "_legacy" else path.parent.name
            uri = path.resolve().as_uri() + "?mode=ro"
            from contextlib import closing
            with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name='processed'").fetchone():
                    ids.update(f"{shard}/{row[0]}" for row in connection.execute("SELECT id FROM processed"))
        return ids
    cache = cache_root / "category"
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
