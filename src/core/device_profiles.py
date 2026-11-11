"""Preferências locais aplicadas somente ao processo do dispositivo escolhido."""

import hashlib
from core.i18n import localized, t
import json
import math
import os
from pathlib import Path
import re
import tempfile
from collections.abc import Mapping
from core.block_regions import validate_regions


MAX_PROFILE_BYTES = 256 * 1024
MAX_CATEGORY_THRESHOLDS = 128
MAX_ITEM_THRESHOLDS = 2000
_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_PROFILE_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")
_SOURCE_PREFIX = re.compile(r"^(\d+x\d+)_(.+)$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_RUN_DEFAULTS = {
    "ai_collect": False, "layout_only": False, "save_log": False,
    "detector_mode": "templates", "ai_model": "auto", "ai_source": "templates",
    "ai_min_confidence": 0.60,
}


def _spec(config, label, group, kind, minimum=None, maximum=None, step=None):
    field = {
        "config": config, "label": label, "group": group, "type": kind,
        "label_key": label.key, "group_key": group.key,
    }
    if minimum is not None:
        field.update(min=minimum, max=maximum, step=step)
    return field


SETTING_SPECS = {
    "shape_threshold": _spec("SHAPE_THRESHOLD", t("messages.shape_threshold"), t("messages.detection"), "float", 0.0, 1.0, 0.01),
    "color_threshold": _spec("COLOR_THRESHOLD", t("messages.color_threshold"), t("messages.detection"), "float", 0.0, 1.0, 0.01),
    "max_detection_age": _spec("MAX_DETECTION_AGE", t("messages.detection_validity"), t("messages.detection"), "float", 0.1, 60.0, 0.1),
    "vision_filter_by_state": _spec("VISION_FILTER_BY_STATE", t("messages.filter_by_game_state"), t("messages.detection"), "bool"),
    "vision_interval": _spec("VISION_INTERVAL", t("messages.detection_interval"), t("messages.detection"), "float", 0.01, 10.0, 0.01),
    "capture_max_fps": _spec("CAPTURE_MAX_FPS", t("messages.fps_limit"), t("messages.capture"), "int", 1, 120, 1),
    "learn_resolution_templates": _spec("LEARN_RESOLUTION_TEMPLATES", t("messages.learn_resolution_templates"), t("messages.templates"), "bool"),
    "template_refresh_interval": _spec("TEMPLATE_REFRESH_INTERVAL", t("messages.learning_interval"), t("messages.templates"), "float", 0.1, 3600.0, 0.1),
    "templates_watch_interval": _spec("TEMPLATES_WATCH_INTERVAL", t("messages.check_for_new_templates"), t("messages.templates"), "float", 0.0, 3600.0, 0.5),
    "dataset_jpeg_quality": _spec("DATASET_JPEG_QUALITY", t("messages.jpeg_quality"), t("messages.collection_training"), "int", 1, 100, 1),
    "dataset_max_disk_mb": _spec("DATASET_MAX_DISK_MB", t("messages.dataset_size_limit"), t("messages.collection_training"), "int", 0, 10000000, 1),
    "ai_auto_train": _spec("AI_AUTO_TRAIN", t("messages.automatic_training"), t("messages.collection_training"), "bool"),
    "ai_train_batch_size": _spec("AI_TRAIN_BATCH_SIZE", t("messages.training_samples"), t("messages.collection_training"), "int", 1, 1000000, 1),
    "ai_train_jobs": _spec("AI_TRAIN_JOBS", t("messages.training_processes"), t("messages.collection_training"), "int", 1, 64, 1),
    "ai_pause_bot_while_training": _spec("AI_PAUSE_BOT_WHILE_TRAINING", t("messages.pause_during_training"), t("messages.collection_training"), "bool"),
    "action_cooldown": _spec("ACTION_COOLDOWN", t("messages.action_interval"), t("messages.actions"), "float", 0.0, 60.0, 0.1),
    "action_settle": _spec("ACTION_SETTLE", t("messages.wait_after_action"), t("messages.actions"), "float", 0.0, 60.0, 0.1),
    "swipe_x": _spec("SWIPE_X", t("messages.swipe_x"), t("messages.actions"), "int", 0, 1080, 1),
    "swipe_y": _spec("SWIPE_Y", t("messages.swipe_y"), t("messages.actions"), "int", 0, 2400, 1),
    "swipe_distance": _spec("SWIPE_DISTANCE", t("messages.swipe_distance"), t("messages.actions"), "int", 1, 2400, 1),
    "swipe_duration_ms": _spec("SWIPE_DURATION_MS", t("messages.swipe_duration"), t("messages.actions"), "int", 1, 5000, 1),
    "swipe_waiting_time": _spec("SWIPE_WAITING_TIME", t("messages.wait_after_swipe"), t("messages.actions"), "float", 0.0, 60.0, 0.1),
    "upgrade_item_clicks": _spec("UPGRADE_ITEM_CLICKS", t("messages.upgrade_clicks"), t("messages.actions"), "int", 1, 100, 1),
    "upgrade_food_press": _spec("UPGRADE_FOOD_PRESS", t("messages.food_upgrade_duration"), t("messages.actions"), "float", 0.1, 30.0, 0.1),
    "exploration_delay": _spec("EXPLORATION_DELAY", t("messages.exploration_delay"), t("messages.actions"), "float", 0.0, 300.0, 0.5),
    "exploration_delay_after_action": _spec("EXPLORATION_DELAY_AFTER_ACTION", t("messages.exploration_action_delay"), t("messages.actions"), "float", 0.0, 300.0, 0.5),
}


