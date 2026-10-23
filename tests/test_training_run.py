import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
for candidate in (ROOT, ROOT / "src", ROOT / "IA"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import train_ai
from training_run import TrainingRun, compact_record, record_key


def sample(index, root):
    return {
        "id": str(index),
        "_shard": "1080x2400",
        "_root": root,
        "_path": root / f"{index}.png",
        "_source_line": f"sample-{index}\n",
        "frame_width": 1080,
        "frame_height": 2400,
        "session": f"session-{index}",
        "phash": f"hash-{index}",
        "action": "click_food",
        "action_kind": "click",
        "state": "NORMAL",
        "outcome": "changed",
        "boxes": [{"category": "food", "width": 32, "height": 32}],
    }


class AutomaticThresholdTests(unittest.TestCase):
    def test_resolution_argument_filters_manual_training(self):
        root = Path("unused-dataset")
        args = train_ai.parse_args([
            "--dataset-root", str(root), "--resolution", "1080x2400",
        ])
        records = [
            sample(1, root),
            {**sample(2, root), "_shard": "720x1600"},
        ]
        with (
            patch("dataset.training_files.training_lock", return_value=nullcontext()),
            patch("dataset.training_files.snapshot_records", return_value=(records, {})),
            patch("train_ai._train_snapshot", return_value=0) as train_snapshot,
        ):
            self.assertEqual(train_ai.train(args), 0)

        trained_records = train_snapshot.call_args.args[2]
        self.assertEqual([record["_shard"] for record in trained_records], ["1080x2400"])

    def test_only_ten_thousand_new_records_start_training(self):
        root = Path("unused-dataset")
        args = train_ai.parse_args(["--automatic", "--dataset-root", str(root)])
        for count in (9999, 10000):
            with self.subTest(count=count):
                records = [sample(index, root) for index in range(count)]
                with (
                    patch("dataset.training_files.training_lock", return_value=nullcontext()),
                    patch("dataset.training_files.snapshot_records", return_value=(records, {})),
                    patch("training_run.trained_sample_ids", return_value=set()),
                    patch("train_ai._train_snapshot", return_value=0) as train_snapshot,
                ):
                    self.assertEqual(train_ai.train(args), 0)
                if count == 9999:
                    train_snapshot.assert_not_called()
                else:
                    self.assertEqual(len(train_snapshot.call_args.args[2]), 10000)

    def test_records_already_fitted_do_not_complete_the_threshold(self):
        root = Path("unused-dataset")
        args = train_ai.parse_args(["--automatic", "--dataset-root", str(root)])
        records = [sample(index, root) for index in range(10000)]
        with (
            patch("dataset.training_files.training_lock", return_value=nullcontext()),
            patch("dataset.training_files.snapshot_records", return_value=(records, {})),
            patch("training_run.trained_sample_ids", return_value={record_key(records[0])}),
            patch("train_ai._train_snapshot") as train_snapshot,
        ):
            self.assertEqual(train_ai.train(args), 0)
        train_snapshot.assert_not_called()


class TrainingReportTests(unittest.TestCase):
    def test_each_run_preserves_previous_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = train_ai.parse_args([])
            first = TrainingRun(root, args)
            first.data["final_fit"] = {"frames": 10000, "rows": 25000}
            first.finish("success")
            original = first.path.read_text(encoding="utf-8")
            second = TrainingRun(root, args)
            second.finish("failed")

            self.assertNotEqual(first.id, second.id)
            self.assertEqual(first.path.read_text(encoding="utf-8"), original)
            self.assertEqual(len(list((root / "training_reports").glob("*.json"))), 2)
            report = json.loads(original)
            self.assertEqual(report["final_fit"]["frames"], 10000)
            self.assertIsNotNone(report["finished_at_utc"])
            self.assertGreaterEqual(report["duration_seconds"], 0)

    def test_final_fit_includes_validation_and_replay_before_cleanup(self):
        self._check_final_fit(filtered=False)

    def test_outcome_filter_preserves_unselected_raw_replay(self):
        self._check_final_fit(filtered=True)

    def _check_final_fit(self, filtered):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = train_ai.parse_args([
                "--dataset-root", str(root / "dataset"),
                "--model-out", str(root / "IA" / "model.joblib"),
            ])
            records = [sample(index, root) for index in range(20)]
            current = records[:15]
            for record in records[15:]:
                record["outcome"] = "unchanged"
            if filtered:
                args.outcome = ["changed"]
            cleanup_batch = current if filtered else records
            metadata = {record_key(record): compact_record(record) for record in records}
            owners = np.asarray([record_key(record) for record in records])
            X = np.arange(40, dtype=np.float32).reshape(20, 2)
            y = np.asarray(["food", "background"] * 10)
            cache = MagicMock()
            extra = {"records": metadata, "caches": {"1080x2400": cache}}
            evaluator, final_model = MagicMock(), MagicMock()
            evaluator.predict.side_effect = lambda rows: np.asarray(["food"] * len(rows))
            final_model.classes_ = ["background", "food"]
            run = TrainingRun(root / "IA", args)
            events = []

            def publish(execution, model, model_metadata, model_out):
                self.assertIs(model, final_model)
                self.assertEqual(model_metadata["final_fit"]["frames"], 20)
                events.append("model_saved")
                return {"model": "immutable/model.joblib", "pointer": "model.current.json"}

            def activate(execution):
                self.assertEqual(json.loads(execution.path.read_text(encoding="utf-8"))["status"], "publishing")
                events.append("pointer_saved")

            def cleanup(dataset_root, snapshot, consumed_ids):
                report = json.loads(run.path.read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "success")
                self.assertTrue(report["model_activated"])
                self.assertIn("pointer_saved", events)
                self.assertEqual(len(snapshot), len(cleanup_batch))
                self.assertEqual(consumed_ids, {record["id"] for record in cleanup_batch})
                events.append("cleanup")
                return {
                    "records_removed": len(cleanup_batch),
                    "images_removed": len(cleanup_batch), "errors": [],
                }

            with (
                patch("train_ai.check_dependencies"),
                patch("train_ai.category_dataset", return_value=(X, y, owners, extra)),
                patch("train_ai.build_classifier", side_effect=[evaluator, final_model]),
                patch("train_ai.report", return_value=0.5),
                patch("sklearn.metrics.classification_report", return_value={}),
                patch("training_run.publish_model", side_effect=publish),
                patch("training_run.activate_model", side_effect=activate),
                patch("dataset.training_files.cleanup_records", side_effect=cleanup),
            ):
                result = train_ai._fit_snapshot(args, root / "dataset", current, records, {}, run)

            self.assertEqual(result, 0)
            self.assertEqual(len(evaluator.fit.call_args.args[0]), 16)
            self.assertEqual(len(final_model.fit.call_args.args[0]), 20)
            self.assertEqual(events, ["model_saved", "pointer_saved", "cleanup"])
            report = json.loads(run.path.read_text(encoding="utf-8"))
            self.assertEqual(report["evaluation"]["validation"]["frames"], 4)
            self.assertEqual(report["final_fit"]["frames"], 20)
            self.assertEqual(report["current_batch"]["used_frames"], 15)
            self.assertEqual(report["replay"]["frames"], 5)
            self.assertEqual(report["cleanup"]["replay_raw_frames"], 0 if filtered else 5)
            cache.mark_trained.assert_called_once()


if __name__ == "__main__":
    unittest.main()
