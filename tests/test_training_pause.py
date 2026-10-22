"""Pausa do loop e retomada com serviços simulados, sem captura nem treino."""

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import main  # noqa: E402
from core import state_machine  # noqa: E402
from dataset.recorder import DatasetRecorder  # noqa: E402


class PauseRequests:
    def __init__(self, states):
        self.states = iter(states)
        self.acknowledge_pause = Mock()

    @property
    def pause_requested(self):
        return next(self.states, False)


class TrainingPauseTests(unittest.TestCase):
    def setUp(self):
        self.frame = SimpleNamespace(shape=(1600, 720, 3))
        self.vision = Mock()
        self.vision.is_alive.return_value = True
        self.vision.get_fps.return_value = 0.0
        self.state = Mock()
        self.state.recorder.prepare_for_training.return_value = True
        self.state.frame_floor.return_value = 0.0
        self.state.summary.side_effect = lambda: {"estado": "FOOD"}
        self.actions = Mock()
        self.actions.is_busy.return_value = False

    def run_frames(self, frames, trainer, *, readings=(), times=(10.0,), panel=None):
        capture = Mock()
        capture.get_frame.side_effect = list(frames) + [(None, 0, 0.0)]
        capture.is_running.side_effect = [True] * len(frames) + [False]
        battery = Mock()
        battery.get.return_value = None
        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "SHOW_AI_VISION", False))
            stack.enter_context(patch.object(main, "VISION_FILTER_BY_STATE", False))
            stack.enter_context(patch.object(main.time, "monotonic", side_effect=times))
            read = stack.enter_context(patch.object(main, "read_detections", side_effect=readings))
            main.run_loop(
                capture, self.vision, Mock(), self.state, self.actions,
                battery, panel, auto_trainer=trainer,
            )
        return capture, read

    def test_pause_stops_detection_and_updates_but_keeps_status_responsive(self):
        trainer = PauseRequests([True, True])
        panel = Mock()
        _, read = self.run_frames(
            [(self.frame, 1, 9.0), (None, 1, 0.0)], trainer, panel=panel,
        )

        self.vision.set_frame.assert_not_called()
        self.state.update.assert_not_called()
        read.assert_not_called()
        self.vision.set_frame_floor.assert_called_once_with(float("inf"))
        self.assertEqual(panel.update.call_count, 2)
        self.assertEqual(panel.update.call_args.args[0]["estado"], "PAUSADO PARA TREINO")

    def test_acknowledges_only_after_action_and_dataset_queue_are_ready(self):
        trainer = PauseRequests([True, True, True])
        self.actions.is_busy.side_effect = [True, False, False]
        self.state.recorder.prepare_for_training.side_effect = [False, True]

        def acknowledge():
            self.assertEqual(self.actions.is_busy.call_count, 3)
            self.assertEqual(self.state.recorder.prepare_for_training.call_count, 2)

        trainer.acknowledge_pause.side_effect = acknowledge
        self.run_frames([(None, 0, 0.0)] * 3, trainer)

        trainer.acknowledge_pause.assert_called_once_with()
        self.state.update.assert_not_called()
        self.vision.set_frame.assert_not_called()

    def test_resume_discards_results_at_or_before_resume_floor(self):
        trainer = PauseRequests([True, False, False, False, False])
        detections = [{"category": "food"}]
        readings = [(detections, 0.0, self.frame, timestamp) for timestamp in (19.0, 20.0, 21.0, 22.0)]
        self.run_frames(
            [(self.frame, index, timestamp) for index, timestamp in enumerate((9.0, 19.0, 20.0, 21.0, 22.0), 1)],
            trainer, readings=readings, times=(10.0, 20.0),
        )

        self.state.resume_after_pause.assert_called_once_with(10.0)
        self.assertEqual(self.state.update.call_args_list, [
            call(detections, 0.0, self.frame, 21.0),
            call(detections, 0.0, self.frame, 22.0),
        ])
        self.assertTrue(all(
            invocation.args[0] >= 20.0
            for invocation in self.vision.set_frame_floor.call_args_list
        ))

    def test_resume_without_new_capture_still_restores_timers(self):
        trainer = PauseRequests([True, False, False])
        detections = [{"category": "box"}]
        self.run_frames(
            [(self.frame, 1, 9.0), (None, 1, 0.0), (self.frame, 2, 21.0)],
            trainer, readings=[(detections, 0.0, self.frame, 21.0)], times=(10.0, 20.0),
        )

        self.state.resume_after_pause.assert_called_once_with(10.0)
        self.state.update.assert_called_once_with(detections, 0.0, self.frame, 21.0)

    def test_dataset_preparation_waits_for_both_existing_and_flushed_tasks(self):
        recorder = DatasetRecorder(directory=ROOT / "dataset", auto_train=False)
        recorder._flush_pending = Mock()
        recorder.queue.put_nowait(object())

        self.assertFalse(recorder.prepare_for_training())
        recorder._flush_pending.assert_not_called()
        recorder.queue.get_nowait()
        recorder.queue.task_done()

        recorder._flush_pending.side_effect = lambda *_: recorder.queue.put_nowait(object())
        self.assertFalse(recorder.prepare_for_training())
        recorder.queue.get_nowait()
        recorder.queue.task_done()
        recorder._flush_pending.side_effect = None

        self.assertTrue(recorder.prepare_for_training())


class StateMachinePauseTests(unittest.TestCase):
    def test_resume_preserves_state_and_remaining_waits(self):
        actions = Mock()
        actions.last_finished_at = 96.0
        machine = state_machine.StateMachine(actions)
        machine.state = state_machine.FOOD
        machine.state_entered = 91.0
        machine.explore_anchor = 92.0
        machine.cycle_at = 80.0
        machine.up_food_wait_start = 94.0
        machine.last_action_time = 95.0
        machine.last_action_at = 95.0
        machine.action_counts["food"] = 3
        target = object()
        machine._food_target = target
        machine._frame = object()
        machine._frame_time = 99.0
        machine._detections = [{"category": "food"}]

        machine.resume_after_pause(50.0)

        self.assertEqual(machine.state, state_machine.FOOD)
        self.assertEqual(machine.state_entered, 141.0)
        self.assertEqual(machine.explore_anchor, 142.0)
        self.assertEqual(machine.cycle_at, 130.0)
        self.assertEqual(machine.up_food_wait_start, 144.0)
        self.assertEqual(machine.last_action_time, 95.0)
        self.assertEqual(machine.last_action_at, 95.0)
        self.assertEqual(actions.last_finished_at, 96.0)
        self.assertEqual(machine.action_counts["food"], 3)
        self.assertIs(machine._food_target, target)
        self.assertIsNone(machine._frame)
        self.assertIsNone(machine._frame_time)
        self.assertEqual(machine._detections, [])

    def test_resume_without_food_wait_preserves_none(self):
        machine = state_machine.StateMachine(Mock())

        machine.resume_after_pause(50.0)

        self.assertIsNone(machine.up_food_wait_start)


if __name__ == "__main__":
    unittest.main()