def settings_schema(defaults: Mapping):
    return [
        {"key": key, **localized(spec), "default": defaults[spec["config"]]}
        for key, spec in SETTING_SPECS.items()
    ]


def _device_id(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or not value.isprintable()
        or any(character.isspace() for character in value)
    ):
        raise ValueError(t("messages.invalid_device_id"))
    return value


def _mapping(value, label, maximum):
    if not isinstance(value, dict) or len(value) > maximum:
        raise ValueError(t("messages.invalid_object", label=label))
    return value


def _number(value, label, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(t("messages.number_required", label=label))
    if integer and not isinstance(value, int):
        raise ValueError(t("messages.integer_required", label=label))
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(t("messages.number_out_of_range", label=label, minimum=minimum, maximum=maximum))
    return int(value) if integer else float(value)


def _item_key(value):
    if not isinstance(value, str) or len(value) > 240 or value.count("/") != 1:
        raise ValueError(t("messages.invalid_template"))
    category, filename = value.split("/")
    if (
        not _CATEGORY.fullmatch(category)
        or not filename
        or filename.startswith(".")
        or filename != filename.strip()
        or not filename.isprintable()
        or any(character in filename for character in '\\:*?"<>|')
        or Path(filename).suffix.lower() not in _IMAGE_SUFFIXES
    ):
        raise ValueError(t("messages.invalid_template"))
    return value


def template_key(category, name):
    if not isinstance(name, str):
        raise ValueError(t("messages.invalid_template_name"))
    source = _SOURCE_PREFIX.match(name)
    base_name = source.group(2) if source else name
    return _item_key(f"{category}/{base_name}")


def validate_profile(device_id, data):
    device_id = _device_id(device_id)
    data = _mapping(data, t("messages.profile"), 7)
    unknown = data.keys() - {"device_id", "name", "settings", "category_thresholds", "item_thresholds", "block_regions", "run"}
    if unknown:
        raise ValueError(t("messages.unknown_profile_fields"))
    if data.get("device_id", device_id) != device_id:
        raise ValueError(t("messages.profile_device_mismatch"))
    name = data.get("name", "")
    if not isinstance(name, str) or len(name) > 128 or (name and not name.isprintable()):
        raise ValueError(t("messages.invalid_device_name"))

    settings = {}
    for key, value in _mapping(data.get("settings", {}), t("messages.settings"), len(SETTING_SPECS)).items():
        if key not in SETTING_SPECS:
            raise ValueError(t("messages.unknown_setting", key=key))
        spec = SETTING_SPECS[key]
        if spec["type"] == "bool":
            if not isinstance(value, bool):
                raise ValueError(t("messages.boolean_required", value_1=spec['label']))
            settings[key] = value
        else:
            settings[key] = _number(value, spec["label"], spec["min"], spec["max"], integer=spec["type"] == "int")

    categories = {}
    for key, value in _mapping(data.get("category_thresholds", {}), t("messages.category_thresholds"), MAX_CATEGORY_THRESHOLDS).items():
        if not isinstance(key, str) or not _CATEGORY.fullmatch(key):
            raise ValueError(t("messages.invalid_category"))
        categories[key] = _number(value, t("messages.threshold_for", key=key), 0.0, 1.0)

    items = {}
    for key, value in _mapping(data.get("item_thresholds", {}), t("messages.item_thresholds"), MAX_ITEM_THRESHOLDS).items():
        category, filename = _item_key(key).split("/")
        canonical = template_key(category, filename)
        if canonical in items:
            raise ValueError(t("messages.duplicate_item_thresholds"))
        items[canonical] = _number(value, t("messages.threshold_for", key=key), 0.0, 1.0)

    blocks = validate_regions(data.get("block_regions", []))

    run = dict(_RUN_DEFAULTS)
    for key, value in _mapping(data.get("run", {}), t("messages.run_options"), len(_RUN_DEFAULTS)).items():
        if key not in run:
            raise ValueError(t("messages.invalid_run_option"))
        if key in {"ai_collect", "layout_only", "save_log"}:
            if not isinstance(value, bool):
                raise ValueError(t("messages.invalid_run_option"))
        elif key == "detector_mode":
            if value not in ("templates", "ai"):
                raise ValueError(t("messages.detector_choice_required"))
        elif key == "ai_source":
            if value not in ("templates", "proposer"):
                raise ValueError(t("messages.invalid_ai_source"))
        elif key == "ai_model":
            if not isinstance(value, str) or not (
                value in {"auto", "IA/model.joblib"}
                or re.fullmatch(r"IA/models/[1-9]\d*x[1-9]\d*/category\.joblib", value)
            ):
                raise ValueError(t("messages.invalid_model"))
        elif key == "ai_min_confidence":
            value = _number(value, t("messages.ai_min_confidence"), 0.0, 1.0)
        run[key] = value
    if run["detector_mode"] == "ai" and run["layout_only"]:
        raise ValueError(t("messages.layout_requires_templates"))
    return {
        "device_id": device_id,
        "name": name.strip(),
        "settings": settings,
        "category_thresholds": categories,
        "item_thresholds": items,
        "block_regions": blocks,
        "run": run,
    }


def _read_profile(path):
    if path.is_symlink():
        raise ValueError(t("messages.profile_symlink_forbidden"))
    with path.open("rb") as file:
        raw = file.read(MAX_PROFILE_BYTES + 1)
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError(t("messages.profile_size_limit"))
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError(t("messages.invalid_profile_file")) from error
    if not isinstance(data, dict) or "device_id" not in data:
        raise ValueError(t("messages.profile_device_required"))
    return validate_profile(data["device_id"], data)


class ProfileStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path_for(self, device_id):
        digest = hashlib.sha256(_device_id(device_id).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def load(self, device_id):
        path = self.path_for(device_id)
        try:
            profile = _read_profile(path)
        except FileNotFoundError:
            return validate_profile(device_id, {})
        if profile["device_id"] != device_id:
            raise ValueError(t("messages.saved_profile_mismatch"))
        return profile

    def save(self, device_id, data):
        profile = validate_profile(device_id, data)
        raw = (json.dumps(profile, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_PROFILE_BYTES:
            raise ValueError(t("messages.profile_size_limit"))
        path = self.path_for(device_id)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.root, prefix=".profile-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return profile

    def reset(self, device_id):
        self.path_for(device_id).unlink(missing_ok=True)
        return validate_profile(device_id, {})

    def list_profiles(self):
        if not self.root.exists():
            return []
        profiles = []
        for path in self.root.glob("*.json"):
            if not _PROFILE_NAME.fullmatch(path.name):
                continue
            try:
                profile = _read_profile(path)
                if self.path_for(profile["device_id"]) == path:
                    profiles.append(profile)
            except (OSError, ValueError):
                continue
        return sorted(profiles, key=lambda profile: (profile["name"].casefold(), profile["device_id"]))


def apply_environment_profile(namespace):
    namespace.setdefault("ITEM_THRESHOLDS", {})
    namespace.setdefault("BLOCK_REGIONS", [])
    namespace.setdefault("DEVICE_PROFILE_ID", None)
    namespace.setdefault("DEVICE_TEMPLATE_DIR", None)
    profile_path = os.environ.get("EATVENTURE_PROFILE")
    if not profile_path:
        return
    profile = _read_profile(Path(profile_path))
    expected_id = os.environ.get("EATVENTURE_DEVICE_ID")
    if expected_id is not None and expected_id != profile["device_id"]:
        raise ValueError(t("messages.runtime_profile_mismatch"))
    namespace.update({SETTING_SPECS[key]["config"]: value for key, value in profile["settings"].items()})
    namespace["CATEGORY_THRESHOLDS"] = {**namespace.get("CATEGORY_THRESHOLDS", {}), **profile["category_thresholds"]}
    namespace["ITEM_THRESHOLDS"] = dict(profile["item_thresholds"])
    namespace["BLOCK_REGIONS"] = [tuple(region) for region in profile["block_regions"]]
    namespace["DATASET_SAVE"] = profile["run"]["ai_collect"]
    namespace.update(
        DETECTOR_MODE=profile["run"]["detector_mode"],
        AI_MODEL=profile["run"]["ai_model"],
        AI_SOURCE=profile["run"]["ai_source"],
        AI_MIN_CONFIDENCE=profile["run"]["ai_min_confidence"],
    )
    namespace["DEVICE_PROFILE_ID"] = profile["device_id"]
    serial = os.environ.get("EATVENTURE_DEVICE_SERIAL")
    if serial:
        namespace["DEVICE_SERIAL"] = _device_id(serial)
    state_root = os.environ.get("EATVENTURE_STATE_ROOT")
    if state_root:
        digest = hashlib.sha256(profile["device_id"].encode("utf-8")).hexdigest()
        device_root = Path(state_root).expanduser().resolve() / "device-data" / digest
        namespace.update(
            DEVICE_TEMPLATE_DIR=None,
            LOG_DIR=device_root / "logs",
        )
    namespace.update(SHOW_AI_VISION=False, SHOW_SCRCPY=False, STATUS_PANEL=False)
