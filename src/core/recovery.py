"""Recuperação limitada da execução pelo terminal."""

import threading
from core.i18n import t
import os
import subprocess
import sys

from core import config, devices, log


logger = log.get("recovery")


def run_with_retries(run):
    if os.environ.get("EATVENTURE_BOT_CHILD") == "1":
        return run()
    environment = {**os.environ, "EATVENTURE_BOT_CHILD": "1"}
    command = [sys.executable, *sys.argv]
    if not any(arg == "--device" or arg.startswith("--device=") for arg in sys.argv[1:]):
        try:
            command.extend(["--device", devices.resolver(config.DEVICE_SERIAL)])
        except KeyboardInterrupt:
            logger.info(t("messages.startup_cancelled"))
            return 0
    for attempt in range(config.BOT_RESTART_LIMIT + 1):
        process = None
        try:
            process = subprocess.Popen(command, env=environment)
            if attempt:
                logger.info(t("messages.process_restarted"), attempt, config.BOT_RESTART_LIMIT, process.pid)
            returncode = process.wait()
            if returncode == 0:
                return 0
            raise RuntimeError(t("messages.process_exit_code", returncode=returncode))
        except KeyboardInterrupt:
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            logger.info(t("messages.recovery_cancelled"))
            return 0
        except Exception:
            logger.exception(t("messages.bot_failed"))
            if attempt >= config.BOT_RESTART_LIMIT:
                logger.error(t("messages.recovery_limit_reached"), config.BOT_RESTART_LIMIT)
                return 1
            logger.warning(
                t("messages.recovery_scheduled"),
                attempt + 1, config.BOT_RESTART_LIMIT, config.BOT_RESTART_DELAY,
            )
            try:
                threading.Event().wait(config.BOT_RESTART_DELAY)
            except KeyboardInterrupt:
                logger.info(t("messages.recovery_cancelled"))
                return 0
