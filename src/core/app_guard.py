"""Garante que o Eatventure esteja aberto e exibindo a tela de jogo."""

import time

from core import log
from core.config import (
    EATVENTURE_GUARD_ENABLED,
    EATVENTURE_GUARD_IDLE_SECONDS,
    EATVENTURE_GUARD_STARTUP_GRACE,
    EATVENTURE_LOCK_PORTRAIT,
    EATVENTURE_PACKAGE,
)


logger = log.get("app_guard")


class EatventureGuard:
    """Usa processo + template engine para distinguir app aberto de tela útil."""

    def __init__(self, android):
        self.android = android
        self.enabled = EATVENTURE_GUARD_ENABLED
        self.lock_portrait_enabled = EATVENTURE_LOCK_PORTRAIT
        self.package = EATVENTURE_PACKAGE
        self.idle_seconds = max(0.0, EATVENTURE_GUARD_IDLE_SECONDS)
        self.startup_grace = max(0.0, EATVENTURE_GUARD_STARTUP_GRACE)
        self._next_check = 0.0
        self._started_at = time.monotonic()
        self._pending_scan = None
        self._startup_validation = True

    def _running(self):
        output = self.android.shell("pidof", self.package, timeout=3.0)
        return bool(output and output.strip())

    def lock_portrait(self):
        """Mantém o Android em retrato antes de o app poder solicitar rotação."""
        if not self.lock_portrait_enabled:
            return True

        automatic = self.android.shell(
            "settings", "put", "system", "accelerometer_rotation", "0",
            timeout=3.0,
        )
        rotation = self.android.shell(
            "settings", "put", "system", "user_rotation", "0",
            timeout=3.0,
        )
        # O bloqueio do sensor não impede um app de pedir LANDSCAPE
        # explicitamente. O Window Manager tem de ignorar esse pedido.
        window_rotation = self.android.shell(
            "cmd", "window", "set-user-rotation", "lock", "0",
            timeout=3.0,
        )
        ignore_app_request = self.android.shell(
            "cmd", "window", "set-ignore-orientation-request", "true",
            timeout=3.0,
        )
        if (
            automatic is None
            or rotation is None
            or window_rotation is None
            or ignore_app_request is None
        ):
            logger.warning("Não foi possível bloquear a rotação em retrato.")
            return False

        logger.info(
            "Rotação automática desativada; Window Manager fixado em retrato."
        )
        return True

    def _launch(self, restart=False):
        self.lock_portrait()
        if restart:
            self.android.shell("am", "force-stop", self.package, timeout=4.0)
        started = self.android.shell(
            "monkey", "-p", self.package,
            "-c", "android.intent.category.LAUNCHER", "1",
            timeout=8.0,
        )
        if started is None:
            logger.warning("Não foi possível abrir o Eatventure (%s).", self.package)
            return False

        # O app pode alterar a preferência de orientação durante a própria
        # inicialização; reaplica o lock depois que o launcher o abriu.
        self.lock_portrait()
        self._pending_scan = None
        self._startup_validation = True
        self._next_check = time.monotonic() + max(
            self.startup_grace, self.idle_seconds,
        )
        logger.warning(
            "%s Eatventure (%s).",
            "Reiniciando" if restart else "Abrindo", self.package,
        )
        return True

    def needs_scan(self, last_action_at):
        """Só verifica a tela depois de um período sem nenhuma ação do bot."""
        if not self.enabled:
            return False
        now = time.monotonic()
        reference = last_action_at if last_action_at > 0 else self._started_at
        return (
            not self._startup_validation
            and self._pending_scan is None
            and now >= self._next_check
            and now - reference >= self.idle_seconds
        )

    def needs_startup_scan(self):
        return (
            self.enabled
            and self._startup_validation
            and self._pending_scan is None
            and time.monotonic() >= self._next_check
        )

    @property
    def startup_validation(self):
        return self._startup_validation

    def request_scan(self, token):
        self._pending_scan = token
        logger.info(
            "Sem ação relevante há %.0fs; verificando o template engine.",
            self.idle_seconds,
        )

    def cancel_scan(self):
        self._pending_scan = None

    def pending_scan(self):
        return self._pending_scan

    def resolve_scan(self, detections):
        """Age uma vez para o resultado da busca pontual solicitada."""
        if self._pending_scan is None:
            return False
        self._pending_scan = None
        self._next_check = time.monotonic() + self.idle_seconds
        detection = max(
            detections,
            key=lambda item: float(item.get("confidence", 0.0)),
            default=None,
        )
        confidence = float(detection.get("confidence", 0.0)) if detection else 0.0
        if detections:
            self._startup_validation = False
            logger.info(
                "Template engine encontrado: %.1f%% (escala %.3f) em %d,%d (%s); Eatventure permanece aberto.",
                confidence * 100,
                float(detection.get("template_scale", 1.0)),
                detection.get("x", 0),
                detection.get("y", 0),
                detection.get("template_path") or detection.get("name") or "template desconhecido",
            )
            return True
        logger.warning("Template engine não encontrado (0.0%% acima do limiar).")
        if not self._running():
            logger.warning("Template engine ausente e processo não encontrado.")
            self._launch()
            return False
        logger.warning("Template engine ausente; reiniciando o Eatventure.")
        self._launch(restart=True)
        return False
