"""Agendamento por registros novos, sem iniciar treino nem acessar imagens."""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dataset.auto_train import AutoTrainer  # noqa: E402


def records(count, shard="1080x2400"):
    return [
        {"id": f"frame-{index}", "_shard": shard}
        for index in range(count)
    ]


class AutoTrainerTests(unittest.TestCase):
    def setUp(self):
        self.trainer = AutoTrainer(ROOT / "dataset", batch_size=10_000, pause_bot=False)

    def ready(self, samples, trained=()):
        with (
            patch("dataset.auto_train.snapshot_records", return_value=(samples, {})),
            patch("dataset.auto_train.trained_sample_ids", return_value=set(trained)),
        ):
            return self.trainer._ready()

    def test_waits_for_ten_thousand_new_records(self):
        self.assertFalse(self.ready(records(9_999)))
        self.assertTrue(self.ready(records(10_000)))

    def test_sums_resolutions_with_independent_ids(self):
        samples = records(6_000, "1080x2400") + records(4_000, "720x1600")
        self.assertTrue(self.ready(samples))

    def test_already_trained_records_do_not_complete_a_batch(self):
        samples = records(10_000)
        trained = {"1080x2400/frame-0"}
        self.assertFalse(self.ready(samples, trained))

        samples.append({"id": "new-frame", "_shard": "1080x2400"})
        self.assertTrue(self.ready(samples, trained))

    def test_trained_id_in_another_resolution_remains_new(self):
        self.assertTrue(self.ready(records(10_000), {"720x1600/frame-0"}))

    def test_duplicate_records_do_not_complete_a_batch(self):
        samples = records(9_999)
        self.assertFalse(self.ready(samples + samples[:100]))
        self.assertTrue(self.ready(samples + [{"id": "new-frame", "_shard": "1080x2400"}]))

    def test_legacy_record_uses_filename_when_id_is_missing(self):
        self.trainer.batch_size = 1
        sample = {"_path": Path("images") / "legacy-frame.jpg"}
        self.assertTrue(self.ready([sample]))
        self.assertFalse(self.ready([sample], {"./legacy-frame"}))

    def test_does_not_start_a_process_after_stop(self):
        self.trainer.stop()
        with patch("dataset.auto_train.subprocess.Popen") as start_process:
            self.trainer._train()
        start_process.assert_not_called()

    def test_stop_after_readiness_check_prevents_process_start(self):
        def ready_then_stop():
            self.trainer.stop()
            return True

        self.trainer.notify()
        with (
            patch.object(self.trainer, "_ready", side_effect=ready_then_stop),
            patch("dataset.auto_train.subprocess.Popen") as start_process,
        ):
            self.trainer._run()
        start_process.assert_not_called()

    def test_stop_terminates_and_waits_for_running_process(self):
        process = Mock()
        process.poll.return_value = None
        self.trainer._process = process

        self.trainer.stop()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)


class AutoTrainerPauseTests(unittest.TestCase):
    def wait_for_pause(self, trainer):
        deadline = time.monotonic() + 1
        interval = threading.Event()
        while time.monotonic() < deadline:
            if trainer.pause_requested:
                return True
            interval.wait(0.005)
        return trainer.pause_requested

    def start_worker(self, trainer):
        finished = threading.Event()
        errors = []

        def train():
            try:
                trainer._train()
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=train, daemon=True)
        worker.start()
        return worker, finished, errors

    def test_acknowledgement_starts_process_and_success_clears_pause(self):
        self.check_process_pause(returncode=0)

    def test_failed_process_clears_pause(self):
        self.check_process_pause(returncode=1)

    def test_pause_disabled_starts_without_acknowledgement(self):
        self.check_process_pause(returncode=0, pause_bot=False)

    def check_process_pause(self, returncode, pause_bot=True):
        trainer = AutoTrainer(ROOT / "dataset", pause_bot=pause_bot)
        process_started = threading.Event()
        process_finished = threading.Event()
        process = Mock()
        process.poll.return_value = returncode

        def wait_for_process(timeout=None):
            if not process_finished.wait(timeout=1):
                raise TimeoutError("O teste não liberou o processo simulado.")
            return returncode

        process.wait.side_effect = wait_for_process

        def spawn(*args, **kwargs):
            process_started.set()
            return process

        stop_wait = trainer._stop.wait

        def wait_without_backoff(timeout=None):
            return False if timeout == 60 else stop_wait(timeout)

        with (
            patch("dataset.auto_train.subprocess.Popen", side_effect=spawn) as start_process,
            patch("dataset.auto_train.log.start_process_output", return_value=None),
            patch.object(trainer._stop, "wait", side_effect=wait_without_backoff),
        ):
            worker, finished, errors = self.start_worker(trainer)
            try:
                if pause_bot:
                    self.assertTrue(self.wait_for_pause(trainer))
                    start_process.assert_not_called()
                    trainer.acknowledge_pause()
                self.assertTrue(process_started.wait(timeout=1))
                self.assertEqual(trainer.pause_requested, pause_bot)
                process_finished.set()
                self.assertTrue(finished.wait(timeout=1))
                self.assertFalse(trainer.pause_requested)
                start_process.assert_called_once()
                self.assertFalse(errors)
            finally:
                process_finished.set()
                trainer.stop()
                worker.join(timeout=1)
            self.assertFalse(worker.is_alive())

    def test_spawn_error_clears_pause(self):
        trainer = AutoTrainer(ROOT / "dataset", pause_bot=True)
        with patch(
            "dataset.auto_train.subprocess.Popen", side_effect=OSError("spawn failed"),
        ) as start_process:
            worker, finished, errors = self.start_worker(trainer)
            try:
                self.assertTrue(self.wait_for_pause(trainer))
                start_process.assert_not_called()
                trainer.acknowledge_pause()
                self.assertTrue(finished.wait(timeout=1))
                self.assertFalse(trainer.pause_requested)
                start_process.assert_called_once()
                self.assertTrue(all(isinstance(error, OSError) for error in errors))
            finally:
                trainer.stop()
                worker.join(timeout=1)
            self.assertFalse(worker.is_alive())

    def test_stop_before_acknowledgement_does_not_start_process(self):
        trainer = AutoTrainer(ROOT / "dataset", pause_bot=True)
        with patch("dataset.auto_train.subprocess.Popen") as start_process:
            worker, finished, errors = self.start_worker(trainer)
            try:
                self.assertTrue(self.wait_for_pause(trainer))
                start_process.assert_not_called()
                trainer.stop()
                self.assertTrue(finished.wait(timeout=1))
                self.assertFalse(trainer.pause_requested)
                start_process.assert_not_called()
                self.assertFalse(errors)
            finally:
                trainer.stop()
                worker.join(timeout=1)
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
