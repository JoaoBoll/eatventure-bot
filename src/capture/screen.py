import socket
import subprocess
import threading
import time

import av
import cv2


class ScreenCapture:

    SERVER_VERSION = "4.1"
    SERVER_PATH = r"C:\scrcpy\scrcpy-server"

    PORT = 27183

    def __init__(self):

        self.server_process = None
        self.socket = None
        self.codec = None

        self.running = False
        self.capture_thread = None

        self.latest_frame = None
        self.frame_lock = threading.Lock()

    # =====================================================
    # START
    # =====================================================

    def start(self):

        print("Iniciando scrcpy-server...")

        self._cleanup_forward()

        self._push_server()

        self._setup_forward()

        self._start_server()

        time.sleep(0.5)

        print("Conectando ao stream H.264...")

        self.socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        self.socket.settimeout(2)

        start_time = time.time()

        while True:

            try:

                self.socket.connect(
                    (
                        "127.0.0.1",
                        self.PORT
                    )
                )

                break

            except ConnectionRefusedError:

                if time.time() - start_time > 10:

                    raise RuntimeError(
                        "Não foi possível conectar "
                        "ao stream do scrcpy."
                    )

                time.sleep(0.1)

        print("Socket conectado!")

        # =================================================
        # DECODER
        # =================================================

        self.codec = av.CodecContext.create(
            "h264",
            "r"
        )

        print("Decoder H.264 criado.")

        # =================================================
        # THREAD DE CAPTURA
        # =================================================

        self.running = True

        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True
        )

        self.capture_thread.start()

        print("Thread de captura iniciada.")

    # =====================================================
    # CAPTURE LOOP
    # =====================================================

    def _capture_loop(self):

        while self.running:

            try:

                data = self.socket.recv(
                    65536
                )

            except socket.timeout:

                continue

            except OSError:

                break

            if not data:

                break

            try:

                packets = self.codec.parse(
                    data
                )

                for packet in packets:

                    frames = self.codec.decode(
                        packet
                    )

                    for frame in frames:

                        # ---------------------------------
                        # YUV → BGR
                        # ---------------------------------

                        yuv = frame.to_ndarray()

                        bgr = cv2.cvtColor(
                            yuv,
                            cv2.COLOR_YUV2BGR_I420
                        )

                        # ---------------------------------
                        # Guarda SOMENTE o frame mais novo
                        # ---------------------------------

                        with self.frame_lock:

                            self.latest_frame = bgr

            except av.error.EOFError:

                break

            except av.error.FFmpegError as e:

                print(
                    f"Erro H264: {e}"
                )

                continue

    # =====================================================
    # GET FRAME
    # =====================================================

    def get_frame(self):

        with self.frame_lock:

            if self.latest_frame is None:
                return None

            return self.latest_frame.copy()

    # =====================================================
    # PUSH SERVER
    # =====================================================

    def _push_server(self):

        result = subprocess.run(
            [
                "adb",
                "push",
                self.SERVER_PATH,
                "/data/local/tmp/scrcpy-server.jar",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:

            raise RuntimeError(
                "Erro ao enviar scrcpy-server:\n"
                + result.stderr
            )

    # =====================================================
    # FORWARD
    # =====================================================

    def _setup_forward(self):

        subprocess.run(
            [
                "adb",
                "forward",
                f"tcp:{self.PORT}",
                "localabstract:scrcpy",
            ],
            check=True,
        )

    # =====================================================
    # SERVER
    # =====================================================

    def _start_server(self):

        command = [
            "adb",
            "shell",

            "CLASSPATH=/data/local/tmp/scrcpy-server.jar",

            "app_process",
            "/",

            "com.genymobile.scrcpy.Server",

            self.SERVER_VERSION,

            "tunnel_forward=true",

            "audio=false",

            "control=false",

            "cleanup=false",

            "raw_stream=true",

            "video_codec=h264",
        ]

        self.server_process = subprocess.Popen(
            command,

            stdout=subprocess.DEVNULL,

            stderr=subprocess.DEVNULL,
        )

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        print("Encerrando captura...")

        self.running = False

        # -------------------------------------------------
        # Socket
        # -------------------------------------------------

        if self.socket:

            try:
                self.socket.close()

            except Exception:
                pass

            self.socket = None

        # -------------------------------------------------
        # Thread
        # -------------------------------------------------

        if self.capture_thread:

            self.capture_thread.join(
                timeout=2
            )

            self.capture_thread = None

        # -------------------------------------------------
        # Forward
        # -------------------------------------------------

        self._cleanup_forward()

        # -------------------------------------------------
        # Server
        # -------------------------------------------------

        if self.server_process:

            self.server_process.terminate()

            try:

                self.server_process.wait(
                    timeout=2
                )

            except subprocess.TimeoutExpired:

                self.server_process.kill()

            self.server_process = None

        # -------------------------------------------------
        # Remove server
        # -------------------------------------------------

        subprocess.run(
            [
                "adb",
                "shell",
                "rm",
                "/data/local/tmp/scrcpy-server.jar",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        print("Captura encerrada.")

    # =====================================================
    # CLEANUP
    # =====================================================

    def _cleanup_forward(self):

        subprocess.run(
            [
                "adb",
                "forward",
                "--remove",
                f"tcp:{self.PORT}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )