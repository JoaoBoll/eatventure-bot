"""Persistência das áreas BLOCK criadas pelo seletor visual do terminal."""

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from core.config import PROJECT_ROOT


MAX_BLOCK_REGIONS = 32
DEFAULT_ROOT = PROJECT_ROOT / "local" / "blocks"


def _path(serial, root=None):
    if not isinstance(serial, str) or not serial:
        raise ValueError("Dispositivo inválido para as áreas BLOCK.")
    digest = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    return Path(root or DEFAULT_ROOT) / f"{digest}.json"


def validate_regions(regions):
    if not isinstance(regions, (list, tuple)) or len(regions) > MAX_BLOCK_REGIONS:
        raise ValueError(f"Informe no máximo {MAX_BLOCK_REGIONS} áreas BLOCK.")
    validated = []
    for index, region in enumerate(regions, 1):
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError(f"BLOCK {index} inválido.")
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


def load_block_regions(serial, root=None):
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


def save_block_regions(serial, regions, root=None):
    regions = validate_regions(regions)
    path = _path(serial, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        {"device": serial, "regions": regions},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".blocks-", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return regions
