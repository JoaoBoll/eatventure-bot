"""Isolamento e validação dos overrides locais usados pelo painel."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.device_profiles import ProfileStore, apply_environment_profile, template_key, validate_profile


class DeviceProfilesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProfileStore(self.root / "profiles")

    def test_missing_profile_has_independent_defaults(self):
        profile = self.store.load("phone-a")
        profile["run"]["save_log"] = True

        self.assertEqual(self.store.load("phone-a")["run"], {
            "ai_collect": False, "layout_only": False, "save_log": False,
        })
        self.assertFalse(self.store.root.exists())

    def test_profiles_are_isolated_and_ids_never_form_paths(self):
        first = "192.168.1.4:39015"
        second = "../../outside"
        self.store.save(first, {"settings": {"shape_threshold": 0.91}})
        self.store.save(second, {"settings": {"shape_threshold": 0.82}})

        self.assertEqual(self.store.path_for(second).parent, self.store.root)
        self.assertEqual(self.store.load(first)["settings"], {"shape_threshold": 0.91})
        self.assertEqual(self.store.load(second)["settings"], {"shape_threshold": 0.82})
        self.store.reset(first)
        self.assertEqual([profile["device_id"] for profile in self.store.list_profiles()], [second])

    def test_save_failure_preserves_previous_profile(self):
        original = self.store.save("phone", {"name": "Antes"})
        with patch("core.device_profiles.os.replace", side_effect=OSError("sem espaço")):
            with self.assertRaises(OSError):
                self.store.save("phone", {"name": "Depois"})

        self.assertEqual(self.store.load("phone"), original)
        self.assertEqual(list(self.store.root.iterdir()), [self.store.path_for("phone")])

    def test_rejects_nonfinite_out_of_range_and_wrong_types(self):
        for settings in (
            {"shape_threshold": float("nan")},
            {"shape_threshold": float("inf")},
            {"shape_threshold": -0.1},
            {"capture_max_fps": True},
            {"capture_max_fps": 60.5},
            {"learn_resolution_templates": "false"},
            {"adb_path": "/bin/sh"},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.store.save("phone", {"settings": settings})

    def test_rejects_unsafe_item_paths(self):
        for name in ("../outside.png", "food/../../outside.png", "food\\1.png", "food/a:1.png", "food/.hidden.png", "food/code.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.save("phone", {"item_thresholds": {name: 0.8}})

    def test_accepts_item_and_category_thresholds(self):
        profile = self.store.save("phone", {
            "category_thresholds": {"food": 0.9},
            "item_thresholds": {"food/1.png": 0.95},
        })
        self.assertEqual(self.store.load("phone"), profile)

    def test_template_key_keeps_the_identity_across_resolutions(self):
        self.assertEqual(template_key("food", "1080x2400_1.png"), "food/1.png")
        self.assertEqual(template_key("food", "720x1600_1.png"), "food/1.png")
        profile = validate_profile("phone", {"item_thresholds": {"food/1080x2400_1.png": 0.9}})
        self.assertEqual(profile["item_thresholds"], {"food/1.png": 0.9})

    def test_rejects_profile_for_another_device(self):
        with self.assertRaises(ValueError):
            self.store.save("phone-a", {"device_id": "phone-b"})
        path = self.store.path_for("phone-a")
        path.parent.mkdir()
        path.write_text(json.dumps(validate_profile("phone-b", {})), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load("phone-a")

    def test_oversized_file_is_rejected(self):
        path = self.store.path_for("phone")
        path.parent.mkdir()
        path.write_text(" " * (256 * 1024 + 1), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load("phone")

    def test_environment_profile_applies_only_selected_overrides(self):
        self.store.save("phone-a", {
            "settings": {"shape_threshold": 0.92},
            "category_thresholds": {"food": 0.93},
            "item_thresholds": {"food/1.png": 0.94},
        })
        defaults = {"SHAPE_THRESHOLD": 0.8, "COLOR_THRESHOLD": 0.85, "CATEGORY_THRESHOLDS": {"box": 0.95}}
        environment = {
            "EATVENTURE_PROFILE": str(self.store.path_for("phone-a")),
            "EATVENTURE_DEVICE_ID": "phone-a",
            "EATVENTURE_DEVICE_SERIAL": "192.168.1.4:39015",
        }
        with patch.dict(os.environ, environment, clear=True):
            apply_environment_profile(defaults)

        self.assertEqual(defaults["SHAPE_THRESHOLD"], 0.92)
        self.assertEqual(defaults["COLOR_THRESHOLD"], 0.85)
        self.assertEqual(defaults["CATEGORY_THRESHOLDS"], {"box": 0.95, "food": 0.93})
        self.assertEqual(defaults["ITEM_THRESHOLDS"], {"food/1.png": 0.94})
        self.assertEqual(defaults["DEVICE_PROFILE_ID"], "phone-a")
        self.assertEqual(defaults["DEVICE_SERIAL"], "192.168.1.4:39015")
        self.assertFalse(defaults["SHOW_AI_VISION"])
        self.assertFalse(defaults["SHOW_SCRCPY"])
        self.assertFalse(defaults["STATUS_PANEL"])

    def test_run_flag_controls_collection_without_changing_source_defaults(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.store.save("phone-a", {"run": {"ai_collect": enabled}})
                namespace = {"DATASET_SAVE": not enabled}
                with patch.dict(os.environ, {
                    "EATVENTURE_PROFILE": str(self.store.path_for("phone-a")),
                }, clear=True):
                    apply_environment_profile(namespace)
                self.assertEqual(namespace["DATASET_SAVE"], enabled)

    def test_wrong_environment_identity_leaves_defaults_untouched(self):
        self.store.save("phone-a", {"settings": {"shape_threshold": 0.92}})
        defaults = {"SHAPE_THRESHOLD": 0.8}
        with patch.dict(os.environ, {
            "EATVENTURE_PROFILE": str(self.store.path_for("phone-a")),
            "EATVENTURE_DEVICE_ID": "phone-b",
        }, clear=True):
            with self.assertRaises(ValueError):
                apply_environment_profile(defaults)
        self.assertEqual(defaults["SHAPE_THRESHOLD"], 0.8)

    def test_device_training_and_learning_directories_are_isolated(self):
        paths = []
        for device_id in ("phone-a", "phone-b"):
            self.store.save(device_id, {})
            namespace = {}
            with patch.dict(os.environ, {
                "EATVENTURE_PROFILE": str(self.store.path_for(device_id)),
                "EATVENTURE_DEVICE_ID": device_id,
                "EATVENTURE_STATE_ROOT": str(self.root / "state"),
            }, clear=True):
                apply_environment_profile(namespace)
            self.assertEqual(namespace["DEVICE_TEMPLATE_DIR"].name, "templates")
            self.assertEqual(namespace["AI_DEVICE_MODEL_DIR"].name, "models")
            self.assertEqual(namespace["DATASET_DIR"].name, "dataset")
            self.assertEqual(namespace["LOG_DIR"].name, "logs")
            self.assertEqual(namespace["DEVICE_TEMPLATE_DIR"].parent, namespace["DATASET_DIR"].parent)
            paths.append(namespace["DATASET_DIR"])
        self.assertNotEqual(paths[0], paths[1])
        self.assertFalse((self.root / "state").exists())


if __name__ == "__main__":
    unittest.main()
