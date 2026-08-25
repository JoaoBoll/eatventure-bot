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
import time

import cv2

from actions.manager import ActionManager
from capture.screen import ScreenCapture
from core import devices, log
from core.battery import BatteryMonitor
from dataset.recorder import DatasetRecorder
from core.config import (
    AI_WINDOW_NAME,
    AI_WINDOW_POSITION,
    DATASET_DB_BATCH,
    DATASET_DB_DSN,
    DATASET_DB_ENABLED,
    DATASET_DB_SCHEMA,
    DEVICE_SERIAL,
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
from core.state_machine import StateMachine
from core.status import StatusPanel, formata_bateria
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
# DATASET
# =========================================================

def build_recorder():
    """
    Gravador do dataset, ou None se estiver desligado.

    O banco é opcional dentro do opcional: sem ele o dataset
    continua completo em arquivo, e o samples.jsonl é a fonte
    de verdade do treino.
    """

    if not DATASET_SAVE:
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

                # Banco fora não pode impedir a coleta: as
                # imagens são o que não se recupera depois.
                logger.error(
                    "Sem conexão com o banco (%s) — gravando "
                    "só em arquivo. Depois dá para importar "
                    "com tools/dataset_import.py.",
                    error,
                )

                store = None

    return DatasetRecorder(store=store)


# =========================================================
# LEITURA DAS DETECÇÕES
# =========================================================

def read_detections(vision):
    """
    (detecções, idade, frame, instante_do_frame).

    O frame é o que GEROU essas detecções, não o mais recente:
    é isso que mantém imagem e rótulo consistentes para o
    dataset.
    """

    frame, detections, frame_time = vision.get_input()

    lag = (
        max(0.0, time.monotonic() - frame_time)
        if frame_time
        else 0.0
    )

    return detections, lag, frame, frame_time


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
    panel=None,
):

    # Começa igual à versão inicial do ScreenCapture, então
    # a primeira espera é pelo primeiro frame de verdade.
    last_version = 0

    # A visão já caiu alguma vez? Só para não repetir o aviso
    # a cada quadro.
    vision_avisada = False

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

        # =================================================
        # A RESOLUÇÃO REAL DO FRAME
        # =================================================
        #
        # Antes aqui ia REFERENCE_WIDTH/HEIGHT, com a
        # justificativa de que "o detector recebe frames
        # normalizados". A premissa estava certa e a conclusão
        # errada: o detector recebe normalizado, mas o
        # VisionWorker agora devolve as detecções de volta no
        # espaço do frame REAL — é lá que elas estão quando
        # chegam aqui.
        #
        # Dizer "referência" para coordenada que está em
        # "frame real" faz o ActionManager aplicar uma regra de
        # três a mais. Num device 1080x2400 dava na mesma
        # (escala 1); em qualquer outro, todo clique saía
        # deslocado. Era esse o problema em dispositivos
        # diferentes.
        #
        # Com a resolução real, a conversão frame->device é
        # exata: o toque cai onde o objeto foi detectado.
        altura_frame, largura_frame = frame.shape[:2]

        actions.set_frame_size(largura_frame, altura_frame)

        # -------------------------------------------------
        # Envia frame para a IA
        # -------------------------------------------------

        if VISION_FILTER_BY_STATE:

            vision.set_categories(
                state_machine.wanted_categories()
            )

        # Frame capturado antes de a última ação assentar não
        # pode autorizar nada (o _can_act descarta), então o
        # worker também não deve gastar uma passada nele. Sem
        # isto, cada ação custava uma análise jogada fora MAIS o
        # atraso até a primeira análise útil, que só começava
        # depois dela.
        vision.set_frame_floor(state_machine.frame_floor())

        vision.set_frame(frame, timestamp)

        # -------------------------------------------------
        # Detecções (do frame que o worker terminou)
        # -------------------------------------------------

        # get_input em vez de get_detections: devolve também o
        # frame que PRODUZIU as detecções. Sem isso, o dataset
        # gravaria uma imagem de uma passada com os rótulos de
        # outra.
        detections, lag, detect_frame, detect_time = (
            read_detections(vision)
        )

        # -------------------------------------------------
        # A VISÃO ESTÁ VIVA?
        # -------------------------------------------------
        #
        # A thread do detector morrer não parava o programa: a
        # captura seguia a 60 fps, a janela seguia aberta e o
        # bot simplesmente nunca mais agia. Era o sintoma
        # relatado, e não havia uma linha no log dizendo isso.
        if not vision.is_alive() and not vision_avisada:

            logger.error(
                "A thread da visão não está rodando (%s) — "
                "não vão existir detecções e o bot não vai "
                "agir.",
                vision.last_error or "motivo desconhecido",
            )

            vision_avisada = True

        # -------------------------------------------------
        # STATE MACHINE
        # -------------------------------------------------
        #
        # A cada frame, sem intervalo mínimo. Quem decide QUANDO
        # agir é o cooldown da própria StateMachine, que já leva
        # em conta a idade do frame — pôr um segundo relógio
        # aqui só somava atraso à reação.
        state_machine.update(
            detections,
            lag,
            detect_frame,
            detect_time,
        )

        # -------------------------------------------------
        # PAINEL
        # -------------------------------------------------
        #
        # Depois do update: mostra o estado JÁ com o efeito
        # desta volta. Antes, mostraria sempre um frame
        # atrasado.
        #
        # Ele decide sozinho se é hora de redesenhar, então
        # chamar a cada volta não custa.
        if panel is not None:

            # A bateria vem da leitura em memória do
            # BatteryMonitor — `dumpsys` custa ~56 ms e não
            # pode entrar no loop. Sessão que morre por bateria
            # descarregada não deixa rastro no log: o bot só
            # para de agir.
            panel.update(
                state_machine.summary(),
                extra=(
                    f"{vision.get_fps():.1f} fps"
                    f" | {formata_bateria(battery.get())}"
                ),

                # Vazio com DETECTOR_DEBUG_MISSES desligado.
                misses=detector.miss_report(),
            )

        # -------------------------------------------------
        # AI VISION
        # -------------------------------------------------

        # Com a janela desligada não há cópia nem desenho:
        # o overlay era o único lugar que copiava o frame.
        if not SHOW_AI_VISION:
            continue

        # =================================================
        # REDUZIR ANTES DE DESENHAR
        # =================================================
        #
        # Mesma taxa de antes — um desenho por frame, sem
        # intervalo mínimo. O que mudou é o custo de cada um.
        #
        # A janela tem 500x900 e o frame tem 1080x2400. O
        # caminho antigo era: copiar 7.8 MB, rabiscar 2.6 Mpx e
        # mandar o `imshow` reduzir — três trabalhos em
        # resolução cheia para caber num quinto do tamanho.
        #
        # Reduzindo primeiro, o `resize` substitui a cópia (o
        # resultado já é array novo) e o desenho toca 0.45 Mpx.
        # As detecções são convertidas pelo mesmo fator dentro
        # do `draw`.
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

            # Frame já pequeno: aí a cópia é necessária, porque
            # o `draw` escreve no array que recebe e este é o
            # buffer compartilhado da captura.
            ai_frame = frame.copy()

        ai_frame = detector.draw(
            ai_frame,
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

                # O que o bot está procurando e o que achou:
                # sem isto, overlay vazio não diz se o
                # problema é o template, o threshold ou o
                # estado errado.
                "state": state_machine.state,
                "searched": detector.last_searched,
                "detections": len(detections),

                # Overlay parado logo depois de uma ação é
                # ESPERADO, não defeito: o worker está pulando
                # frames que mostram a tela de antes do efeito.
                # Sem esta linha, a pausa parece detector
                # travado.
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

    recorder = build_recorder()

    state_machine = StateMachine(actions, recorder)

    battery = BatteryMonitor(actions.android)

    # -----------------------------------------------------
    # PAINEL
    # -----------------------------------------------------
    #
    # O nome do device vem resolvido do adb, então é o serial
    # real que está sendo usado — inclusive quando veio da
    # pergunta interativa e não do config.
    panel = (
        StatusPanel(
            device_id,
            interval=STATUS_PANEL_INTERVAL,
        )
        if STATUS_PANEL
        else None
    )

    if panel is not None:

        # Sem isto, cada ação imprime uma linha de INFO e
        # empurra o painel para cima — as duas coisas
        # brigando pelo mesmo terminal.
        log.set_console_level("WARNING")

    try:

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
        )

    except KeyboardInterrupt:

        logger.info("Interrompido pelo usuário.")

    finally:

        # Antes de qualquer log: devolve o cursor para baixo do
        # bloco, senão as linhas de encerramento escrevem em
        # cima do painel.
        if panel is not None:

            panel.close()

            log.set_console_level(LOG_LEVEL)

        logger.info("Encerrando EatVenture AI...")

        # Antes do resto: fecha a amostra pendente e grava o
        # último lote no banco.
        if recorder:

            recorder.stop()

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
