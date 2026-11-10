"""Persistência das áreas BLOCK criadas pelo seletor visual do terminal."""

import hashlib
import json
import math
from pathlib import Path


MAX_BLOCK_REGIONS = 32
DEFAULT_STATE_ROOT = Path(__file__).resolve().parents[2] / "local"


def _path(serial, root=None):
    if not isinstance(serial, str) or not serial:
        raise ValueError("Dispositivo inválido para as áreas BLOCK.")
    digest = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    return Path(root or DEFAULT_STATE_ROOT / "blocks") / f"{digest}.json"


def validate_regions(regions):
    if not isinstance(regions, (list, tuple)) or len(regions) > MAX_BLOCK_REGIONS:
        raise ValueError(f"Informe no máximo {MAX_BLOCK_REGIONS} áreas BLOCK.")
    validated = []
    for index, region in enumerate(regions, 1):
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError(f"BLOCK {index} inválido.")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in region):
            raise ValueError(f"BLOCK {index}: informe números.")
        values = tuple(float(value) for value in region)
        x, y, width, height = values
        if (
            not all(math.isfinite(value) and 0 <= value <= 1 for value in values)
            or width <= 0
            or height <= 0
            or x + width > 1 + 1e-9
            or y + height > 1 + 1e-9
        ):
            raise ValueError(f"BLOCK {index} deve ficar dentro da tela.")
        validated.append([x, y, min(width, 1 - x), min(height, 1 - y)])
    return validated


def _load_legacy_regions(serial, root=None):
    path = _path(serial, root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError(f"Arquivo de BLOCKs inválido: {path}") from error
    if not isinstance(data, dict) or data.get("device") != serial:
        raise ValueError("O arquivo de BLOCKs pertence a outro dispositivo.")
    return validate_regions(data.get("regions", []))


def migrate_legacy_blocks(store, device_id, aliases):
    profile = store.load(device_id)
    path = store.path_for(device_id)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "block_regions" in raw:
            return profile
    regions = []
    for alias in dict.fromkeys([device_id, *aliases]):
        for region in _load_legacy_regions(alias, store.root.parent / "blocks"):
            if region not in regions:
                regions.append(region)
    if regions:
        profile["block_regions"] = validate_regions(regions)
        return store.save(device_id, profile)
    return profile


def terminal_profile(serial, state_root=None):
    # A mesma descoberta do painel resolve USB/Wi-Fi para a identidade física.
    from core.device_profiles import ProfileStore
    from dashboard.supervisor import DeviceManager

    manager = DeviceManager()
    device = next((
        device for device in manager.list()
        if serial == device["id"] or serial in device["aliases"]
    ), None)
    if device is None:
        raise RuntimeError(manager.last_error or f"Dispositivo não encontrado: {serial}")
    store = ProfileStore(Path(state_root or DEFAULT_STATE_ROOT) / "devices")
    profile = migrate_legacy_blocks(store, device["id"], device["aliases"])
    return store, profile


def load_block_regions(serial, root=None):
    return terminal_profile(serial, root)[1]["block_regions"]


def save_block_regions(serial, regions, root=None):
    store, profile = terminal_profile(serial, root)
    profile["block_regions"] = validate_regions(regions)
    return store.save(profile["device_id"], profile)["block_regions"]
