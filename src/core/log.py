"""Log central; timestamp com milissegundos permite separar "detectou errado" de "detectou tarde"."""

import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path


class OutputCopy:

    def __init__(self, stream, handler):
        self.stream = stream
        self.handler = handler

    def write(self, text):
        self.handler.acquire()
        try:
            if self.handler.stream is not None:
                self.handler.stream.write(text)
                self.handler.flush()
        finally:
            self.handler.release()
        return self.stream.write(text)

    def flush(self):
        self.handler.flush()
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class ConsoleHandler(logging.StreamHandler):

    def __init__(self, stream=None):
        super().__init__(stream)
        self.panel = None

    def emit(self, record):
        try:
            panel = self.panel
            if panel is not None and panel.ansi:
                message = self.format(record)
                if panel.write_log(
                    message,
                    force=record.levelno >= logging.WARNING,
                ):
                    return
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)
            return

        super().emit(record)


def setup(level="INFO", log_dir=None):

    root = logging.getLogger("eatventure")

    console_level = getattr(logging, str(level).upper(), logging.INFO)
    message_format = "%(asctime)s.%(msecs)03d %(levelname).1s [%(name)s] %(message)s"

    if not root.handlers:
        root.setLevel(console_level)
        handler = ConsoleHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(message_format, datefmt="%H:%M:%S")
        )
        root.addHandler(handler)

    if log_dir is not None and not any(
        isinstance(handler, logging.FileHandler) for handler in root.handlers
    ):
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = directory / f"eatventure_{timestamp}_{os.getpid()}.log"
        handler = logging.FileHandler(
            path, mode="a", encoding="utf-8", errors="backslashreplace",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(message_format, datefmt="%Y-%m-%d %H:%M:%S")
        )
        root.setLevel(logging.DEBUG)
        root.propagate = False
        set_console_level(console_level)
        root.addHandler(handler)

        # O console e o painel usam o stream original para não duplicar logs nem ANSI.
        sys.stdout = OutputCopy(sys.stdout, handler)
        sys.stderr = OutputCopy(sys.stderr, handler)
        root.info("Log completo desta execução: %s", path)

    return root


def console_stream():
    stream = sys.stdout
    return stream.stream if isinstance(stream, OutputCopy) else stream


def set_console_level(level):
    """Ajusta o nível dos handlers do console sem alterar o logger."""

    root = logging.getLogger("eatventure")

    alvo = (
        level
        if isinstance(level, int)
        else getattr(logging, str(level).upper(), logging.WARNING)
    )

    for handler in root.handlers:

        if isinstance(handler, ConsoleHandler):
            handler.setLevel(alvo)

    return alvo


def set_console_panel(panel):
    root = logging.getLogger("eatventure")

    for handler in root.handlers:
        if isinstance(handler, ConsoleHandler):
            handler.acquire()
            try:
                handler.panel = panel
            finally:
                handler.release()


def start_process_output(process, name):
    pipe = process.stdout
    if pipe is None:
        return None

    logger = get(name)

    def forward():
        try:
            for line in pipe:
                logger.info(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    thread = threading.Thread(
        target=forward,
        name=f"eatventure-{name}-output",
        daemon=True,
    )
    thread.start()
    return thread


def get(name):

    return logging.getLogger(
        f"eatventure.{name}"
    )
