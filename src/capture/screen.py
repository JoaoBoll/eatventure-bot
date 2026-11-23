"""ScreenCapture: stream H.264 do scrcpy-server, versioning, FPS cap."""

import random
from core.i18n import t
import re
import shlex
import socket
import subprocess
import threading
import time
import os
import posixpath

import av
import cv2

from core import devices, log
from core.metrics import RateMeter
from core.config import (
    CAPTURE_CONNECT_TIMEOUT,
    CAPTURE_MAX_FPS,
    CAPTURE_SOFTWARE_ENCODER,
    CAPTURE_START_TIMEOUT,
    SCRCPY_DEVICE_JAR,
    SCRCPY_PORT,
    SCRCPY_PATH,
    SCRCPY_SERVER_PATH,
    SCRCPY_SERVER_VERSION,
    SCRCPY_VIDEO_ENCODER,
    ADB_PATH,
)

logger = log.get("capture")
_PROCESS_PREFIX = "eatventure-scrcpy-"
_PROCESS_NAME = re.compile(rf"{_PROCESS_PREFIX}(legacy-)?([0-7][0-9a-f]{{7}})")
_SERVER_CLASS = "com.genymobile.scrcpy.Server"


class ScreenCapture:

    SERVER_VERSION = SCRCPY_SERVER_VERSION
    SERVER_PATH = SCRCPY_SERVER_PATH

    # Caminho no device, separado do que o scrcpy.exe usa.
    DEVICE_JAR = SCRCPY_DEVICE_JAR

    PORT = SCRCPY_PORT

    def __init__(self, serial=None):

        self.serial = serial  # Device; None = deixa adb decidir (requer 1 device)

        self.server_process = None
        self.socket = None
        self.codec = None

        self.scid = 0  # Server instance ID (scrcpy_<id> em scrcpy 2.0+)
        self.socket_name = "scrcpy"

        self.first_chunk = None  # Primeiro pedaço do stream (H.264 real, não descartável)
        self._forward_port = None
        self._server_pushed = False

        self.running = False
        self.capture_thread = None

        self.latest_frame = None
        self.latest_frame_time = 0.0

        self.version = 0  # Incrementa a cada frame (permite detectar novidade)

        # Frames por segundo que o device está entregando.
        self.rate = RateMeter()

        # Intervalo mínimo entre frames publicados (capping FPS)
        self._min_interval = (
            1.0 / CAPTURE_MAX_FPS
            if CAPTURE_MAX_FPS
            else 0.0
        )
        self._published_at = 0.0
        self.dropped = 0  # Frames descartados pelo teto

        self.condition = threading.Condition()

    def start(self):

        if self.running:
            return

        if self.capture_thread and self.capture_thread.is_alive():
            raise RuntimeError(t("messages.capture_still_stopping"))

        if self.socket or self.server_process or self._forward_port is not None or self._server_pushed:
            self.stop()

        server_version = self._resolve_server_version()

        with self.condition:
            self.latest_frame = None
            self.latest_frame_time = 0.0
        self._published_at = 0.0

        # Desde 2.0, o scid evita colisões com o espelho e outras capturas.
        # O servidor 1.x usa obrigatoriamente o socket fixo "scrcpy".
        self.scid = random.randint(0, 0x7FFFFFFF)
        self.socket_name = (
            f"scrcpy_{self.scid:08x}" if server_version >= (2, 0) else "scrcpy"
        )
        jar_base, jar_ext = posixpath.splitext(type(self).DEVICE_JAR)
        self.DEVICE_JAR = f"{jar_base}-{self.scid:08x}{jar_ext}"

        logger.info(
            t("messages.scrcpy_server_starting"),
            self.SERVER_VERSION,
            self.socket_name,
        )

        self._push_server()
        self._setup_forward()
        self._start_server()

        logger.info(t("messages.h264_connecting"))
        self.socket, self.first_chunk = self._connect(CAPTURE_CONNECT_TIMEOUT)

        if self.socket is None:

            raise RuntimeError(
                "\n".join(
                    [
                        t("messages.stream_read_timeout", CAPTURE_CONNECT_TIMEOUT=CAPTURE_CONNECT_TIMEOUT, value_2=self.PORT, value_3=self.socket_name),

                        t("messages.likely_causes"),

                        t("messages.screen_encoder_hint"),

                        t("messages.scrcpy_version_hint"),

                        t("messages.server_start_hint")
                        + " ".join(self._adb("shell", "cat"))
                        + t("messages.socket_check_hint"),
                    ]
                )
            )

        logger.info(t("messages.stream_opened"), len(self.first_chunk))

        self.codec = av.CodecContext.create("h264", "r")

        self.running = True
        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            name="capture",
            daemon=True,
        )
        self.capture_thread.start()
        logger.info(t("messages.capture_thread_started"))

        if not self._wait_first_frame(CAPTURE_START_TIMEOUT):

            raise RuntimeError(
                "\n".join(
                    [
                        t("messages.first_frame_timeout", CAPTURE_START_TIMEOUT=CAPTURE_START_TIMEOUT, value_2=self.PORT),

                        t("messages.likely_causes"),

                        t("messages.port_in_use_hint"),

                        t("messages.screen_off_hint"),

                        t("messages.scrcpy_version_hint"),
                    ]
                )
            )

    def _connect(self, timeout):
        """Conecta e aguarda primeiro pedaço válido (0 bytes = socket não existe ainda)."""

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
                self._fecha(candidato)
                time.sleep(0.15)
                continue

            # Distinção: 0 bytes = socket não existe; timeout = aguardando dados válidos

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
                    t("messages.stream_connected_attempt"),
                    tentativas,
                )

                return candidato, data

            self._fecha(candidato)

            if not morto:
                break

            time.sleep(0.15)

        logger.warning(
            t("messages.stream_attempts_exhausted"),
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

    def _capture_loop(self):
        # first_chunk é H.264 real, não descartável
        pendente = self.first_chunk

        self.first_chunk = None

        try:
            self._receive(pendente)
        except Exception:
            logger.exception("Falha inesperada na captura")
        finally:
            # Acorda quem espera mesmo se a conversão do frame falhar.
            with self.condition:
                self.running = False
                self.condition.notify_all()

    def _receive(self, pendente):

        stream = self.socket

        while self.running:

            if pendente is not None:

                data = pendente
                pendente = None

                if not self._decode(data):
                    break

                continue

            try:

                data = stream.recv(65536)

            except socket.timeout:

                continue

            except OSError as error:

                if self.running:

                    logger.warning(
                        t("messages.socket_disconnected"),
                        error,
                    )

                break

            if not data:

                if self.running:
                    logger.warning(
                        t("messages.remote_stream_closed"),
                        self.PORT,
                    )

                break

            if not self._decode(data):
                break

    def _decode(self, data):
        """Retorna False quando o stream terminou (EOF)."""

        try:

            for packet in self.codec.parse(data):

                for frame in self.codec.decode(packet):

                    self._publish(frame)

        except av.error.EOFError:

            logger.warning(t("messages.h264_eof"))

            return False

        except av.error.FFmpegError as error:

            logger.warning(t("messages.h264_error"), error)

        return True

    def _publish(self, frame):
        # Capping ANTES da conversão cara (YUV->BGR). Decodificação não é pulada (inter-frame)
        if self._min_interval:

            agora = time.monotonic()

            if (
                agora - self._published_at
                < self._min_interval
            ):

                self.dropped += 1

                return

            self._published_at = agora

        yuv = frame.to_ndarray()

        bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

        with self.condition:
            # Guarda só o frame mais novo (dataset recebe original, detector normaliza)
            self.latest_frame = bgr
            self.latest_frame_time = time.monotonic()

            self.version += 1

            self.condition.notify_all()

        self.rate.tick()

    def get_frame(self, since_version=None, timeout=1.0):
        """Retorna (frame, versão, timestamp). Com since_version, bloqueia até frame novo. Array é compartilhado."""

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
        return self.rate.rate()

    def _adb(self, *args):
        """Monta comando adb para device escolhido (sem -s, adb recusa com múltiplos devices)."""

        comando = [ADB_PATH]

        if self.serial:

            comando += ["-s", self.serial]

        return comando + list(args)

    def _cleanup_adb(self, *args, serial=None):
        command = self._adb(*args) if serial is None else [ADB_PATH, "-s", serial, *args]
        return subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=10.0,
        ).stdout

    @staticmethod
    def _proc_has(pid, token, field="cmdline"):
        return f"tr '\\000' '\\n' 2>/dev/null < /proc/{pid}/{field} | grep -Fqx -- {shlex.quote(token)}"

    def _previous_server_processes(self, owned_jar, legacy_jar, scids):
        try:
            output = self._cleanup_adb("shell", "ps", "-A", "-o", "PID,ARGS")
        except subprocess.CalledProcessError:
            # O toolbox de Android antigo só oferece o formato padrão de ps.
            output = self._cleanup_adb("shell", "ps")

        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            if re.fullmatch(r"[1-9]\d*", fields[0]):
                pid, args = fields[0], fields[1:]
            elif fields[0] == "shell" and re.fullmatch(r"[1-9]\d*", fields[1]):
                name = fields[-1]
                if _PROCESS_NAME.fullmatch(name) or name == _SERVER_CLASS:
                    pid, args = fields[1], [name]
                elif posixpath.basename(name) in {"app_process", "app_process32", "app_process64"}:
                    pid = fields[1]
                    args = self._cleanup_adb(
                        "shell", f"tr '\\000' '\\n' 2>/dev/null < /proc/{pid}/cmdline || :",
                    ).splitlines()
                else:
                    continue
            else:
                continue
            if not args:
                continue

            named = _PROCESS_NAME.fullmatch(args[0])
            if named:
                if not named[1]:
                    scids.add(named[2])
                yield pid, self._proc_has(pid, args[0])
                continue
            if _SERVER_CLASS not in args:
                continue
            scid = next((arg[5:] for arg in args if arg.startswith("scid=")), None)
            if scid in scids:
                yield pid, f"{self._proc_has(pid, _SERVER_CLASS)} && {self._proc_has(pid, f'scid={scid}')}"
                continue

            # Uma versão anterior pode ter apagado o JAR sem encerrar o processo.
            for classpath in self._cleanup_adb(
                "shell", f"tr '\\000' '\\n' 2>/dev/null < /proc/{pid}/environ | grep '^CLASSPATH=' || :",
            ).splitlines():
                jar = classpath.removeprefix("CLASSPATH=")
                match = owned_jar.fullmatch(jar)
                if match or jar == legacy_jar:
                    if match and scid == match[1]:
                        scids.add(scid)
                    yield pid, f"{self._proc_has(pid, _SERVER_CLASS)} && {self._proc_has(pid, classpath, 'environ')}"
                    break

    def cleanup_previous_sessions(self, hardware=None):
        """Encerra servidores e forwards do projeto, inclusive em aliases verificados."""
        if not self.serial:
            return

        jar = type(self).DEVICE_JAR
        base, extension = posixpath.splitext(jar)
        owned_jar = re.compile(rf"{re.escape(base)}-([0-7][0-9a-f]{{7}}){re.escape(extension)}")
        jar_glob = f"{shlex.quote(base)}-*{shlex.quote(extension)}"
        try:
            output = self._cleanup_adb(
                "shell",
                f"for jar in {shlex.quote(jar)} {jar_glob}; do "
                "if [ -f \"$jar\" ]; then printf '%s\\n' \"$jar\"; fi; done",
            )
            jars = [path for path in output.splitlines() if path == jar or owned_jar.fullmatch(path)]
            scids = {match[1] for path in jars if (match := owned_jar.fullmatch(path))}
            for pid, owns_process in self._previous_server_processes(owned_jar, jar, scids):
                self._cleanup_adb(
                    "shell",
                    f"if {owns_process}; then kill -TERM {pid} 2>/dev/null || :; "
                    f"for step in 1 2 3 4 5; do if ! ( {owns_process} ); then break; fi; sleep 0.1; done; "
                    f"if {owns_process}; then kill -KILL {pid}; fi; fi",
                )

            if scids:
                sockets = {f"localabstract:scrcpy_{scid}" for scid in scids}
                same_device = {self.serial: True}
                for line in self._cleanup_adb("forward", "--list").splitlines():
                    fields = line.split()
                    if len(fields) != 3:
                        continue
                    serial, port, remote = fields
                    if remote not in sockets or not re.fullmatch(r"tcp:[1-9]\d{0,4}", port):
                        continue
                    if serial == self.serial:
                        self._cleanup_adb("forward", "--remove", port)
                        continue
                    if serial not in same_device:
                        same_device[serial] = bool(hardware and devices.hardware_serial(serial) == hardware)
                    if same_device[serial]:
                        self._cleanup_adb("forward", "--remove", port, serial=serial)

            if jars:
                self._cleanup_adb("shell", "rm -f -- " + " ".join(map(shlex.quote, jars)))
        except (OSError, subprocess.SubprocessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise RuntimeError(t("messages.capture_previous_cleanup_failed", error=detail.strip())) from error

    def _push_server(self):

        if not self.SERVER_PATH:
            raise RuntimeError(
                t("messages.scrcpy_server_path_missing")
            )

        if not os.path.exists(self.SERVER_PATH):
            raise RuntimeError(
                t("messages.scrcpy_server_not_found", value_1=self.SERVER_PATH)
            )

        result = subprocess.run(
            self._adb("push", self.SERVER_PATH, self.DEVICE_JAR),
            capture_output=True,
            text=True,
            timeout=15.0,
        )

        if result.returncode != 0:
            raise RuntimeError(t("messages.scrcpy_upload_error") + (result.stderr or ""))

        self._server_pushed = True

    def _setup_forward(self):

        result = subprocess.run(
            self._adb(
                "forward",
                "--no-rebind",
                f"tcp:{type(self).PORT}",
                f"localabstract:{self.socket_name}",
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=10.0,
        )

        self.PORT = type(self).PORT or int(result.stdout.strip())
        self._forward_port = self.PORT

    def _resolve_server_version(self):
        if self.SERVER_VERSION is None:
            try:
                result = subprocess.run(
                    [SCRCPY_PATH, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError(t("messages.scrcpy_version_query_failed")) from error

            output = result.stdout + "\n" + result.stderr
            match = re.search(r"(?m)^scrcpy\s+(\S+)", output)
            if match is None:
                raise RuntimeError(t("messages.scrcpy_version_unrecognized"))
            self.SERVER_VERSION = match.group(1)

        match = re.match(r"(\d+)\.(\d+)", self.SERVER_VERSION)
        if match is None:
            raise RuntimeError(t("messages.invalid_scrcpy_version", value_1=self.SERVER_VERSION))
        version = tuple(map(int, match.groups()))
        if version < (1, 23):
            raise RuntimeError(
                t("messages.scrcpy_upgrade_required", value_1=self.SERVER_VERSION)
            )
        return version

    def _start_server(self):

        version = self._resolve_server_version()
        options = ["tunnel_forward=true", "control=false", "cleanup=false"]
        if CAPTURE_MAX_FPS:
            options.append(f"max_fps={CAPTURE_MAX_FPS}")
        if CAPTURE_SOFTWARE_ENCODER:
            options.append("video_encoder=c2.android.avc.encoder")
        if version >= (2, 0):
            options.extend((f"scid={self.scid:08x}", "audio=false", "video_codec=h264"))
        if SCRCPY_VIDEO_ENCODER:
            options.append(f"video_encoder={SCRCPY_VIDEO_ENCODER}")
        raw_option = "raw_stream" if version >= (2, 1) else "raw_video_stream"
        options.append(f"{raw_option}=true")

        command = self._adb(
            "shell",

            f"CLASSPATH={self.DEVICE_JAR}",

            "app_process",
            "/",
            f"--nice-name={_PROCESS_PREFIX}{'legacy-' if version < (2, 0) else ''}{self.scid:08x}",
            _SERVER_CLASS,
            self.SERVER_VERSION,
            *options,
        )

        self.server_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        threading.Thread(
            target=self._log_server_output,
            args=(self.server_process.stdout,),
            name="scrcpy-log",
            daemon=True,
        ).start()

    @staticmethod
    def _log_server_output(stream):
        if stream is None:
            return
        try:
            for line in stream:
                logger.info("scrcpy-server: %s", line.rstrip())
        finally:
            stream.close()

    def stop(self):
        logger.info(t("messages.capture_stopping"))

        with self.condition:
            self.running = False
            self.condition.notify_all()

        if self.socket:

            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            try:
                self.socket.close()

            except OSError:
                pass
            self.socket = None

        if self.capture_thread:
            self.capture_thread.join(timeout=2)
            if not self.capture_thread.is_alive():
                self.capture_thread = None

        self._cleanup_forward()

        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
                self.server_process.wait(timeout=2)
            self.server_process = None

        if self._server_pushed:
            try:
                subprocess.run(
                    self._adb("shell", "rm", self.DEVICE_JAR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                )
            except (OSError, subprocess.SubprocessError) as error:
                logger.warning(t("messages.server_cleanup_failed"), error)
            self._server_pushed = False

        logger.info(t("messages.capture_stopped"))

    def _cleanup_forward(self):

        if self._forward_port is None:
            return

        try:
            subprocess.run(
                self._adb("forward", "--remove", f"tcp:{self._forward_port}"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning(t("messages.tunnel_cleanup_failed"), error)
        self._forward_port = None
