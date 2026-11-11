"""EatVenture AI — atualizador de layouts (python src/main_layouts.py).

Mesma janela/visão do main.py; a única diferença é o que roda por trás:
aqui não há StateMachine nem ações, só captura + Detector em todas as
categorias. Aprende os overrides que faltam e atualiza os existentes em
segundo plano, como o detector do bot.

O outro processo recarrega os overrides sozinho — ver
Detector._maybe_reload_templates / TEMPLATES_WATCH_INTERVAL. Nenhuma
comunicação entre os dois além do disco.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import time

import cv2

from capture.screen import ScreenCapture
from core import config, devices, log
from core.cli import ArgumentParser, configure_locale
from core.i18n import t
from core.block_regions import load_block_regions
from core.config import (
    AI_WINDOW_NAME,
    AI_WINDOW_POSITION,
    DEVICE_SERIAL,
    LOG_LEVEL,
    SCRCPY_EXTRA_ARGS,
    SCRCPY_PATH,
    SHOW_AI_VISION,
    SHOW_SCRCPY,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from core.metrics import DurationMeter, RateMeter

from vision.detector import Detector

logger = log.get("main_layouts")

# Só usado quando SHOW_AI_VISION está desligado — sem janela, é o único
# jeito de ver o progresso.
REPORT_INTERVAL = 10.0


def parse_args(argv=None):
    configure_locale(argv)

    parser = ArgumentParser(
        description=t("terminal.layouts_description"),
    )
    parser.add_argument("--state-dir", type=Path, help=t("terminal.state_dir_help"))

    parser.add_argument(
        "--device",
        metavar="SERIAL",
        help=(
            t("terminal.main_device_help")
        ),
    )

    return parser.parse_args(argv)


def start_scrcpy(device_id):

    window_title = f"EatVenture layouts ({device_id})"

    logger.info(t("terminal.starting", title=window_title))

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


def setup_window():

    cv2.namedWindow(AI_WINDOW_NAME, cv2.WINDOW_NORMAL)

    cv2.resizeWindow(
        AI_WINDOW_NAME,
        WINDOW_WIDTH,
        WINDOW_HEIGHT,
    )

    cv2.moveWindow(AI_WINDOW_NAME, *AI_WINDOW_POSITION)


def run_loop(capture, detector):

    last_version = 0
    reported_at = 0.0

    detect_rate = RateMeter()
    detect_duration = DurationMeter()

    while True:

        # Timeout curto para a janela do OpenCV continuar respondendo
        # (e o ESC funcionar) mesmo se o stream travar.
        frame, version, _timestamp = capture.get_frame(
            since_version=last_version,
            timeout=0.2,
        )

        if not capture.is_running():

            logger.warning(t("messages.stream_ended"))

            break

        if frame is None or version == last_version:
            if SHOW_AI_VISION and cv2.waitKey(1) & 0xFF == 27:
                break
            continue

        last_version = version

        started = time.monotonic()

        # categories=None: passa por TODAS as categorias, não só as do
        # estado atual do jogo — é isso que dá cobertura ampla aos defaults.
        detections = detector.detect(frame)

        detect_duration.add(time.monotonic() - started)
        detect_rate.tick()

        if not SHOW_AI_VISION:

            agora = time.monotonic()

            if agora - reported_at >= REPORT_INTERVAL:

                reported_at = agora

                cobertos, total = detector.template_coverage() or (0, 0)

                logger.info(
                    t("messages.template_coverage"),
                    cobertos,
                    total,
                )

            continue

        altura_frame, largura_frame = frame.shape[:2]

        escala_janela = min(
            WINDOW_WIDTH / largura_frame,
            WINDOW_HEIGHT / altura_frame,
            1.0,
        )

        if escala_janela < 1.0:

            ai_frame = cv2.resize(
                frame,
                (
                    int(round(largura_frame * escala_janela)),
                    int(round(altura_frame * escala_janela)),
                ),
                interpolation=cv2.INTER_AREA,
            )

        else:

            ai_frame = frame.copy()

        ai_frame = detector.draw(
            ai_frame,
            detections,
            {
                "capture_fps": capture.get_fps(),
                "detect_fps": detect_rate.rate(),
                "detect_ms": detect_duration.average() * 1000,
                "detections": len(detections),

                # Cobertura da pasta da resolução sobre o default —
                # o número que diz se ainda vale a pena este processo rodar.
                "coverage": detector.template_coverage(),
            },
            scale=escala_janela,
        )

        cv2.imshow(AI_WINDOW_NAME, ai_frame)

        # ESC só chega aqui, é esta janela que recebe teclas.
        if cv2.waitKey(1) & 0xFF == 27:
            break


def main(argv=None):

    log.setup(LOG_LEVEL)

    args = parse_args(argv)

    try:

        device_id = devices.resolver(args.device or DEVICE_SERIAL)

    except KeyboardInterrupt:

        logger.info(t("messages.cancelled"))

        return 1

    scrcpy_process = None

    capture = ScreenCapture(device_id)

    terminal_blocks = [] if config.DEVICE_PROFILE_ID else load_block_regions(device_id, args.state_dir)
    detector = Detector(block_regions=[*config.BLOCK_REGIONS, *terminal_blocks])

    logger.info(
        t("messages.layout_updater_running"),
        device_id,
        t("messages.exit_keys") if SHOW_AI_VISION else t("messages.exit_ctrl_c"),
    )

    try:

        if SHOW_SCRCPY:
            scrcpy_process = start_scrcpy(device_id)

        capture.start()

        if SHOW_AI_VISION:
            setup_window()

        run_loop(capture, detector)

    except KeyboardInterrupt:

        logger.info(t("messages.user_interrupted"))

    finally:

        logger.info(t("messages.layout_updater_stopping"))

        detector.stop()

        capture.stop()

        cv2.destroyAllWindows()

        if scrcpy_process:

            scrcpy_process.terminate()

            try:

                scrcpy_process.wait(timeout=2)

            except subprocess.TimeoutExpired:

                scrcpy_process.kill()

    return 0


if __name__ == "__main__":

    sys.exit(main())
