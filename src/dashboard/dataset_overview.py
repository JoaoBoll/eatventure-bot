"""Resumo dos dados locais sem abrir imagens ou carregar modelos."""

from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from dataset.layout import INDEX_NAME, record_resolution, shard_roots


def _size(path):
    total = 0
    for folder, directories, files in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(folder) / name).is_symlink()]
        for name in files:
            file = Path(folder) / name
            try:
                if not file.is_symlink():
                    total += file.stat().st_size
            except OSError:
                continue
    return total


def overview(root):
    root = Path(root)
    dataset = root / "dataset"
    groups = {}
    seen = set()
    incomplete = False
    indexes = [(shard, False) for shard in shard_roots(dataset)]
    test_root = dataset / "post_training_test"
    if test_root.is_dir():
        indexes.extend((folder, True) for folder in test_root.iterdir() if folder.is_dir() and not folder.is_symlink())

    def group(resolution):
        return groups.setdefault(resolution, {"samples": 0, "test_samples": 0, "categories": Counter(), "archive_frames": 0, "archive_categories": Counter()})

    for shard, is_test in indexes:
        shard_id = "." if shard == dataset else shard.name
        try:
            with (shard / INDEX_NAME).open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict) or not isinstance(record.get("image"), str):
                            continue
                        image = (shard / record["image"]).resolve()
                        if not image.is_relative_to(shard.resolve()) or not image.is_file():
                            continue
                        resolution = record_resolution({**record, "_shard": shard_id})
                        identifier = str(record.get("id") or image.stem)
                        key = (resolution, identifier)
                        if key in seen:
                            continue
                        seen.add(key)
                        item = group(resolution)
                        item["test_samples" if is_test else "samples"] += 1
                        for box in record.get("boxes") or []:
                            if isinstance(box, dict) and isinstance(box.get("category"), str):
                                item["categories"][box["category"]] += 1
                    except (ValueError, TypeError, OSError):
                        continue
        except OSError:
            incomplete = True
            continue

    archive_root = dataset / "cache" / "continuous"
    archive_seen = set()
    for path in archive_root.glob("*/*.sqlite3"):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        resolution = "legacy" if path.parent.name == "_legacy" else path.parent.name
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                frames = "archive_frames" if "archive_frames" in tables else "frames"
                crops = "archive_crops" if "archive_crops" in tables else "crops"
                if frames not in tables or crops not in tables:
                    continue
                item = group(resolution)
                for (identifier,) in db.execute(f"SELECT id FROM {frames}"):
                    key = (resolution, str(identifier))
                    if key not in archive_seen:
                        archive_seen.add(key)
                        item["archive_frames"] += 1
                    seen.add(key)
                item["archive_categories"].update(dict(db.execute(f"SELECT label, COUNT(*) FROM {crops} GROUP BY label")))
        except (OSError, sqlite3.Error):
            incomplete = True
            continue

    storage = {"data": _size(dataset / "data"), "test": _size(test_root), "cache": _size(dataset / "cache"), "models": _size(root / "IA" / "models")}
    dataset_size = _size(dataset)
    storage["other"] = max(0, dataset_size - storage["data"] - storage["test"] - storage["cache"])
    storage["total"] = dataset_size + storage["models"]
    return {"samples": len(seen), "incomplete": incomplete, "resolutions": [{"resolution": key, **value} for key, value in sorted(groups.items())], "storage": storage}
