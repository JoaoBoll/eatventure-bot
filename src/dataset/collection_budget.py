"""Inventário da coleta, sincronizado sob o mesmo bloqueio dos escritores e da limpeza."""

import json
from collections import Counter
from pathlib import Path

from dataset import layout


def bucket(record):
    if not layout.confirmed_training_sample(record):
        return "diagnostic"
    if not record.get("action"):
        return "negative"
    return (record["action"], record.get("target_category"))


class ShardInventory:
    def __init__(self):
        self.signature = None
        self.counts = Counter()
        self.seen = set()
        self.images = {}
        self.size = 0

    def add_image(self, path, size):
        if path not in self.images:
            self.images[path] = size
            self.size += size

    def add_record(self, record):
        key = bucket(record)
        self.counts[key] += 1
        if record.get("image_sha256") and key != "diagnostic":
            self.seen.add((key, record["image_sha256"]))


class CollectionBudget:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.shards = {}

    def refresh(self, shard):
        # Inclui pastas ainda sem índice (interrupção entre a imagem e o índice).
        data = self.root / layout.DATA_DIRNAME
        roots = {shard, self.root}
        if data.is_dir():
            roots.update(path for path in data.iterdir() if path.is_dir())
        for removed in self.shards.keys() - roots:
            del self.shards[removed]
        for path in roots:
            if path.resolve().is_relative_to(self.root):
                self._sync(path)
        return self.shards[shard]

    def _sync(self, path):
        index = layout.index_path(path)
        try:
            stat = index.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        except FileNotFoundError:
            signature = None
        inventory = self.shards.get(path)
        if inventory is not None and inventory.signature == signature:
            return
        previous = inventory.signature if inventory is not None else None
        append = previous and signature and previous[0] == signature[0] and previous[1] < signature[1]
        if not append:
            inventory = self.shards[path] = ShardInventory()
            for image in layout.images_dir(path).rglob("*"):
                if image.is_file() and image.resolve().is_relative_to(self.root):
                    inventory.add_image(image.resolve(), image.stat().st_size)
            if previous:
                (path / ".collection-pressure").unlink(missing_ok=True)
        if signature:
            with index.open("rb") as file:
                file.seek(previous[1] if append else 0)
                for line in file:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(record, dict) or not isinstance(record.get("image"), str):
                        continue
                    image = (path / record["image"]).resolve()
                    if not image.is_relative_to(self.root) or not image.is_file():
                        continue
                    inventory.add_image(image, image.stat().st_size)
                    inventory.add_record(record)
        inventory.signature = signature

    @property
    def size(self):
        return sum(inventory.size for inventory in self.shards.values())
