"""
Integração: frame -> detector -> worker -> state machine -> toque.

    python tests/test_pipeline.py

Usa o VisionWorker, o Detector, a StateMachine e o
ActionManager DE VERDADE, com as threads de verdade. Só o
adb é falso, então nada é enviado a nenhum device.

Pega o tipo de erro que teste de unidade não pega: handoff
de frame entre threads, cálculo da idade da detecção, o
gate de "ação em andamento", e a conversão de coordenada
de frame para coordenada de toque.
"""

import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

from actions.android import AndroidActions      # noqa: E402
from actions.manager import ActionManager       # noqa: E402
from core import log                            # noqa: E402
from core.battery import BatteryMonitor         # noqa: E402
from core import state_machine as sm            # noqa: E402
from core.metrics import DurationMeter, RateMeter  # noqa: E402
from vision.detector import Detector            # noqa: E402
from vision.worker import VisionWorker          # noqa: E402

IMAGE = ROOT / "tests" / "images" / "eatventure.png"

# Onde o detector acha o botão de upgrade nessa tela.
# Confirmado por tests/test_detection.py.
UPGRADE_BOX = (914, 2194, 136, 134)


# =========================================================
# ADB FALSO
# =========================================================

class FakeAndroid(AndroidActions):

    def __init__(self, device_size=(1080, 2400)):

        super().__init__()

        self.device_size = device_size

        self.commands = []

    def _run(self, *args, timeout=10.0):

        if args[:3] == ("shell", "wm", "size"):

            return (
                f"Physical size: "
                f"{self.device_size[0]}x"
                f"{self.device_size[1]}\n"
            )

        self.commands.extend(self._expande(args))

        return ""

    @staticmethod
    def _expande(args):
        """
        Um comando adb pode carregar VÁRIOS `input`.

        tap_many e swipe_many mandam os N toques num único
        `adb shell "input tap ...; input tap ..."`, para não
        pagar N vezes o spawn do adb e a JVM do device. Aqui
        isso é desmontado de volta na forma palavra-por-palavra,
        que é o que as propriedades abaixo leem.

        Sem esta expansão o dublê registrava a rolagem em lote
        como um comando opaco, e `scrolls` via zero swipes num
        caminho que no device manda seis.
        """

        if len(args) != 2 or args[0] != "shell":
            return [args]

        if ";" not in args[1]:
            return [args]

        return [
            ("shell", *pedaco.split())
            for pedaco in args[1].split(";")
            if pedaco.strip()
        ]

    @property
    def taps(self):

        return [
            (int(command[3]), int(command[4]))
            for command in self.commands
            if command[:3] == ("shell", "input", "tap")
        ]

    @property
    def backs(self):
        """
        BACK nunca deveria aparecer: neste jogo ele sai do jogo.
        """

        return [
            command
            for command in self.commands
            if command[:3] == ("shell", "input", "keyevent")
            and command[3] == "4"
        ]

    @property
    def scrolls(self):
        """
        Swipes de rolagem: 'input swipe' com origem != destino.
        Devolve (y_inicial, y_final).
        """

        resultado = []

        for command in self.commands:

            if command[:3] != ("shell", "input", "swipe"):
                continue

            x1, y1, x2, y2, _ = (
                int(v) for v in command[3:8]
            )

            if (x1, y1) == (x2, y2):
                continue

            resultado.append((y1, y2))

        return resultado

    @property
    def holds(self):
        """
        Toques mantidos: 'input swipe' com origem == destino.
        Devolve (x, y, duração em segundos).
        """

        resultado = []

        for command in self.commands:

            if command[:3] != ("shell", "input", "swipe"):
                continue

            x1, y1, x2, y2, ms = (
                int(v) for v in command[3:8]
            )

            if (x1, y1) != (x2, y2):
                continue

            resultado.append((x1, y1, ms / 1000.0))

        return resultado


# =========================================================
# CAPTURA FALSA
# =========================================================

class FakeCapture:
    """
    Serve o mesmo frame algumas vezes e depois encerra,
    imitando o fim do stream.
    """

    def __init__(self, frame, frames=5):

        self.frame = frame

        self.frames = frames

        self.version = 0

        self.running = True

    def get_frame(self, since_version=None, timeout=None):

        if self.version >= self.frames:

            self.running = False

            return None, self.version, 0.0

        self.version += 1

        return self.frame, self.version, time.monotonic()

    def is_running(self):

        return self.running


