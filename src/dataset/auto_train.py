"""Agenda o treino em outro processo quando há um lote completo na coleta."""

import os
import re
import subprocess
import sys
import threading
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path

from core import log
from dataset.layout import record_resolution
from dataset.training_files import snapshot_records, trained_sample_ids


logger = log.get("auto_train")
ROOT = Path(__file__).resolve().parents[2]


class AutoTrainer:
    def __init__(
        self, directory, batch_size=10_000, interval=5.0, jobs=1,
        pause_bot=True, training_session=None,
    ):
        self.directory = Path(directory)
        self.batch_size = batch_size
        self.interval = interval
        self.jobs = jobs
        self.pause_bot = pause_bot
        self.training_session = training_session or (lambda resolution: nullcontext())
        self._pause_requested = threading.Event()
        self._pause_ready = threading.Event()
        self._stop = threading.Event()
        self._check = threading.Event()
        self._lock = threading.Lock()
        self._process = None
        self.thread = None

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self._stop.clear()
        self._check.set()
        self.thread = threading.Thread(
            target=self._run, name="auto-train", daemon=True,
        )
        self.thread.start()

    def notify(self):
        self._check.set()

    @property
    def pause_requested(self):
        return self._pause_requested.is_set()

    def acknowledge_pause(self):
        if self.pause_requested and not self._stop.is_set():
            self._pause_ready.set()

    def _request_pause(self):
        if self._stop.is_set():
            return False
        if not self.pause_bot:
            return True
        self._pause_ready.clear()
        self._pause_requested.set()
        logger.info("Aguardando o bot pausar para iniciar o treino automático.")
        while not self._stop.is_set():
            if self._pause_ready.wait(timeout=0.1):
                return not self._stop.is_set()
        return False

    def stop(self):
        self._stop.set()
        self._check.set()
        with self._lock:
            process = self._process
            if process is not None:
                logger.info("Encerrando treino automático; dados não consumidos serão preservados.")
                self._terminate(process)
        if self.thread is not None:
            self.thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            self._check.wait(timeout=self.interval)
            self._check.clear()
            if self._stop.is_set():
                break
            try:
                for resolution in self._ready_resolutions():
                    if self._stop.is_set():
                        break
                    self._train(resolution)
            except Exception:
                logger.exception("Não foi possível agendar o treino automático; coleta preservada.")
            # Agrupa notificações para não reler o índice a cada imagem salva.
            if self._stop.wait(self.interval):
                break

    def _ready(self):
        return bool(self._ready_resolutions())

    def _ready_resolutions(self):
        registros, _ = snapshot_records(self.directory)
        treinados = trained_sample_ids(self.directory)
        novos = defaultdict(set)
        totais = defaultdict(set)
        for record in registros:
            key = f"{record.get('_shard') or '.'}/{record.get('id') or record['_path'].stem}"
            resolution = record_resolution(record)
            totais[resolution].add(key)
            if key not in treinados:
                novos[resolution].add(key)
        for resolution, records in sorted(totais.items()):
            logger.info(
                "Treino automático: resolução %s tem %d amostras válidas, %d novas (%d já processadas); limite %d.",
                resolution, len(records), len(novos[resolution]),
                len(records) - len(novos[resolution]), self.batch_size,
            )
        return sorted(
            resolution for resolution, records in novos.items()
            if len(records) >= self.batch_size
        )

    def _terminate(self, process):
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                self._stop.set()
                logger.warning("Não foi possível encerrar o processo de treino.", exc_info=True)

    def _train(self, resolution="legacy"):
        if not re.fullmatch(r"(?:[1-9]\d*x[1-9]\d*|legacy)", resolution):
            raise ValueError(f"Resolução inválida para treino: {resolution}")
        command = [
            sys.executable, "-u", str(ROOT / "IA" / "train_ai.py"),
            "--dataset-root", str(self.directory.resolve()),
            "--resolution", resolution,
            "--automatic", "--min-samples", str(self.batch_size),
            "--batch-size", str(self.batch_size),
            "--cleanup", "--jobs", str(self.jobs),
        ]
        command.extend((
            "--model-out",
            str(ROOT / "IA" / "models" / resolution / "category.joblib"),
        ))
        process = None
        returncode = None
        try:
            with self.training_session(resolution):
                if not self._request_pause():
                    return
                with self._lock:
                    if self._stop.is_set():
                        return
                    logger.info(
                        "Lote de %d registros da resolução %s disponível; iniciando treino automático.",
                        self.batch_size, resolution,
                    )
                    process = subprocess.Popen(
                        command, cwd=ROOT, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    self._process = process
                output_thread = log.start_process_output(process, "train")
                returncode = process.wait()
                if output_thread is not None:
                    output_thread.join(timeout=5)
                if not self._stop.is_set() and returncode:
                    logger.warning("Treino automático não concluído (código %d); dados preservados.", returncode)
        finally:
            with self._lock:
                if process is not None:
                    self._terminate(process)
            # Mantém a pausa se uma falha do sistema impedir encerrar o filho.
            if process is not None:
                while process.poll() is None:
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        continue
            with self._lock:
                self._process = None
            self._pause_requested.clear()
            self._pause_ready.clear()
        if not self._stop.is_set() and returncode:
            self._stop.wait(60)
