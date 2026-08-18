import subprocess
import cv2

from capture.screen import ScreenCapture
from vision.detector import Detector
from vision.worker import VisionWorker
from actions.manager import ActionManager
from core.state_machine import StateMachine

from core.config import (
    SHOW_AI_VISION,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
)


SCRCPY_PATH = r"C:\scrcpy\scrcpy.exe"

AI_WINDOW = "EatVenture - AI"


# =========================================================
# ADB
# =========================================================

def get_device_id():

    result = subprocess.run(
        [
            "adb",
            "devices",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    devices = []

    for line in result.stdout.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("List of devices"):
            continue

        parts = line.split()

        if len(parts) >= 2:

            device_id = parts[0]
            status = parts[1]

            if status == "device":

                devices.append(
                    device_id
                )

    if not devices:

        raise RuntimeError(
            "Nenhum dispositivo Android encontrado."
        )

    if len(devices) > 1:

        raise RuntimeError(
            "Mais de um dispositivo Android conectado:\n"
            + "\n".join(devices)
        )

    return devices[0]


# =========================================================
# SCRCPY
# =========================================================

def start_scrcpy(device_id):

    window_title = (
        f"EatVenture ({device_id})"
    )

    print(
        f"Iniciando {window_title}..."
    )

    return subprocess.Popen(
        [
            SCRCPY_PATH,

            "--serial",
            device_id,

            "--window-title",
            window_title,
        ]
    )


# =========================================================
# INICIALIZAÇÃO
# =========================================================

device_id = get_device_id()

scrcpy_process = start_scrcpy(
    device_id
)

capture = ScreenCapture()

detector = Detector(
    threshold=0.80,
    color_threshold=0.85,

    category_thresholds={
        "build": 0.95,
        "new_point": 0.90,
        "food": 0.90,
        "upgrade": 0.98,
        "up_food": 0.90,
        "up_upgrade": 0.90,
        "close": 0.85,

    }
)

vision = VisionWorker(
    detector=detector,

    # 20 análises por segundo
    interval=0.05,
)

action_manager = ActionManager()

state_machine = StateMachine(
    action_manager
)


try:

    # =====================================================
    # CAPTURE
    # =====================================================

    capture.start()

    # =====================================================
    # VISION WORKER
    # =====================================================

    vision.start()

    # =====================================================
    # AI WINDOW
    # =====================================================

    if SHOW_AI_VISION:

        cv2.namedWindow(
            AI_WINDOW,
            cv2.WINDOW_NORMAL
        )

        cv2.resizeWindow(
            AI_WINDOW,
            WINDOW_WIDTH,
            WINDOW_HEIGHT
        )

        cv2.moveWindow(
            AI_WINDOW,
            600,
            50
        )

    # =====================================================
    # LOOP
    # =====================================================

    while True:

        # -------------------------------------------------
        # Frame mais recente
        # -------------------------------------------------

        frame = capture.get_frame()

        if frame is None:
            continue

        # -------------------------------------------------
        # Envia frame para a IA
        # -------------------------------------------------

        vision.set_frame(
            frame
        )

        # -------------------------------------------------
        # Pega as detecções atuais
        # -------------------------------------------------

        detections = (
            vision.get_detections()
        )

        # -------------------------------------------------
        # STATE MACHINE
        # -------------------------------------------------

        state_machine.update(
            detections
        )

        # -------------------------------------------------
        # AI VISION
        # -------------------------------------------------

        if SHOW_AI_VISION:

            ai_frame = frame.copy()

            ai_frame = detector.draw(
                ai_frame,
                detections
            )

            cv2.imshow(
                AI_WINDOW,
                ai_frame
            )

        # -------------------------------------------------
        # ESC
        # -------------------------------------------------

        if cv2.waitKey(1) & 0xFF == 27:
            break


except KeyboardInterrupt:

    print(
        "\nPrograma interrompido pelo usuário."
    )


finally:

    print(
        "Encerrando EatVenture AI..."
    )

    # =====================================================
    # VISION
    # =====================================================

    vision.stop()

    # =====================================================
    # CAPTURE
    # =====================================================

    capture.stop()

    # =====================================================
    # WINDOWS
    # =====================================================

    cv2.destroyAllWindows()

    # =====================================================
    # SCRCPY
    # =====================================================

    if scrcpy_process:

        scrcpy_process.terminate()

        try:

            scrcpy_process.wait(
                timeout=2
            )

        except subprocess.TimeoutExpired:

            scrcpy_process.kill()