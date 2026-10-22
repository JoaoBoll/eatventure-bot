"""Preservação dos arquivos de coleta durante a limpeza dos lotes."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dataset import training_files  # noqa: E402
from dataset.layout import INDEX_NAME  # noqa: E402


class TrainingFilesTests(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base / "dataset"
        self.shard = self.root / "data" / "1080x2400"
        self.shard.mkdir(parents=True)

    def fake_image(self, name, shard=None):
        path = (shard or self.shard) / "images" / f"{name}.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("arquivo de coleta simulado", encoding="utf-8")
        return path

    def append_record(self, identifier, image, shard=None):
        shard = shard or self.shard
        shard.mkdir(parents=True, exist_ok=True)
        record = {
            "id": identifier,
            "image": Path(os.path.relpath(image, shard)).as_posix(),
        }
        with (shard / INDEX_NAME).open("a", encoding="utf-8", newline="") as file:
            file.write(json.dumps(record) + "\n")
        return record

    def read_records(self, shard=None):
        path = (shard or self.shard) / INDEX_NAME
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_training_lock_rejects_competing_holder_and_releases(self):
        with training_files.training_lock(self.root):
            with self.assertRaises(BlockingIOError):
                with training_files.training_lock(self.root):
                    self.fail("O segundo acesso adquiriu um lock já ocupado.")

        with training_files.training_lock(self.root):
            pass

    def test_cleanup_preserves_records_appended_after_snapshot(self):
        old_image = self.fake_image("old")
        self.append_record("old", old_image)
        records, _ = training_files.snapshot_records(self.root)
        new_image = self.fake_image("new")
        new_record = self.append_record("new", new_image)

        result = training_files.cleanup_records(self.root, records, {"old"})

        self.assertEqual(self.read_records(), [new_record])
        self.assertFalse(old_image.exists())
        self.assertTrue(new_image.exists())
        self.assertEqual(result["records_removed"], 1)
        self.assertEqual(result["images_removed"], 1)
        self.assertEqual(result["errors"], [])

    def test_cleanup_preserves_records_not_consumed_by_training(self):
        used_image = self.fake_image("used")
        kept_image = self.fake_image("kept")
        self.append_record("used", used_image)
        kept_record = self.append_record("kept", kept_image)
        records, _ = training_files.snapshot_records(self.root)

        result = training_files.cleanup_records(self.root, records, {"used"})

        self.assertEqual(self.read_records(), [kept_record])
        self.assertFalse(used_image.exists())
        self.assertTrue(kept_image.exists())
        self.assertEqual(result["records_removed"], 1)
        self.assertEqual(result["errors"], [])

    def test_cleanup_preserves_image_referenced_by_another_shard(self):
        shared_image = self.fake_image("shared")
        self.append_record("used", shared_image)
        records, _ = training_files.snapshot_records(self.root)
        other_shard = self.root / "data" / "720x1600"
        kept_record = self.append_record("kept", shared_image, other_shard)

        result = training_files.cleanup_records(self.root, records, {"used"})

        self.assertEqual(self.read_records(), [])
        self.assertEqual(self.read_records(other_shard), [kept_record])
        self.assertTrue(shared_image.exists())
        self.assertEqual(result["images_removed"], 0)
        self.assertEqual(result["errors"], [])

    def test_snapshot_rejects_image_paths_outside_dataset(self):
        outside_image = self.fake_image("outside", self.base / "outside")
        self.append_record("relative", outside_image)
        with (self.shard / INDEX_NAME).open("a", encoding="utf-8") as file:
            file.write(json.dumps({"id": "absolute", "image": str(outside_image)}) + "\n")

        records, warnings = training_files.snapshot_records(self.root)

        self.assertEqual(records, [])
        self.assertEqual(warnings["sem_imagem"], 2)
        self.assertTrue(outside_image.exists())

    def test_cleanup_rejects_image_and_index_paths_outside_dataset(self):
        outside_shard = self.base / "outside"
        outside_image = self.fake_image("outside", outside_shard)
        inside_image = self.fake_image("inside")
        inside_record = self.append_record("outside_image", outside_image)
        outside_record = self.append_record("outside_index", inside_image, outside_shard)
        records = [
            {
                **record,
                "_root": shard,
                "_path": image,
                "_source_line": json.dumps(record) + "\n",
            }
            for record, shard, image in (
                (inside_record, self.shard, outside_image),
                (outside_record, outside_shard, inside_image),
            )
        ]

        result = training_files.cleanup_records(
            self.root, records, {"outside_image", "outside_index"},
        )

        self.assertEqual(self.read_records(), [inside_record])
        self.assertEqual(self.read_records(outside_shard), [outside_record])
        self.assertTrue(inside_image.exists())
        self.assertTrue(outside_image.exists())
        self.assertEqual(result["records_removed"], 0)
        self.assertEqual(result["images_removed"], 0)

    def test_resume_cleanup_recovers_interruption_after_index_replacement(self):
        image = self.fake_image("used")
        self.append_record("used", image)
        records, _ = training_files.snapshot_records(self.root)
        original_unlink = Path.unlink

        def interrupt_image_removal(path, *args, **kwargs):
            if path.resolve() == image.resolve():
                raise KeyboardInterrupt("interrupção simulada")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupt_image_removal):
            with self.assertRaises(KeyboardInterrupt):
                training_files.cleanup_records(self.root, records, {"used"})

        self.assertEqual(self.read_records(), [])
        self.assertTrue(image.exists())

        training_files.resume_cleanup(self.root)

        self.assertEqual(self.read_records(), [])
        self.assertFalse(image.exists())

    def test_disk_usage_reflects_space_freed_after_training(self):
        old_image = self.fake_image("old")
        new_image = self.fake_image("new")
        self.append_record("old", old_image)
        self.append_record("new", new_image)
        records, _ = training_files.snapshot_records(self.root)
        self.assertEqual(
            training_files.raw_dataset_bytes(self.root),
            old_image.stat().st_size + new_image.stat().st_size,
        )

        training_files.cleanup_records(self.root, records, {"old"})

        self.assertEqual(training_files.raw_dataset_bytes(self.root), new_image.stat().st_size)


if __name__ == "__main__":
    unittest.main()
