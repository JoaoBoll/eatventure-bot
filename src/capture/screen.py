"""
Captura o vídeo do device pelo scrcpy-server.

Mudanças em relação à versão anterior:

1. FRAME VERSIONADO + espera com bloqueio.
   O loop principal fazia `while True` sem pausa, girando a
   100% de CPU e copiando ~7.8 MB por volta mesmo quando o
   frame era o mesmo. Agora ele dorme até existir frame novo.

2. UMA cópia em vez de três.
   Eram: get_frame() copiava, set_frame() guardava a
   referência, o worker copiava de novo. O decoder já cria
   um array novo por frame e ninguém escreve nele, então
   a referência pode ser compartilhada. Só o overlay
   (que desenha em cima) copia.

3. Timestamp por frame, para a StateMachine saber a idade
   da detecção.
"""

import random
import socket
import subprocess
import threading
import time

import av
import cv2

from core import log
from core.metrics import RateMeter
from core.config import (
    CAPTURE_CONNECT_TIMEOUT,
    CAPTURE_START_TIMEOUT,
    SCRCPY_DEVICE_JAR,
    SCRCPY_PORT,
    SCRCPY_SERVER_PATH,
    SCRCPY_SERVER_VERSION,
)

logger = log.get("capture")


class ScreenCapture:

    SERVER_VERSION = SCRCPY_SERVER_VERSION
    SERVER_PATH = SCRCPY_SERVER_PATH

    # Caminho no device, separado do que o scrcpy.exe usa.
    DEVICE_JAR = SCRCPY_DEVICE_JAR

    PORT = SCRCPY_PORT

    def __init__(self, serial=None):

        # Device escolhido. None = deixa o adb decidir, o que
        # só funciona com UM device conectado.
        self.serial = serial

        self.server_process = None
        self.socket = None
        self.codec = None

        # Id da instância do servidor.
        #
        # No scrcpy 4.1 o socket abstrato é SEMPRE
        # "scrcpy_<scid em 8 hex>" — não existe um "scrcpy"
        # puro. Encaminhar para o nome sem scid faz o adb
        # aceitar a conexão TCP e devolver EOF na hora, o que
        # aparecia como "Stream fechou do outro lado (0 bytes)".
        self.scid = 0
        self.socket_name = "scrcpy"

        # Primeiro pedaço do stream, obtido durante a conexão.
        # Não pode ser descartado: é H.264 de verdade.
        self.first_chunk = None

        self.running = False
        self.capture_thread = None

        self.latest_frame = None
        self.latest_frame_time = 0.0

        # Incrementa a cada frame decodificado. É o que
        # permite ao consumidor saber se há coisa nova.
        self.version = 0

        # Frames por segundo que o device está entregando.
        self.rate = RateMeter()

        self.condition = threading.Condition()

    # =====================================================
    # START
    # =====================================================

    def start(self):

        # Um scid novo por execução: o socket abstrato do
        # servidor é "scrcpy_<scid>", e um id próprio garante
        # que não colidimos com o espelho nem com sobras de
        # execuções anteriores.
        self.scid = random.randint(0, 0x7FFFFFFF)
        self.socket_name = f"scrcpy_{self.scid:08x}"

        logger.info(
            "Iniciando scrcpy-server (socket @%s)...",
            self.socket_name,
        )

        self._cleanup_forward()

        self._push_server()

        self._setup_forward()

        self._start_server()

        logger.info("Conectando ao stream H.264...")

        self.socket, self.first_chunk = self._connect(
            CAPTURE_CONNECT_TIMEOUT
        )

        if self.socket is None:

            raise RuntimeError(
                "\n".join(
                    [
                        "Não conseguiu ler o stream em "
                        f"{CAPTURE_CONNECT_TIMEOUT:.0f}s "
                        f"(porta {self.PORT}, socket "
                        f"@{self.socket_name}).",

                        "Causas prováveis:",

                        "  - a tela do device está desligada "
                        "(o encoder captura o display)",

                        "  - SCRCPY_SERVER_VERSION não bate "
                        "com o scrcpy-server instalado",

                        "  - servidor não subiu: rode "
                        + " ".join(self._adb("shell", "cat"))
                        + " /proc/net/unix | grep scrcpy "
                        "e veja se o socket aparece",
                    ]
                )
            )

        logger.info(
            "Stream aberto (%d bytes iniciais).",
            len(self.first_chunk),
        )

        # =================================================
        # DECODER
        # =================================================

        self.codec = av.CodecContext.create("h264", "r")

        # =================================================
        # THREAD DE CAPTURA
        # =================================================

        self.running = True

        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            name="capture",
            daemon=True,
        )

        self.capture_thread.start()

        logger.info("Thread de captura iniciada.")

        # =================================================
        # PRIMEIRO FRAME
        # =================================================
        #
        # Sem esta checagem, túnel errado virava só
        # "Stream encerrado" milissegundos depois, sem motivo.
        #

        if not self._wait_first_frame(CAPTURE_START_TIMEOUT):

            raise RuntimeError(
                "\n".join(
                    [
                        f"Nenhum frame em "
                        f"{CAPTURE_START_TIMEOUT:.0f}s na "
                        f"porta {self.PORT}.",

                        "Causas prováveis:",

                        "  - outro processo usando a porta "
                        "(o espelho do scrcpy usa "
                        "27183-27199 por padrão)",

                        "  - a tela do device está desligada",

                        "  - SCRCPY_SERVER_VERSION não bate "
                        "com o scrcpy-server instalado",
                    ]
                )
            )

    def _connect(self, timeout):
        """
        Conecta e só devolve o socket depois de receber o
        primeiro pedaço de verdade.

        O adb aceita a conexão TCP e SÓ DEPOIS tenta abrir o
        socket abstrato no device. Se o servidor ainda não
        criou o socket, o resultado é 0 bytes — "conectou" não
        prova nada. Medido: o servidor leva ~1.4 s entre subir
        e começar a servir.

        Devolve (socket, primeiro_pedaco) ou (None, None).
        """

        deadline = time.monotonic() + timeout

        tentativas = 0

        while time.monotonic() < deadline:

            tentativas += 1

            candidato = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM,
            )

            candidato.settimeout(1.0)

            try:

                candidato.connect(("127.0.0.1", self.PORT))

            except OSError:

                # Nem conectou: adb ainda não está pronto.
                self._fecha(candidato)

                time.sleep(0.15)

                continue

            # -------------------------------------------------
            # Conectou. Agora INSISTE no mesmo socket.
            # -------------------------------------------------
            #
            # Distinção que importa:
            #
            #   0 bytes  -> o socket abstrato não existe ainda;
            #               fecha e tenta de novo
            #   timeout  -> o servidor ACEITOU e só não mandou
            #               nada ainda; fechar aqui joga fora
            #               uma conexão boa e derruba o
            #               servidor, que aceita um cliente só
            #
            # Com o espelho do scrcpy rodando o device fica
            # ocupado e o primeiro frame passa de 1 s.
            #

            morto = False

            while time.monotonic() < deadline:

                try:

                    data = candidato.recv(65536)

                except socket.timeout:

                    continue

                except OSError:

                    morto = True

                    break

                if not data:

                    morto = True

                    break

                candidato.settimeout(2)

                logger.debug(
                    "Stream respondeu na tentativa %d.",
                    tentativas,
                )

                return candidato, data

            if not morto:
                break

            self._fecha(candidato)

            time.sleep(0.15)

        logger.warning(
            "Sem stream após %d tentativas.",
            tentativas,
        )

        return None, None

    @staticmethod
    def _fecha(sock):

        try:
            sock.close()

        except OSError:
            pass

    def _wait_first_frame(self, timeout):

        with self.condition:

            return self.condition.wait_for(
                lambda: (
                    self.latest_frame is not None
                    or not self.running
                ),
                timeout=timeout,
            ) and self.latest_frame is not None

    # =====================================================
    # CAPTURE LOOP
    # =====================================================

    def _capture_loop(self):

        # O pedaço obtido na conexão é H.264 de verdade: se
        # fosse descartado, faltaria o início do stream.
        pendente = self.first_chunk

        self.first_chunk = None

        while self.running:

            if pendente is not None:

                data = pendente
                pendente = None

                self._decode(data)

                continue

            try:

                data = self.socket.recv(65536)

            except socket.timeout:

                continue

            except OSError as error:

                if self.running:

                    logger.warning(
                        "Socket caiu: %s",
                        error,
                    )

                break

            if not data:

                logger.warning(
                    "Stream fechou do outro lado (0 bytes). "
                    "Porta %d ocupada por outro processo, ou "
                    "servidor encerrou.",
                    self.PORT,
                )

                break

            if not self._decode(data):
                break

        # Acorda quem estiver esperando frame.
        with self.condition:

            self.running = False

            self.condition.notify_all()

    def _decode(self, data):
        """
        Devolve False quando o stream terminou de vez.
        """

        try:

            for packet in self.codec.parse(data):

                for frame in self.codec.decode(packet):

                    self._publish(frame)

        except av.error.EOFError:

            logger.warning("Decoder H264 recebeu EOF.")

            return False

        except av.error.FFmpegError as error:

            logger.warning("Erro H264: %s", error)

        return True

    def _publish(self, frame):

        yuv = frame.to_ndarray()

        bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

        with self.condition:

            # Guarda SOMENTE o frame mais novo.
            self.latest_frame = bgr
            self.latest_frame_time = time.monotonic()

            self.version += 1

            self.condition.notify_all()

        self.rate.tick()

    # =====================================================
    # GET FRAME
    # =====================================================

    def get_frame(self, since_version=None, timeout=1.0):
        """
        Devolve (frame, versão, timestamp).

        Com since_version, BLOQUEIA até aparecer um frame
        diferente desse (ou até o timeout). É o que substitui
        o busy-wait do loop principal.

        O array devolvido é compartilhado: quem for desenhar
        em cima precisa copiar.
        """

        with self.condition:

            if since_version is not None:

                self.condition.wait_for(
                    lambda: (
                        self.version != since_version
                        or not self.running
                    ),
                    timeout=timeout,
                )

            if self.latest_frame is None:

                return None, self.version, 0.0

            return (
                self.latest_frame,
                self.version,
                self.latest_frame_time,
            )

    def is_running(self):

        return self.running

    def get_fps(self):
        """
        Frames por segundo chegando do device.
        """

        return self.rate.rate()

    # =====================================================
    # ADB
    # =====================================================

    def _adb(self, *args):
        """
        Monta um comando adb já apontado para o device escolhido.

        Sem o -s, com dois devices na lista (o mesmo celular por
        USB e por wifi, por exemplo) o adb recusa TODA chamada
        com "more than one device". O sintoma aparecia longe
        daqui — o push falhava e o erro lido era "não conectou
        no stream".
        """

        comando = ["adb"]

        if self.serial:

            comando += ["-s", self.serial]

        return comando + list(args)

    # =====================================================
    # PUSH SERVER
    # =====================================================

    def _push_server(self):

        result = subprocess.run(
            self._adb(
                "push",
                self.SERVER_PATH,
                self.DEVICE_JAR,
            ),
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:

            raise RuntimeError(
                "Erro ao enviar scrcpy-server:\n"
                + (result.stderr or "")
            )

    # =====================================================
    # FORWARD
    # =====================================================

    def _setup_forward(self):

        subprocess.run(
            self._adb(
                "forward",
                f"tcp:{self.PORT}",
                f"localabstract:{self.socket_name}",
            ),
            check=True,
        )

    # =====================================================
    # SERVER
    # =====================================================

    def _start_server(self):

        command = self._adb(
            "shell",

            f"CLASSPATH={self.DEVICE_JAR}",

            "app_process",
            "/",

            "com.genymobile.scrcpy.Server",

            self.SERVER_VERSION,

            # Obrigatório no scrcpy 4.1: define o nome do
            # socket abstrato como "scrcpy_<scid>".
            f"scid={self.scid:08x}",

            "tunnel_forward=true",

            "audio=false",

            "control=false",

            "cleanup=false",

            "raw_stream=true",

            "video_codec=h264",
        )

        self.server_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        logger.info("Encerrando captura...")

        with self.condition:

            self.running = False

            self.condition.notify_all()

        # -------------------------------------------------
        # Socket
        # -------------------------------------------------

        if self.socket:

            try:
                self.socket.close()

            except OSError:
                pass

            self.socket = None

        # -------------------------------------------------
        # Thread
        # -------------------------------------------------

        if self.capture_thread:

            self.capture_thread.join(timeout=2)

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

                self.server_process.wait(timeout=2)

            except subprocess.TimeoutExpired:

                self.server_process.kill()

            self.server_process = None

        # -------------------------------------------------
        # Remove server
        # -------------------------------------------------

        subprocess.run(
            self._adb(
                "shell",
                "rm",
                self.DEVICE_JAR,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        logger.info("Captura encerrada.")

    # =====================================================
    # CLEANUP
    # =====================================================

    def _cleanup_forward(self):

        subprocess.run(
            self._adb(
                "forward",
                "--remove",
                f"tcp:{self.PORT}",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
