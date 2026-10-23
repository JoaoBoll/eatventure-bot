"""Preferências locais aplicadas somente ao processo do dispositivo escolhido."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from collections.abc import Mapping


MAX_PROFILE_BYTES = 256 * 1024
MAX_CATEGORY_THRESHOLDS = 128
MAX_ITEM_THRESHOLDS = 2000
_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_PROFILE_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")
_SOURCE_PREFIX = re.compile(r"^(\d+x\d+)_(.+)$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_RUN_DEFAULTS = {"ai_collect": False, "layout_only": False, "save_log": False}


def _spec(config, label, group, kind, minimum=None, maximum=None, step=None):
    field = {"config": config, "label": label, "group": group, "type": kind}
    if minimum is not None:
        field.update(min=minimum, max=maximum, step=step)
    return field


SETTING_SPECS = {
    "shape_threshold": _spec("SHAPE_THRESHOLD", "Fidelidade da forma", "Detecção", "float", 0.0, 1.0, 0.01),
    "color_threshold": _spec("COLOR_THRESHOLD", "Fidelidade das cores", "Detecção", "float", 0.0, 1.0, 0.01),
    "max_detection_age": _spec("MAX_DETECTION_AGE", "Validade da detecção (s)", "Detecção", "float", 0.1, 60.0, 0.1),
    "vision_filter_by_state": _spec("VISION_FILTER_BY_STATE", "Filtrar pelo estado do jogo", "Detecção", "bool"),
    "vision_interval": _spec("VISION_INTERVAL", "Intervalo de detecção (s)", "Detecção", "float", 0.01, 10.0, 0.01),
    "capture_max_fps": _spec("CAPTURE_MAX_FPS", "Limite de FPS", "Captura", "int", 1, 120, 1),
    "learn_resolution_templates": _spec("LEARN_RESOLUTION_TEMPLATES", "Aprender templates da resolução", "Templates", "bool"),
    "template_refresh_interval": _spec("TEMPLATE_REFRESH_INTERVAL", "Intervalo de aprendizado (s)", "Templates", "float", 0.1, 3600.0, 0.1),
    "templates_watch_interval": _spec("TEMPLATES_WATCH_INTERVAL", "Verificar novos templates (s; 0 desativa)", "Templates", "float", 0.0, 3600.0, 0.5),
    "dataset_jpeg_quality": _spec("DATASET_JPEG_QUALITY", "Qualidade JPEG da coleta", "Coleta e treino", "int", 1, 100, 1),
    "dataset_max_disk_mb": _spec("DATASET_MAX_DISK_MB", "Limite do dataset (MB; 0 ilimitado)", "Coleta e treino", "int", 0, 10000000, 1),
    "ai_auto_train": _spec("AI_AUTO_TRAIN", "Treino automático", "Coleta e treino", "bool"),
    "ai_train_batch_size": _spec("AI_TRAIN_BATCH_SIZE", "Amostras por treino", "Coleta e treino", "int", 1, 1000000, 1),
    "ai_train_jobs": _spec("AI_TRAIN_JOBS", "Processos de treino", "Coleta e treino", "int", 1, 64, 1),
    "ai_pause_bot_while_training": _spec("AI_PAUSE_BOT_WHILE_TRAINING", "Pausar bot durante treino", "Coleta e treino", "bool"),
    "action_cooldown": _spec("ACTION_COOLDOWN", "Intervalo entre ações (s)", "Ações", "float", 0.0, 60.0, 0.1),
    "action_settle": _spec("ACTION_SETTLE", "Espera após ação (s)", "Ações", "float", 0.0, 60.0, 0.1),
    "swipe_waiting_time": _spec("SWIPE_WAITING_TIME", "Espera após deslizar (s)", "Ações", "float", 0.0, 60.0, 0.1),
    "upgrade_item_clicks": _spec("UPGRADE_ITEM_CLICKS", "Cliques por melhoria", "Ações", "int", 1, 100, 1),
    "upgrade_food_press": _spec("UPGRADE_FOOD_PRESS", "Duração ao melhorar comida (s)", "Ações", "float", 0.1, 30.0, 0.1),
    "exploration_delay": _spec("EXPLORATION_DELAY", "Espera para explorar (s)", "Ações", "float", 0.0, 300.0, 0.5),
    "exploration_delay_after_action": _spec("EXPLORATION_DELAY_AFTER_ACTION", "Espera para explorar após ação (s)", "Ações", "float", 0.0, 300.0, 0.5),
}


def settings_schema(defaults: Mapping):
    return [
        {"key": key, **spec, "default": defaults[spec["config"]]}
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
        raise ValueError("Identificador de dispositivo inválido.")
    return value


def _mapping(value, label, maximum):
    if not isinstance(value, dict) or len(value) > maximum:
        raise ValueError(f"{label}: objeto inválido ou com campos demais.")
    return value


def _number(value, label, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: informe um número.")
    if integer and not isinstance(value, int):
        raise ValueError(f"{label}: informe um número inteiro.")
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label}: o valor deve ficar entre {minimum} e {maximum}.")
    return int(value) if integer else float(value)


def _item_key(value):
    if not isinstance(value, str) or len(value) > 240 or value.count("/") != 1:
        raise ValueError("Template inválido; use categoria/arquivo.png.")
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
        raise ValueError("Template inválido; use categoria/arquivo.png.")
    return value


def template_key(category, name):
    if not isinstance(name, str):
        raise ValueError("Nome de template inválido.")
    source = _SOURCE_PREFIX.match(name)
    base_name = source.group(2) if source else name
    return _item_key(f"{category}/{base_name}")


def validate_profile(device_id, data):
    device_id = _device_id(device_id)
    data = _mapping(data, "Perfil", 6)
    unknown = data.keys() - {"device_id", "name", "settings", "category_thresholds", "item_thresholds", "run"}
    if unknown:
        raise ValueError("O perfil contém campos desconhecidos.")
    if data.get("device_id", device_id) != device_id:
        raise ValueError("O perfil pertence a outro dispositivo.")
    name = data.get("name", "")
    if not isinstance(name, str) or len(name) > 128 or (name and not name.isprintable()):
        raise ValueError("O nome deve ter até 128 caracteres sem controles.")

    settings = {}
    for key, value in _mapping(data.get("settings", {}), "Configurações", len(SETTING_SPECS)).items():
        if key not in SETTING_SPECS:
            raise ValueError(f"Configuração desconhecida: {key}.")
        spec = SETTING_SPECS[key]
        if spec["type"] == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{spec['label']}: informe verdadeiro ou falso.")
            settings[key] = value
        else:
            settings[key] = _number(value, spec["label"], spec["min"], spec["max"], integer=spec["type"] == "int")

    categories = {}
    for key, value in _mapping(data.get("category_thresholds", {}), "Fidelidades por categoria", MAX_CATEGORY_THRESHOLDS).items():
        if not isinstance(key, str) or not _CATEGORY.fullmatch(key):
            raise ValueError("Categoria inválida.")
        categories[key] = _number(value, f"Fidelidade de {key}", 0.0, 1.0)

    items = {}
    for key, value in _mapping(data.get("item_thresholds", {}), "Fidelidades por template", MAX_ITEM_THRESHOLDS).items():
        category, filename = _item_key(key).split("/")
        canonical = template_key(category, filename)
        if canonical in items:
            raise ValueError("O perfil contém fidelidades duplicadas para o mesmo template.")
        items[canonical] = _number(value, f"Fidelidade de {key}", 0.0, 1.0)

    run = dict(_RUN_DEFAULTS)
    for key, value in _mapping(data.get("run", {}), "Execução", len(_RUN_DEFAULTS)).items():
        if key not in run or not isinstance(value, bool):
            raise ValueError("Opção de execução inválida.")
        run[key] = value
    return {
        "device_id": device_id,
        "name": name.strip(),
        "settings": settings,
        "category_thresholds": categories,
        "item_thresholds": items,
        "run": run,
    }


def _read_profile(path):
    if path.is_symlink():
        raise ValueError("O perfil não pode ser um link simbólico.")
    with path.open("rb") as file:
        raw = file.read(MAX_PROFILE_BYTES + 1)
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError("O perfil excede o limite de 256 KB.")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Arquivo de perfil inválido.") from error
    if not isinstance(data, dict) or "device_id" not in data:
        raise ValueError("O perfil precisa identificar o dispositivo.")
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
            raise ValueError("O perfil salvo pertence a outro dispositivo.")
        return profile

    def save(self, device_id, data):
        profile = validate_profile(device_id, data)
        raw = (json.dumps(profile, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_PROFILE_BYTES:
            raise ValueError("O perfil excede o limite de 256 KB.")
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
    namespace.setdefault("DEVICE_PROFILE_ID", None)
    namespace.setdefault("DEVICE_TEMPLATE_DIR", None)
    namespace.setdefault("AI_DEVICE_MODEL_DIR", None)
    profile_path = os.environ.get("EATVENTURE_PROFILE")
    if not profile_path:
        return
    profile = _read_profile(Path(profile_path))
    expected_id = os.environ.get("EATVENTURE_DEVICE_ID")
    if expected_id is not None and expected_id != profile["device_id"]:
        raise ValueError("O perfil não corresponde ao dispositivo desta execução.")
    namespace.update({SETTING_SPECS[key]["config"]: value for key, value in profile["settings"].items()})
    namespace["CATEGORY_THRESHOLDS"] = {**namespace.get("CATEGORY_THRESHOLDS", {}), **profile["category_thresholds"]}
    namespace["ITEM_THRESHOLDS"] = dict(profile["item_thresholds"])
    namespace["DATASET_SAVE"] = profile["run"]["ai_collect"]
    namespace["DEVICE_PROFILE_ID"] = profile["device_id"]
    serial = os.environ.get("EATVENTURE_DEVICE_SERIAL")
    if serial:
        namespace["DEVICE_SERIAL"] = _device_id(serial)
    state_root = os.environ.get("EATVENTURE_STATE_ROOT")
    if state_root:
        digest = hashlib.sha256(profile["device_id"].encode("utf-8")).hexdigest()
        device_root = Path(state_root).expanduser().resolve() / "device-data" / digest
        namespace.update(
            DEVICE_TEMPLATE_DIR=device_root / "templates",
            AI_DEVICE_MODEL_DIR=device_root / "models",
            LOG_DIR=device_root / "logs",
        )
    namespace.update(SHOW_AI_VISION=False, SHOW_SCRCPY=False, STATUS_PANEL=False)
