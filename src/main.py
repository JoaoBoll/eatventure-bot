"""
EatVenture AI — ponto de entrada.

Uso:

    python src/main.py

Mudanças em relação à versão anterior:

1. Código dentro de main(), não no topo do módulo.
   Antes importar main.py já ligava o scrcpy.

2. Loop principal dorme esperando frame novo, em vez de
   girar a 100% de CPU reprocessando o mesmo frame.

3. Informa ao detector as categorias do estado atual, e ao
   ActionManager a resolução em que as detecções estão.
"""

import argparse
import subprocess
import sys

import cv2

from actions.manager import ActionManager
from capture.screen import ScreenCapture
from core import devices, log
from core.battery import BatteryMonitor
from core.config import (
    AI_WINDOW_NAME,
    AI_WINDOW_POSITION,
    DEVICE_SERIAL,
    LOG_LEVEL,
    SCRCPY_EXTRA_ARGS,
    SCRCPY_PATH,
    SHOW_AI_VISION,
    SHOW_SCRCPY,
    VISION_FILTER_BY_STATE,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from core.state_machine import StateMachine
from vision.detector import Detector
from vision.worker import VisionWorker

logger = log.get("main")


# =========================================================
# ARGUMENTOS
# =========================================================

def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description="EatVenture AI",
    )

    parser.add_argument(
        "--device",
        metavar="SERIAL",
        help=(
            "serial do device (adb devices). Sem isto, usa "
            "DEVICE_SERIAL do config; sem os dois, pergunta "
            "quando houver mais de um conectado."
        ),
    )

    return parser.parse_args(argv)


# =========================================================
# SCRCPY
# =========================================================

def start_scrcpy(device_id):

    window_title = f"EatVenture ({device_id})"

    logger.info("Iniciando %s...", window_title)

    return subprocess.Popen(
        [
            SCRCPY_PATH,

            "--serial",
            device_id,

            "--window-title",
            window_title,
        ]
        + list(SCRCPY_EXTRA_ARGS)
    )


# =========================================================
# JANELA
# =========================================================

def setup_window():

    cv2.namedWindow(AI_WINDOW_NAME, cv2.WINDOW_NORMAL)

    cv2.resizeWindow(
        AI_WINDOW_NAME,
        WINDOW_WIDTH,
        WINDOW_HEIGHT,
    )

    cv2.moveWindow(AI_WINDOW_NAME, *AI_WINDOW_POSITION)


# =========================================================
# LOOP
# =========================================================

def run_loop(
    capture,
    vision,
    detector,
    state_machine,
    actions,
    battery,
):

    # Começa igual à versão inicial do ScreenCapture, então
    # a primeira espera é pelo primeiro frame de verdade.
    last_version = 0

    while True:

        # -------------------------------------------------
        # Espera o frame mais recente
        # -------------------------------------------------

        # Timeout curto para a janela do OpenCV continuar
        # respondendo (e o ESC funcionar) mesmo se o stream
        # travar.
        frame, version, timestamp = capture.get_frame(
            since_version=last_version,
            timeout=0.2,
        )

        if not capture.is_running():

            logger.warning("Stream encerrado.")

            break

        if frame is None:
            continue

        last_version = version

        height, width = frame.shape[:2]

        actions.set_frame_size(width, height)

        # -------------------------------------------------
        # Envia frame para a IA
        # -------------------------------------------------

        if VISION_FILTER_BY_STATE:

            vision.set_categories(
                state_machine.wanted_categories()
            )

        vision.set_frame(frame, timestamp)

        # -------------------------------------------------
        # Detecções (do frame que o worker terminou)
        # -------------------------------------------------

        detections, lag = vision.get_detections()

        # -------------------------------------------------
        # STATE MACHINE
        # -------------------------------------------------

        state_machine.update(detections, lag)

        # -------------------------------------------------
        # AI VISION
        # -------------------------------------------------

        # Com a janela desligada não há cópia nem desenho:
        # o overlay era o único lugar que copiava o frame.
        if not SHOW_AI_VISION:
            continue

        ai_frame = detector.draw(
            frame.copy(),
            detections,
            {
                "capture_fps": capture.get_fps(),
                "detect_fps": vision.get_fps(),
                "detect_ms": vision.get_duration() * 1000,
                "lag": lag,

                # Leitura em memória, feita por outra thread:
                # dumpsys custa ~56 ms e não pode entrar aqui.
                "battery": battery.get(),

                "cycle": state_machine.cycle_stats(),
            },
        )

        cv2.imshow(AI_WINDOW_NAME, ai_frame)

        # O ESC só chega aqui: é esta janela que recebe as
        # teclas. Sem ela, o encerramento é por Ctrl+C.
        if cv2.waitKey(1) & 0xFF == 27:
            break


# =========================================================
# MAIN
# =========================================================

def main(argv=None):

    log.setup(LOG_LEVEL)

    args = parse_args(argv)

    # --device manda no config, o config manda na pergunta.
    #
    # O Ctrl+C aqui é desistência do usuário na pergunta, não
    # erro: um traceback de KeyboardInterrupt só polui a tela.
    try:

        device_id = devices.resolver(args.device or DEVICE_SERIAL)

    except KeyboardInterrupt:

        logger.info("Cancelado.")

        return 1

    windows = [
        name
        for name, enabled in (
            ("IA", SHOW_AI_VISION),
            ("scrcpy", SHOW_SCRCPY),
        )
        if enabled
    ]

    logger.info(
        "Janelas: %s | encerrar com %s",
        ", ".join(windows) or "nenhuma (headless)",
        "ESC ou Ctrl+C" if SHOW_AI_VISION else "Ctrl+C",
    )

    # O espelho do scrcpy é opcional: a captura do bot não
    # passa por ele.
    scrcpy_process = (
        start_scrcpy(device_id)
        if SHOW_SCRCPY
        else None
    )

    capture = ScreenCapture(device_id)

    detector = Detector()

    vision = VisionWorker(detector)

    actions = ActionManager(device_id)

    state_machine = StateMachine(actions)

    battery = BatteryMonitor(actions.android)

    try:

        capture.start()

        vision.start()

        actions.start()

        battery.start()

        if SHOW_AI_VISION:

            setup_window()

        run_loop(
            capture,
            vision,
            detector,
            state_machine,
            actions,
            battery,
        )

    except KeyboardInterrupt:

        logger.info("Interrompido pelo usuário.")

    finally:

        logger.info("Encerrando EatVenture AI...")

        battery.stop()

        vision.stop()

        actions.stop()

        capture.stop()

        cv2.destroyAllWindows()

        if scrcpy_process:

            scrcpy_process.terminate()

            try:

                scrcpy_process.wait(timeout=2)

            except subprocess.TimeoutExpired:

                scrcpy_process.kill()


if __name__ == "__main__":

    sys.exit(main())
