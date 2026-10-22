"""EatVenture AI — ponto de entrada (python src/main.py)."""

import argparse
import subprocess
import sys
import time

from core import log
from core.config import (
    AI_WINDOW_NAME,
    AI_WINDOW_POSITION,
    DATASET_DB_BATCH,
    DATASET_DB_DSN,
    DATASET_DB_ENABLED,
    DATASET_DB_SCHEMA,
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
        help=(
            "detect() busca só os overrides da resolução, sem os "
            "templates default (mais rápido, mas cego onde ainda não há "
            "override). Os existentes continuam sendo atualizados "
            "em segundo plano quando o aprendizado está ativo."
        ),
    )

    return parser.parse_args(argv)


# Registra também erros ao importar as dependências durante a execução direta.
if __name__ == "__main__":
    log.setup(LOG_LEVEL, log_dir=LOG_DIR if parse_args().log else None)

import cv2

from actions.manager import ActionManager
from capture.screen import ScreenCapture
from core import devices
from core.battery import BatteryMonitor
from core.state_machine import StateMachine
from core.status import StatusPanel, formata_bateria
from dataset.recorder import DatasetRecorder
from vision.detector import Detector
from vision.worker import VisionWorker

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
    """Gravador do dataset, ou None se desligado. O banco é opcional; sem ele o samples.jsonl continua completo."""

    dataset_enabled = ai_collect if ai_collect is not None else DATASET_SAVE

    if not dataset_enabled:
        return None

    store = None

    if DATASET_DB_ENABLED:

        if not DATASET_DB_DSN:

            logger.error(
                "DATASET_DB_ENABLED ligado mas "
                "DATASET_DB_DSN vazio — gravando só em "
                "arquivo. Ver docs/dataset.md."
            )

        else:

            from dataset.store import PostgresStore

            try:

                store = PostgresStore(
                    DATASET_DB_DSN,
                    batch_size=DATASET_DB_BATCH,
                    schema=DATASET_DB_SCHEMA,
                ).connect()

            except Exception as error:

                # Banco fora não pode impedir a coleta: imagens não se recuperam depois.
                logger.error(
                    "Sem conexão com o banco (%s) — gravando "
                    "só em arquivo. Depois dá para importar "
                    "com tools/dataset_import.py.",
                    error,
                )

                store = None

    return DatasetRecorder(store=store)


def read_detections(vision):
    """(detecções, idade, frame, instante_do_frame). O frame é o que GEROU as detecções, não o mais recente — mantém imagem e rótulo consistentes no dataset."""

    frame, detections, frame_time = vision.get_input()

    lag = (
        max(0.0, time.monotonic() - frame_time)
        if frame_time
        else 0.0
    )

    return detections, lag, frame, frame_time


def run_loop(
    capture,
    vision,
    detector,
    state_machine,
    actions,
    battery,
    panel=None,
    auto_trainer=None,
):

    last_version = 0
    pause_started = None
    resume_floor = 0.0

    # Evita repetir o aviso de visão caída a cada quadro.
    vision_avisada = False

    def update_panel():
        if panel is not None:
            resumo = state_machine.summary()
            if pause_started is not None:
                resumo["estado"] = "PAUSADO PARA TREINO"
            panel.update(
                resumo,
                extra=(
                    f"{vision.get_fps():.1f} fps"
                    f" | {formata_bateria(battery.get())}"
                ),
                misses=detector.miss_report(),
            )

    while True:

        paused = auto_trainer is not None and auto_trainer.pause_requested
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
                logger.info("Bot pausado para o treino automático.")
            recorder = state_machine.recorder
            if not actions.is_busy() and (recorder is None or recorder.prepare_for_training()):
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

        actions.set_frame_size(largura_frame, altura_frame)

        detections, lag, detect_frame, detect_time = [], 0.0, None, 0.0
        if not paused:
            if VISION_FILTER_BY_STATE:
                vision.set_categories(state_machine.wanted_categories())

            floor = state_machine.frame_floor()
            if resume_floor:
                floor = max(floor, resume_floor)
            vision.set_frame_floor(floor)
            vision.set_frame(frame, timestamp)

            detections, lag, detect_frame, detect_time = (
                read_detections(vision)
            )

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

        if not paused and detect_frame is not None and detect_time > 0:
            state_machine.update(
                detections,
                lag,
                detect_frame,
                detect_time,
            )

        update_panel()

        # Sem janela, não há cópia nem desenho do frame.
        if not SHOW_AI_VISION:
            continue

        # Reduz antes de desenhar: `resize` já substitui a cópia e o
        # desenho toca só a resolução da janela (bem menor que o frame).
        # As detecções são convertidas pelo mesmo fator dentro do `draw`.
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

            # Frame já pequeno: cópia necessária porque `draw` escreve
            # no array recebido, e este é o buffer da captura.
            ai_frame = frame.copy()

        ai_frame = detector.draw(
            ai_frame,
            detections,
            {
                "capture_fps": capture.get_fps(),
                "detect_fps": vision.get_fps(),
                "detect_ms": vision.get_duration() * 1000,
                "lag": lag,

                # Leitura em memória: dumpsys custa ~56 ms e não pode entrar aqui.
                "battery": battery.get(),

                "cycle": state_machine.cycle_stats(),

                # Sem isto, overlay vazio não diz se o problema é
                # template, threshold ou estado errado.
                "state": "PAUSADO PARA TREINO" if paused else state_machine.state,
                "searched": detector.last_searched,
                "searchable": detector.last_total,
                "detections": len(detections),

                # Cobertura da pasta da resolução sobre o default.
                "coverage": detector.template_coverage(),

                # Overlay parado após uma ação é ESPERADO (worker
                # pulando frames de antes do efeito assentar), não defeito.
                "waiting_settle": vision.waiting_settle,
                "vision_error": (
                    None
                    if vision.is_alive()
                    else (
                        vision.last_error
                        or "thread da visão parada"
                    )
                ),
            },
            scale=escala_janela,
        )

        cv2.imshow(AI_WINDOW_NAME, ai_frame)

        # ESC só chega aqui, é esta janela que recebe teclas.
        if cv2.waitKey(1) & 0xFF == 27:
            break


def main(argv=None):

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

    detector = Detector(
        use_defaults=not args.layout_only,
        # O aprendizado por resolução independe da coleta do dataset.
        learn_only_actions=False,
    )

    vision = VisionWorker(detector)

    actions = ActionManager(device_id)

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
        )

    except KeyboardInterrupt:

        logger.info("Interrompido pelo usuário.")

    finally:

        if panel is not None:
            try:
                panel.close()
            finally:
                log.set_console_panel(None)

        logger.info("Encerrando EatVenture AI...")

        # Antes do resto: fecha a amostra pendente e grava o último lote no banco.
        if recorder:

            recorder.stop()

        battery.stop()

        vision.stop()

        detector.stop()

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
