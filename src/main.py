"""EatVenture AI — ponto de entrada (python src/main.py)."""

import argparse
from functools import partial
import os
import subprocess
import sys
import time

from core import log
from core.config import (
    AI_WINDOW_NAME,
    AI_WINDOW_POSITION,
    DEVICE_SERIAL,
    LOG_DIR,
    LOG_LEVEL,
    DATASET_SAVE,
    SCRCPY_EXTRA_ARGS,
    SCRCPY_PATH,
    SHOW_AI_VISION,
    SHOW_SCRCPY,
    STATUS_PANEL,
    STATUS_PANEL_INTERVAL,
    VISION_FILTER_BY_STATE,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)


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

    parser.add_argument(
        "--ai-collect",
        action="store_true",
        help="Ativa coleta de dados de IA para o dataset.",
    )

    parser.add_argument(
        "--log",
        action="store_true",
        help="Salva o log completo da execução em arquivo na pasta logs/.",
    )

    parser.add_argument(
        "--layout-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    return parser.parse_args(argv)


# Registra também erros ao importar as dependências durante a execução direta.
if __name__ == "__main__":
    log.setup(LOG_LEVEL, log_dir=LOG_DIR if parse_args().log else None)

import cv2

from actions.manager import ActionManager
from capture.screen import ScreenCapture
from core import devices
from core.app_guard import EatventureGuard
from core.battery import BatteryMonitor
from core.state_machine import StateMachine
from core.status import StatusPanel, formata_bateria
from dataset.recorder import DatasetRecorder
from vision.detector import Detector
from vision.template_updater import DefaultTemplateScanner
from vision.worker import EngineWorker, VisionWorker

logger = log.get("main")


def start_scrcpy(device_id):

    window_title = f"EatVenture ({device_id})"

    logger.info("Iniciando %s...", window_title)

    process = subprocess.Popen(
        [
            SCRCPY_PATH,

            "--serial",
            device_id,

            "--window-title",
            window_title,
        ]
        + list(SCRCPY_EXTRA_ARGS),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    process.output_thread = log.start_process_output(process, "scrcpy")
    return process


def setup_window():

    cv2.namedWindow(AI_WINDOW_NAME, cv2.WINDOW_NORMAL)

    cv2.resizeWindow(
        AI_WINDOW_NAME,
        WINDOW_WIDTH,
        WINDOW_HEIGHT,
    )

    cv2.moveWindow(AI_WINDOW_NAME, *AI_WINDOW_POSITION)


def build_recorder(ai_collect=None):
    """Gravador local do dataset, ou None se desligado."""

    dataset_enabled = ai_collect if ai_collect is not None else DATASET_SAVE

    if not dataset_enabled:
        return None

    return DatasetRecorder(
        session=os.environ.get("EATVENTURE_SESSION_ID") or None,
    )


def read_detections(vision):
    """(detecções, idade, frame, instante_do_frame). O frame é o que GEROU as detecções, não o mais recente — mantém imagem e rótulo consistentes no dataset."""

    frame, detections, frame_time = vision.get_input()

    lag = (
        max(0.0, time.monotonic() - frame_time)
        if frame_time
        else 0.0
    )

    return detections, lag, frame, frame_time


def render_ai_frame(frame, detector, detections, stats):
    height, width = frame.shape[:2]
    scale = min(WINDOW_WIDTH / width, WINDOW_HEIGHT / height, 1.0)
    if scale < 1.0:
        output = cv2.resize(
            frame, (int(round(width * scale)), int(round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        output = frame.copy()
    stats = {**stats, "coverage": detector.template_coverage()}
    return detector.draw(output, detections, stats, scale=scale)


def run_loop(
    capture,
    vision,
    detector,
    state_machine,
    actions,
    battery,
    panel=None,
    auto_trainer=None,
    runtime=None,
    app_guard=None,
    recorder=None,
    engine_worker=None,
):

    last_version = 0
    pause_started = None
    pause_label = None
    resume_floor = 0.0
    current_resolution = None

    # Evita repetir o aviso de visão caída a cada quadro.
    vision_avisada = False

    def update_panel():
        if panel is not None or runtime is not None:
            resumo = state_machine.summary()
            resumo["templates"] = detector.template_count()
            resumo["dados_sessao"] = recorder.stats()["saved"] if recorder is not None else 0
            if current_resolution is not None:
                resumo["resolution"] = current_resolution
            if pause_started is not None:
                resumo["estado"] = pause_label or "PAUSADO"
            if runtime is not None:
                runtime.publish(
                    resumo, fps=vision.get_fps(), capture_fps=capture.get_fps(),
                    templates=resumo["templates"], battery=battery.get(),
                )
            if panel is None:
                return
            panel.update(
                resumo,
                extra=(
                    f"{vision.get_fps():.1f} fps"
                    f" | {formata_bateria(battery.get())}"
                ),
                misses=detector.miss_report(),
            )

    while True:

        if runtime is not None:
            if runtime.should_stop():
                break
            runtime.preview.requested()

        training_paused = auto_trainer is not None and auto_trainer.pause_requested
        template_paused = runtime is not None and runtime.should_pause()
        paused = training_paused or template_paused
        pause_label = (
            "PAUSADO PARA TEMPLATE" if template_paused
            else "PAUSADO PARA TREINO" if training_paused
            else None
        )
        # Timeout curto para a janela do OpenCV continuar respondendo
        # (e o ESC funcionar) mesmo se o stream travar.
        frame, version, timestamp = capture.get_frame(
            since_version=last_version,
            timeout=0.2,
        )

        if not capture.is_running():

            logger.warning("Stream encerrado.")

            break

        if paused:
            if pause_started is None:
                pause_started = time.monotonic()
                vision.set_frame_floor(float("inf"))
                logger.info(
                    "Bot pausado %s.",
                    "para selecionar template" if template_paused else "para o treino automático",
                )
            recorder = state_machine.recorder
            if training_paused and not actions.is_busy() and (recorder is None or recorder.prepare_for_training()):
                auto_trainer.acknowledge_pause()
        elif pause_started is not None:
            resume_floor = time.monotonic()
            state_machine.resume_after_pause(resume_floor - pause_started)
            pause_started = None
            logger.info("Treino automático encerrado; retomando o bot.")

        if frame is None or version == last_version:
            update_panel()
            if SHOW_AI_VISION and cv2.waitKey(1) & 0xFF == 27:
                break
            continue

        last_version = version

        # Resolução real do frame: o VisionWorker devolve as detecções
        # nesse espaço, e é isso que faz a conversão frame->device ser
        # exata em qualquer resolução de device (não só 1080x2400).
        altura_frame, largura_frame = frame.shape[:2]
        current_resolution = f"{largura_frame}x{altura_frame}"

        actions.set_frame_size(largura_frame, altura_frame)

        detections, lag, detect_frame, detect_time = [], 0.0, None, 0.0
        guard_blocking = False
        if not paused:
            if VISION_FILTER_BY_STATE:
                vision.set_categories(state_machine.wanted_categories())

            floor = state_machine.frame_floor()
            if resume_floor:
                floor = max(floor, resume_floor)
            vision.set_frame_floor(floor)
            vision.set_frame(frame, timestamp)
            if engine_worker is not None:
                engine_worker.set_frame(frame)

            detections, lag, detect_frame, detect_time = (
                read_detections(vision)
            )
            if app_guard is not None and engine_worker is not None and state_machine.state == StateMachine.NORMAL:
                pending_scan = app_guard.pending_scan()
                if pending_scan is not None:
                    result = engine_worker.result(pending_scan)
                    if result is not None:
                        if app_guard.resolve_scan(result):
                            state_machine.confirm_game_visible()
                        guard_blocking = app_guard.startup_validation
                    else:
                        guard_blocking = app_guard.startup_validation
                elif app_guard.needs_startup_scan():
                    app_guard.request_scan(engine_worker.request_scan())
                    guard_blocking = True
                # Swipe só explora a tela; qualquer outra ação real zera
                # o intervalo antes de o guardião poder reiniciar o jogo.
                elif app_guard.needs_scan(state_machine.last_meaningful_action_time):
                    if recorder is not None:
                        recorder.discard_since(state_machine.last_meaningful_action_wall)
                    app_guard.request_scan(engine_worker.request_scan())
            elif app_guard is not None:
                app_guard.cancel_scan()

            if detect_frame is not None and (
                detect_frame.shape[:2] != frame.shape[:2]
                or detect_time <= resume_floor
            ):
                detections, detect_frame, detect_time = [], None, 0.0

        # Thread do detector morta não para o programa sozinha (captura e
        # janela seguem ativas, o bot só some de agir) — loga uma vez.
        if not vision.is_alive() and not vision_avisada:

            logger.error(
                "A thread da visão não está rodando (%s) — "
                "não vão existir detecções e o bot não vai "
                "agir.",
                vision.last_error or "motivo desconhecido",
            )

            vision_avisada = True

        if not paused and not guard_blocking and detect_frame is not None and detect_time > 0:
            state_machine.update(
                detections,
                lag,
                detect_frame,
                detect_time,
            )

        update_panel()

        web_vision = runtime is not None and runtime.preview.requested()
        if not SHOW_AI_VISION and not web_vision:
            continue

        stats = {
            "capture_fps": capture.get_fps(),
            "detect_fps": vision.get_fps(),
            "detect_ms": vision.get_duration() * 1000,
            "lag": lag,

            # Leitura em memória: dumpsys custa ~56 ms e não pode entrar aqui.
            "battery": battery.get(),
            "cycle": state_machine.cycle_stats(),
            "state": (
                "PAUSADO PARA TEMPLATE" if template_paused
                else "PAUSADO PARA TREINO" if paused
                else state_machine.state
            ),
            "searched": detector.last_searched,
            "searchable": detector.last_total,
            "detections": len(detections),
            "waiting_settle": vision.waiting_settle,
            "vision_error": (
                None if vision.is_alive()
                else vision.last_error or "thread da visão parada"
            ),
        }

        if web_vision:
            runtime.preview.submit(
                frame,
                None if runtime.preview.mode() == "template" else partial(
                    render_ai_frame, detector=detector, detections=detections, stats=stats,
                ),
            )

        if SHOW_AI_VISION:
            ai_frame = render_ai_frame(frame, detector, detections, stats)
            cv2.imshow(AI_WINDOW_NAME, ai_frame)
            # ESC só chega aqui, é esta janela que recebe teclas.
            if cv2.waitKey(1) & 0xFF == 27:
                break


def main(argv=None, runtime=None):

    args = parse_args(argv)

    log.setup(LOG_LEVEL, log_dir=LOG_DIR if args.log else None)

    # --device manda no config, o config manda na pergunta.
    # Ctrl+C aqui é desistência do usuário, não erro.
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

    scrcpy_process = None

    capture = ScreenCapture(device_id)

    # O aprendizado por resolução independe da coleta do dataset.
    detector = Detector()

    vision = VisionWorker(detector)

    actions = ActionManager(device_id)
    app_guard = EatventureGuard(actions.android)
    engine_detector = None
    engine_worker = None
    if app_guard.enabled:
        app_guard.lock_portrait()
        # O guardião precisa funcionar antes de existir qualquer override da
        # resolução. O scanner usa diretamente os defaults já carregados.
        engine_detector = DefaultTemplateScanner(detector)
        engine_worker = EngineWorker(engine_detector)

    recorder = build_recorder(ai_collect=True if args.ai_collect else None)

    state_machine = StateMachine(actions, recorder, detector)

    battery = BatteryMonitor(actions.android)

    # device_id já vem resolvido do adb: é o serial real em uso mesmo
    # quando veio da pergunta interativa, não do config.
    panel = (
        StatusPanel(
            device_id,
            interval=STATUS_PANEL_INTERVAL,
            stream=log.console_stream(),
            templates=detector.template_count(),
        )
        if STATUS_PANEL
        else None
    )

    if panel is not None:
        log.set_console_panel(panel)

    try:

        if SHOW_SCRCPY:
            scrcpy_process = start_scrcpy(device_id)

        capture.start()

        vision.start()

        if engine_worker is not None:
            engine_worker.start()

        actions.start()

        battery.start()

        if recorder:

            recorder.start()

        if SHOW_AI_VISION:

            setup_window()

        run_loop(
            capture,
            vision,
            detector,
            state_machine,
            actions,
            battery,
            panel,
            auto_trainer=recorder.auto_trainer if recorder else None,
            runtime=runtime,
            app_guard=app_guard,
            recorder=recorder,
            engine_worker=engine_worker,
        )

    except KeyboardInterrupt:

        logger.info("Interrompido pelo usuário.")

    finally:

        if runtime is not None:
            runtime.preview.stop()

        if panel is not None:
            try:
                panel.close()
            finally:
                log.set_console_panel(None)

        logger.info("Encerrando EatVenture AI...")

        # Antes do resto: fecha a amostra pendente e termina a fila local.
        if recorder:

            recorder.stop()

        battery.stop()

        vision.stop()

        if engine_worker is not None:
            engine_worker.stop()

        detector.stop()

        if engine_detector is not None:
            engine_detector.stop()

        actions.stop()

        capture.stop()

        if SHOW_AI_VISION:
            cv2.destroyAllWindows()

        if scrcpy_process:

            scrcpy_process.terminate()

            try:

                scrcpy_process.wait(timeout=2)

            except subprocess.TimeoutExpired:

                scrcpy_process.kill()
                scrcpy_process.wait(timeout=2)

            output_thread = getattr(scrcpy_process, "output_thread", None)
            if output_thread is not None:
                output_thread.join(timeout=2)


if __name__ == "__main__":

    sys.exit(main())