# =========================================================
# HELPERS
# =========================================================

def build(device_size=(1080, 2400)):

    detector = Detector()

    vision = VisionWorker(detector)

    actions = ActionManager()

    actions.android = FakeAndroid(device_size)

    machine = sm.StateMachine(actions)

    return detector, vision, actions, machine


def wait_for_detections(vision, timeout=10.0):

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:

        detections, lag = vision.get_detections()

        if detections:

            return detections, lag

        time.sleep(0.02)

    return [], 0.0


# =========================================================
# TESTES
# =========================================================

def test_frame_vira_toque_no_lugar_certo():
    """
    O caminho completo, com as threads reais.
    """

    frame = cv2.imread(str(IMAGE))

    assert frame is not None, IMAGE

    _, vision, actions, machine = build()

    vision.start()

    actions.start()

    try:

        height, width = frame.shape[:2]

        actions.set_frame_size(width, height)

        vision.set_categories(machine.wanted_categories())

        vision.set_frame(frame)

        detections, lag = wait_for_detections(vision)

        assert detections, "worker não produziu detecção"

        # A idade é medida a partir do frame, não da consulta.
        assert 0.0 < lag < 5.0, lag

        categories = [d["category"] for d in detections]

        assert "upgrade" in categories, categories

        # Maior confiança primeiro.
        confidences = [d["confidence"] for d in detections]

        assert confidences == sorted(
            confidences,
            reverse=True,
        ), confidences

        machine.update(detections, lag)

        assert machine.state == sm.UPGRADE, machine.state

        # Espera a thread de ação drenar.
        deadline = time.monotonic() + 5.0

        while (
            actions.is_busy()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        x, y, box_width, box_height = UPGRADE_BOX

        expected = (
            x + box_width // 2,
            y + box_height // 2,
        )

        assert actions.android.taps == [expected], (
            actions.android.taps,
            expected,
        )

    finally:

        vision.stop()

        actions.stop()


def test_frame_reduzido_converte_coordenada():
    """
    Se o stream vier em resolução diferente do device, o
    toque tem que ser convertido. Antes não havia conversão
    nenhuma: o clique caía no lugar errado.
    """

    _, vision, actions, machine = build(
        device_size=(1080, 2400)
    )

    actions.start()

    try:

        # Detecção calculada num frame com metade do tamanho.
        actions.set_frame_size(540, 1200)

        detection = {
            "category": "upgrade",
            "name": "item_001.png",
            "confidence": 1.0,
            "color_similarity": 1.0,
            "min_threshold": 0.98,
            "x": 100,
            "y": 200,
            "width": 40,
            "height": 40,
        }

        actions.execute("upgrade", detection)

        deadline = time.monotonic() + 5.0

        while (
            actions.is_busy()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        # centro no frame = (120, 220); device é 2x maior.
        assert actions.android.taps == [(240, 440)], (
            actions.android.taps
        )

    finally:

        actions.stop()


def test_acao_longa_nao_bloqueia_o_loop():
    """
    O long press de comida dura 4 s. Antes ele congelava o
    loop principal inteiro; agora roda em paralelo.
    """

    _, _, actions, _ = build()

    actions.start()

    try:

        detection = {
            "category": "up_food",
            "name": "item_001.png",
            "confidence": 0.99,
            "color_similarity": 0.95,
            "min_threshold": 0.9,
            "x": 500,
            "y": 900,
            "width": 43,
            "height": 128,
        }

        actions.set_frame_size(1080, 2400)

        started = time.monotonic()

        assert actions.execute("upgrade_food", detection)

        # A chamada tem que voltar na hora, não em 4 s.
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, elapsed

        # E enquanto ela roda, nada mais entra na fila.
        assert actions.is_busy()

        assert not actions.execute("close", detection)

    finally:

        actions.stop()


def test_categoria_desconhecida_nao_derruba():

    _, _, actions, _ = build()

    actions.start()

    try:

        assert not actions.execute("nao_existe", None)

        assert actions.android.taps == []

    finally:

        actions.stop()


def test_loop_roda_sem_janela():
    """
    Com DISPLAY_MODE = "scrcpy" ou "none" não existe janela
    do OpenCV. O loop tem que rodar e encerrar limpo mesmo
    assim — e é justamente o caminho em que ninguém chama
    cv2.waitKey.
    """

    import main

    frame = cv2.imread(str(IMAGE))

    detector, vision, actions, machine = build()

    capture = FakeCapture(frame, frames=5)

    original = main.SHOW_AI_VISION

    main.SHOW_AI_VISION = False

    # O monitor de bateria roda em thread e fala adb — aqui o
    # adb é falso, então ele exercita o caminho todo sem device.
    battery = BatteryMonitor(actions.android, interval=0.05)

    vision.start()

    actions.start()

    battery.start()

    try:

        main.run_loop(
            capture,
            vision,
            detector,
            machine,
            actions,
            battery,
        )

        # Consumiu os frames e saiu quando o stream acabou.
        assert capture.version == 5, capture.version

        assert not capture.is_running()

    finally:

        main.SHOW_AI_VISION = original

        battery.stop()

        vision.stop()

        actions.stop()


def test_medidores_reportam_taxa():

    meter = RateMeter()

    for _ in range(20):

        meter.tick()

        time.sleep(0.01)

    rate = meter.rate()

    # ~100/s, com folga larga para não ficar frágil.
    assert 50 < rate < 200, rate

    duration = DurationMeter()

    for _ in range(40):

        duration.add(0.320)

    assert abs(duration.average() - 0.320) < 0.005, (
        duration.average()
    )


def test_proporcao_diferente_nao_encolhe_o_template():
    """
    O bug de "não detecta em outro aparelho".

    Num 1080x1920 contra uma referência 1080x2400, a LARGURA é
    idêntica e o ícone na tela tem exatamente o mesmo tamanho em
    pixels. Escalar pelo menor dos dois fatores encolhia todo
    template 20%:

        min(1080/1080, 1920/2400) = 0.80

    e nada passava do threshold. É proporção diferente, não
    resolução diferente.
    """

    from core.config import REFERENCE_HEIGHT, REFERENCE_WIDTH

    base = cv2.imread(str(IMAGE))

    assert base.shape[:2] == (
        REFERENCE_HEIGHT,
        REFERENCE_WIDTH,
    ), "o fixture deixou de estar na referência"

    # Mesma largura, menos altura: é o que um 16:9 mostra.
    frame = base[: int(REFERENCE_WIDTH * 16 / 9), :]

    largura, altura = frame.shape[1], frame.shape[0]

    escala = Detector._frame_scale(largura, altura)

    assert abs(escala - 1.0) < 0.01, (
        f"largura idêntica tem de dar escala 1.0, deu {escala}"
    )

    detector = Detector()

    deteccoes = detector.detect(frame)

    assert deteccoes, (
        "não detectou nada num aparelho de outra proporção — "
        "é exatamente o bug que a escala por lado curto corrige"
    )

    # E o comportamento antigo REALMENTE falhava aqui: sem
    # isto, o teste acima passaria mesmo com o bug de volta.
    import vision.detector as vd

    basis_original = vd.TEMPLATE_SCALE_BASIS
    steps_original = vd.TEMPLATE_SCALE_STEPS

    try:

        vd.TEMPLATE_SCALE_BASIS = "min"
        vd.TEMPLATE_SCALE_STEPS = (1.0,)

        assert Detector._frame_scale(largura, altura) < 0.85, (
            "o critério antigo deveria encolher o template"
        )

        assert not Detector().detect(frame), (
            "se o critério antigo também detecta, este teste "
            "não está medindo o que diz medir"
        )

    finally:

        vd.TEMPLATE_SCALE_BASIS = basis_original
        vd.TEMPLATE_SCALE_STEPS = steps_original


def test_resolucao_proporcional_continua_valendo():
    """
    Mudar de resolução MANTENDO a proporção é o caso fácil, e
    tem de continuar funcionando: os templates acompanham.
    """

    base = cv2.imread(str(IMAGE))

    esperado = Detector().detect(base)

    assert esperado, "nem detectou na referência"

    for largura, altura, fator in (
        (720, 1600, 2 / 3),
        (1440, 3200, 4 / 3),
    ):

        frame = cv2.resize(
            base,
            (largura, altura),
            interpolation=(
                cv2.INTER_AREA
                if fator < 1
                else cv2.INTER_LINEAR
            ),
        )

        escala = Detector._frame_scale(largura, altura)

        assert abs(escala - fator) < 0.01, (largura, escala)

        deteccoes = Detector().detect(frame)

        assert deteccoes, f"não detectou em {largura}x{altura}"

        # As MESMAS categorias da referência.
        assert (
            {d["category"] for d in deteccoes}
            == {d["category"] for d in esperado}
        ), (largura, altura, deteccoes)


def test_referencia_nao_paga_pelas_escalas_extra():
    """
    As escalas extra existem para aparelho fora da referência.
    No aparelho da referência elas seriam custo puro — o triplo
    de templates por passada sem nada em troca.
    """

    from core.config import REFERENCE_HEIGHT, REFERENCE_WIDTH

    detector = Detector()

    originais = len(detector.templates)

    # Nas duas orientações: o jogo roda deitado.
    for largura, altura in (
        (REFERENCE_WIDTH, REFERENCE_HEIGHT),
        (REFERENCE_HEIGHT, REFERENCE_WIDTH),
    ):

        assert Detector._is_reference(largura, altura)

        indice = detector._templates_for(largura, altura)

        assert indice is detector.by_category, (
            "referência não devia gerar variante de escala"
        )

        assert (
            sum(len(lista) for lista in indice.values())
            == originais
        )

    # Fora da referência, aí sim.
    fora = detector._templates_for(1080, 1920)

    assert (
        sum(len(lista) for lista in fora.values())
        > originais
    ), "esperava templates em mais de uma escala"


def test_teto_de_fps_corta_antes_de_converter():
    """
    O device manda 60 e o bot não usa 60. O corte tem de
    acontecer ANTES da conversão de cor, que é o trabalho caro
    (7.8 MB por frame), senão não economiza nada.
    """

    import numpy as np

    from capture.screen import ScreenCapture
    from core.config import CAPTURE_MAX_FPS

    if not CAPTURE_MAX_FPS:

        print("    (CAPTURE_MAX_FPS desligado, nada a testar)")

        return

    capture = ScreenCapture()

    convertidos = []

    class FrameFalso:
        """
        Acusa a conversão: se `to_ndarray` foi chamado, o frame
        pagou o caminho caro.
        """

        def to_ndarray(self):

            convertidos.append(1)

            return np.zeros(
                (2400 * 3 // 2, 1080),
                dtype=np.uint8,
            )

    # Alimenta ao DOBRO do teto.
    inicio = time.monotonic()
    enviados = 0

    while time.monotonic() - inicio < 1.5:

        capture._publish(FrameFalso())

        enviados += 1

        time.sleep(1.0 / (CAPTURE_MAX_FPS * 2))

    decorrido = time.monotonic() - inicio

    entregues = capture.version / decorrido

    assert entregues <= CAPTURE_MAX_FPS * 1.2, entregues

    assert entregues >= CAPTURE_MAX_FPS * 0.7, entregues

    assert capture.dropped > 0, "não descartou nada"

    # O que foi descartado NÃO pagou a conversão.
    assert len(convertidos) == capture.version, (
        len(convertidos),
        capture.version,
        "frame descartado ainda pagou o YUV->BGR",
    )


def test_piso_descarta_frame_velho_sem_perder_o_anterior():
    """
    Depois de uma ação, o frame que o worker tem na mão mostra a
    tela de ANTES do efeito dela: o _can_act descartaria o
    resultado de qualquer forma, então gastar uma passada nele é
    perda dupla — a passada em si, e o atraso até a primeira
    passada ÚTIL, que só começa depois dela.

    O que NÃO pode acontecer é o piso apagar a última leitura
    boa: o overlay ficaria vazio e pareceria detector travado.
    """

    frame = cv2.imread(str(IMAGE))

    _, vision, _, _ = build()

    vision.set_categories({"upgrade"})

    vision.start()

    try:

        # Uma leitura boa primeiro.
        deadline = time.monotonic() + 5.0

        while time.monotonic() < deadline:

            vision.set_frame(frame)

            deteccoes, _ = vision.get_detections()

            if deteccoes:
                break

            time.sleep(0.02)

        assert deteccoes, "nem detectou antes de testar o piso"

        antes = vision.detections_time

        # -------------------------------------------------
        # Piso no futuro: TODO frame de agora é velho.
        # -------------------------------------------------

        vision.set_frame_floor(time.monotonic() + 30.0)

        pulados = vision.skipped_stale

        for _ in range(30):

            vision.set_frame(frame)

            time.sleep(0.02)

        assert vision.skipped_stale > pulados, (
            "não descartou frame anterior ao piso"
        )

        # A leitura anterior continua de pé.
        ainda, _ = vision.get_detections()

        assert ainda == deteccoes, "apagou a última leitura boa"

        assert vision.detections_time == antes, (
            "publicou análise de frame que devia ter pulado"
        )

        assert vision.waiting_settle is True

        # E a thread não morreu pulando.
        assert vision.is_alive()

        # -------------------------------------------------
        # Piso liberado: volta a analisar.
        # -------------------------------------------------

        vision.set_frame_floor(0.0)

        deadline = time.monotonic() + 5.0

        while time.monotonic() < deadline:

            vision.set_frame(frame)

            if vision.detections_time > antes:
                break

            time.sleep(0.02)

        assert vision.detections_time > antes, (
            "não voltou a analisar depois de liberar o piso"
        )

        assert vision.waiting_settle is False

    finally:

        vision.stop()


def test_worker_reporta_fps_e_custo():
    """
    Os dois números do HUD vêm daqui, e são diferentes do
    FPS da captura.
    """

    frame = cv2.imread(str(IMAGE))

    _, vision, _, _ = build()

    # Poucas categorias = passada rápida.
    vision.set_categories({"upgrade"})

    vision.start()

    try:

        deadline = time.monotonic() + 2.0

        while time.monotonic() < deadline:

            vision.set_frame(frame)

            time.sleep(0.01)

        assert vision.get_fps() > 0.0, vision.get_fps()

        assert vision.get_duration() > 0.0, (
            vision.get_duration()
        )

        # Uma passada não pode custar mais que o intervalo
        # entre passadas.
        assert (
            vision.get_duration()
            <= 1.0 / vision.get_fps() + 0.05
        ), (vision.get_duration(), vision.get_fps())

    finally:

        vision.stop()


def test_hud_desenha_e_desliga():

    import vision.detector as vd

    frame = cv2.imread(str(IMAGE))

    detector = build()[0]

    detections = detector.detect(frame)

    stats = {
        "capture_fps": 59.4,
        "detect_fps": 3.1,
        "detect_ms": 318.0,
        "lag": 0.34,
    }

    sem_hud = detector.draw(frame.copy(), detections, None)

    com_hud = detector.draw(frame.copy(), detections, stats)

    assert (com_hud != sem_hud).any(), "HUD não desenhou"

    fps_original = vd.SHOW_FPS
    lag_original = vd.SHOW_DETECTION_LAG

    try:

        vd.SHOW_FPS = False
        vd.SHOW_DETECTION_LAG = False

        desligado = detector.draw(
            frame.copy(),
            detections,
            stats,
        )

        # Só o HUD sai; as caixas continuam.
        assert (desligado == sem_hud).all(), (
            "desligar o HUD mexeu em outra coisa"
        )

    finally:

        vd.SHOW_FPS = fps_original
        vd.SHOW_DETECTION_LAG = lag_original


def test_dismiss_toca_no_ponto_neutro():
    """
    A ação "dismiss" tem que virar toque MANTIDO no
    DISMISS_POINT, ignorando a detecção que a disparou.

    Ela não está em nenhuma regra hoje: NORMAL_RULES usa
    "upgrade_food" para up_food, por escolha do dono. Este
    teste chama o ActionManager direto de propósito — a ação
    continua disponível para voltar às regras, e código que
    ninguém exercita apodrece sem ninguém notar.
    """

    from core.config import (
        DISMISS_HOLD_DURATION,
        DISMISS_POINT,
        REFERENCE_HEIGHT,
        REFERENCE_WIDTH,
    )

    _, _, actions, _ = build()

    actions.start()

    try:

        actions.set_frame_size(
            REFERENCE_WIDTH,
            REFERENCE_HEIGHT,
        )

        # Detecção longe do ponto de dispensa, para provar
        # que o toque NÃO usa o centro dela.
        deteccao = {
            "category": "up_food",
            "name": "item_001.png",
            "confidence": 0.968,
            "color_similarity": 0.996,
            "min_threshold": 0.9,
            "x": 689,
            "y": 1348,
            "width": 43,
            "height": 128,
        }

        assert actions.execute("dismiss", deteccao)

        deadline = time.monotonic() + 5.0

        while (
            actions.is_busy()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        # É toque MANTIDO, não tap: um tap seco do adb às
        # vezes não fecha o painel.
        assert actions.android.taps == [], actions.android.taps

        assert actions.android.holds == [
            (
                DISMISS_POINT[0],
                DISMISS_POINT[1],
                DISMISS_HOLD_DURATION,
            )
        ], actions.android.holds

    finally:

        actions.stop()


def test_dismiss_converte_para_device_menor():
    """
    O ponto de dispensa é anotado na resolução de referência,
    então em outro device ele tem que escalar.
    """

    _, _, actions, _ = build(device_size=(540, 1200))

    actions.start()

    try:

        actions.set_frame_size(540, 1200)

        assert actions.execute(
            "dismiss",
            {
                "category": "up_food",
                "name": "item_001.png",
                "confidence": 0.97,
                "color_similarity": 0.99,
                "min_threshold": 0.9,
                "x": 100,
                "y": 200,
                "width": 40,
                "height": 40,
            },
        )

        deadline = time.monotonic() + 5.0

        while (
            actions.is_busy()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        # (10, 2200) na referência 1080x2400 -> metade disso.
        assert [
            (x, y) for x, y, _ in actions.android.holds
        ] == [(5, 1100)], actions.android.holds

    finally:

        actions.stop()


def test_hold_do_dismiss_e_curto():
    """
    Dispensar painel usa toque mantido CURTO. Se usasse a
    duração do upgrade de comida (4 s), o bot congelaria por
    4 s a cada painel aberto sem querer.
    """

    from core.config import (
        DISMISS_HOLD_DURATION,
        UPGRADE_FOOD_PRESS,
    )

    assert DISMISS_HOLD_DURATION < UPGRADE_FOOD_PRESS, (
        DISMISS_HOLD_DURATION,
        UPGRADE_FOOD_PRESS,
    )

    # Acima de um tap falho, e abaixo do limite de long press
    # do Android (~500 ms), para não disparar gesto de segurar.
    assert 0.15 <= DISMISS_HOLD_DURATION <= 0.5, (
        DISMISS_HOLD_DURATION
    )


def test_escape_rola_a_tela_para_baixo():
    """
    A sequência de escape tem que rolar a VISTA para baixo, o
    que no ActionManager é o swipe "up" (dedo para cima). Se a
    direção estiver invertida, o bot sobe a tela e o canto de
    baixo continua na barra de botões.
    """

    from core.config import (
        SCROLL_BOTTOM_DIRECTION,
        SCROLL_BOTTOM_SWIPES,
    )

    _, _, actions, _ = build()

    actions.start()

    try:

        actions.set_frame_size(1080, 2400)

        assert actions.execute("scroll_bottom", None) is True

        deadline = time.monotonic() + 15.0

        while (
            actions.is_busy()
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)

        rolagens = actions.android.scrolls

        assert len(rolagens) == SCROLL_BOTTOM_SWIPES, rolagens

        # Dedo para cima: y final MENOR que o inicial.
        assert SCROLL_BOTTOM_DIRECTION == "up", (
            SCROLL_BOTTOM_DIRECTION
        )

        for y1, y2 in rolagens:

            assert y2 < y1, (y1, y2)

        # E nenhum BACK, que fecharia o jogo.
        assert actions.android.backs == [], (
            actions.android.backs
        )

    finally:

        actions.stop()


def test_escape_nao_bloqueia_o_loop():
    """
    A sequência são 6 swipes com pausa: ~1.5 s. Roda na thread
    de ação, então execute() tem que voltar na hora.
    """

    _, _, actions, _ = build()

    actions.start()

    try:

        actions.set_frame_size(1080, 2400)

        inicio = time.monotonic()

        assert actions.execute("scroll_bottom", None) is True

        assert time.monotonic() - inicio < 0.5, (
            time.monotonic() - inicio
        )

        assert actions.is_busy()

    finally:

        actions.stop()


def test_back_nao_esta_disponivel():
    """
    Neste jogo o BACK sai do jogo. Não pode ser acionável por
    nome de ação.
    """

    _, _, actions, _ = build()

    actions.start()

    try:

        assert actions.execute("back", None) is False

        assert actions.android.backs == [], (
            actions.android.backs
        )

    finally:

        actions.stop()


def test_porta_nao_colide_com_o_scrcpy():
    """
    O espelho do scrcpy usa 27183-27199 por padrão. Se a nossa
    captura voltar para essa faixa, o bot passa a falhar de
    forma INTERMITENTE — quem perde a corrida pela porta conecta
    no túnel errado e o stream morre na hora.

    O sintoma no log é:
      WARN: Could not listen on port 27183, retrying on 27184
    """

    from core.config import SCRCPY_DEVICE_JAR, SCRCPY_PORT

    assert not (27183 <= SCRCPY_PORT <= 27199), (
        f"SCRCPY_PORT={SCRCPY_PORT} está na faixa default do "
        f"scrcpy.exe"
    )

    # E o jar no device tem que ser nosso: os dois davam push
    # no mesmo arquivo ao mesmo tempo, e o nosso stop() apagava.
    assert "scrcpy-server.jar" not in SCRCPY_DEVICE_JAR, (
        SCRCPY_DEVICE_JAR
    )


def test_socket_do_servidor_tem_scid():
    """
    No scrcpy 4.1 o socket abstrato é SEMPRE "scrcpy_<scid>".
    Encaminhar para "scrcpy" puro faz o adb aceitar a conexão
    TCP e devolver 0 bytes na hora — falha silenciosa que
    parecia "porta ocupada".
    """

    from capture.screen import ScreenCapture

    c = ScreenCapture()

    # Simula o que o start() faz, sem tocar no device.
    import random as _r

    c.scid = _r.randint(0, 0x7FFFFFFF)
    c.socket_name = f"scrcpy_{c.scid:08x}"

    assert c.socket_name != "scrcpy", c.socket_name

    assert c.socket_name.startswith("scrcpy_"), c.socket_name

    # 8 hex, e scid dentro de 31 bits (é o que o scrcpy usa).
    sufixo = c.socket_name.split("_")[1]

    assert len(sufixo) == 8, sufixo

    int(sufixo, 16)

    assert 0 <= c.scid <= 0x7FFFFFFF, c.scid


def test_timeout_de_conexao_maior_que_o_arranque():
    """
    Medido: o servidor leva ~1.4 s para servir o primeiro byte,
    e passa disso com o espelho do scrcpy rodando junto. Um
    timeout curto aqui reintroduz a falha intermitente.
    """

    from core.config import (
        CAPTURE_CONNECT_TIMEOUT,
        CAPTURE_START_TIMEOUT,
    )

    assert CAPTURE_CONNECT_TIMEOUT >= 8.0, (
        CAPTURE_CONNECT_TIMEOUT
    )

    assert CAPTURE_START_TIMEOUT >= 3.0, (
        CAPTURE_START_TIMEOUT
    )


# =========================================================
# RUNNER
# =========================================================

def main():

    log.setup("WARNING")

    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]

    failures = 0

    for test in tests:

        try:

            test()

            print(f"  ok    {test.__name__}")

        except AssertionError as error:

            failures += 1

            print(f"  FALHA {test.__name__}: {error}")

        except Exception as error:

            failures += 1

            print(
                f"  ERRO  {test.__name__}: "
                f"{type(error).__name__}: {error}"
            )

    print()

    if failures:

        print(f"{failures}/{len(tests)} falharam.")

        return 1

    print(f"{len(tests)}/{len(tests)} passaram.")

    return 0


if __name__ == "__main__":

    sys.exit(main())
