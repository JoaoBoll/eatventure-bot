"""Propriedade local da sessão do bot por aparelho físico."""

from contextlib import ExitStack, contextmanager
import hashlib
import re
import time
import uuid

from core import log
from core.i18n import t
from core.processes import file_lock


logger = log.get("device_session")


@contextmanager
def device_session(directory, device_id, *, cancelled=None, timeout=35.0):
    key = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
    lock = directory / f"{key}.lock"
    owner = directory / f"{key}.owner"
    deadline = time.monotonic() + timeout
    requested_token = None

    with ExitStack() as locks:
        while True:
            if cancelled is not None and cancelled():
                yield None
                return
            try:
                locks.enter_context(file_lock(lock, blocking=False))
                break
            except BlockingIOError:
                pass

            if time.monotonic() >= deadline:
                raise RuntimeError(t("messages.device_session_stop_failed", device_id=device_id))
            try:
                previous_token = owner.read_text(encoding="ascii").strip()
            except (FileNotFoundError, UnicodeError):
                previous_token = ""
            if previous_token != requested_token and re.fullmatch(r"[0-9a-f]{32}", previous_token):
                (directory / f"{key}.{previous_token}.stop").touch()
                requested_token = previous_token
                logger.info(t("messages.device_session_replacing", device_id=device_id))
            time.sleep(0.1)

        if requested_token is None:
            try:
                previous_token = owner.read_text(encoding="ascii").strip()
            except (FileNotFoundError, UnicodeError):
                previous_token = ""
            if re.fullmatch(r"[0-9a-f]{32}", previous_token):
                stop_file = directory / f"{key}.{previous_token}.stop"
                stop_file.touch()
                wait_deadline = time.monotonic() + 3
                while stop_file.is_file() and time.monotonic() < wait_deadline:
                    time.sleep(0.1)
                stop_file.unlink(missing_ok=True)
        if requested_token is not None:
            (directory / f"{key}.{requested_token}.stop").unlink(missing_ok=True)
        token = uuid.uuid4().hex
        owner.write_text(token, encoding="ascii")
        stop_file = directory / f"{key}.{token}.stop"
        try:
            yield stop_file.is_file
        finally:
            stop_file.unlink(missing_ok=True)
            try:
                if owner.read_text(encoding="ascii").strip() == token:
                    owner.unlink(missing_ok=True)
            except (FileNotFoundError, UnicodeError):
                pass
