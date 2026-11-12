"""Grava dataset: frame, detecções (caixas+categoria), ação e resultado. Assíncrono com fila descartável."""

import hashlib
from core.i18n import t
import json
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from core import log
from dataset import layout
from dataset.collection_budget import CollectionBudget
from dataset.training_files import (
    cleanup_records,
    index_lock,
    snapshot_records,
)
from core.config import (
    AI_AUTO_TRAIN,
    AI_PAUSE_BOT_WHILE_TRAINING,
    AI_TRAIN_BATCH_SIZE,
    AI_TRAIN_CHECK_INTERVAL,
    AI_TRAIN_JOBS,
    DATASET_ACTIONS,
    DATASET_DIR,
    DATASET_HASH_SIZE,
    DATASET_IMAGE_FORMAT,
    DATASET_JPEG_QUALITY,
    DATASET_MAX_SAMPLES,
    DATASET_MAX_DISK_MB,
    DATASET_MAX_SAMPLES_PER_LABEL,
    DATASET_MAX_UNCHANGED_STREAK,
    DATASET_NEGATIVE_INTERVAL,
    DATASET_SCREEN_CHANGE,
    DATASET_OUTCOME_TIMEOUT,
    DATASET_QUEUE_SIZE,
)

logger = log.get("dataset")


# Resultados possíveis de uma ação.
CHANGED = "changed"        # o alvo saiu da tela: a ação pegou
UNCHANGED = "unchanged"    # o alvo continua lá: não pegou
UNKNOWN = "unknown"        # não chegou frame para julgar


class Sample:
    """Amostra em construção: fica pendente entre a ação e o frame que mostra o efeito dela."""

    __slots__ = (
        "id",
        "created",
        "action_time",
        "frame",
        "detections",
        "action",
        "action_kind",
        "target",
        "click",
        "state",
        "lag",
        "cycle_count",
        "outcome",
        "outcome_after",
        "label_evidence",
        "thumb",
    )

    def __init__(self, **campos):

        for nome in self.__slots__:
            setattr(self, nome, campos.get(nome))


class DatasetRecorder:

    def __init__(
        self,
        directory=None,
        session=None,
        enabled=True,
        auto_train=None,
    ):

        self.root = Path(
            DATASET_DIR if directory is None else directory
        ).resolve()

        self.session = session or uuid.uuid4().hex
        self.created_at = time.time()

        self.enabled = enabled
        self.auto_trainer = None
        enable_auto_train = (
            AI_AUTO_TRAIN if auto_train is None else auto_train
        ) and os.environ.get("EATVENTURE_AUTO_TRAIN_EXTERNAL") != "1"
        if enable_auto_train:
            from dataset.auto_train import AutoTrainer

            self.auto_trainer = AutoTrainer(
                self.root, batch_size=AI_TRAIN_BATCH_SIZE,
                interval=AI_TRAIN_CHECK_INTERVAL, jobs=AI_TRAIN_JOBS,
                pause_bot=AI_PAUSE_BOT_WHILE_TRAINING,
            )

        # Destino é por resolução e decidido por amostra: o device pode
        # trocar de resolução no meio da sessão (outro aparelho, rotação).
        self.data_dir = self.root / layout.DATA_DIRNAME

        # Fila limitada e descartável: o bot nunca espera o disco.
        self.queue = queue.Queue(maxsize=DATASET_QUEUE_SIZE)

        self.running = False
        self.thread = None

        self.pending = None
        self.lock = threading.Lock()

        # Corta amostragem de ação travada (medido: 35 amostras/30s repetindo a mesma tela).
        # Dedup por conteúdo não serve aqui pois a tela anima sozinha; o que separa
        # limpo é o RESULTADO (unchanged em sequência = travado).
        self.unchanged_action = None
        self.unchanged_streak = 0

        self.saved = 0
        self._resolution_count_cache = {}
        self.dropped = 0
        self.duplicates = 0
        self.by_action = {}
        self.budget = CollectionBudget(self.root)

        # Última negativa, para espaçá-las no TEMPO. Começa no
        # passado para a primeira sair logo.
        self.last_negative = 0.0
        self.empty_observation = None

        self.bytes_written = 0

        self.disk_warned = False
        self._discard_ranges = []
        self.discarded = 0

    def samples_for_resolution(self, resolution):
        """Total atualmente registrado no shard da resolução."""
        if not isinstance(resolution, str) or not re.fullmatch(
            r"[1-9]\d*x[1-9]\d*", resolution
        ):
            return 0

        index = layout.index_path(self.data_dir / resolution)
        try:
            signature = (index.stat().st_mtime_ns, index.stat().st_size)
        except OSError:
            self._resolution_count_cache.pop(resolution, None)
            return 0

        cached = self._resolution_count_cache.get(resolution)
        if cached and cached[0] == signature:
            return cached[1]

        try:
            with index_lock(self.root):
                signature = (index.stat().st_mtime_ns, index.stat().st_size)
                with index.open(encoding="utf-8") as file:
                    total = sum(1 for line in file if line.strip())
        except OSError:
            return 0

        self._resolution_count_cache[resolution] = (signature, total)
        return total

    def start(self):

        if (
            self.running
            or (self.thread is not None and self.thread.is_alive())
            or not self.enabled
        ):
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.running = True

        self.thread = threading.Thread(
            target=self._run,
            name="dataset",
            daemon=True,
        )

        self.thread.start()

        if self.auto_trainer is not None:
            self.auto_trainer.start()

        logger.info(
            t("messages.dataset_recording"),
            self.root,
            self.session[:8],
        )

    def stop(self):

        if self.auto_trainer is not None:
            self.auto_trainer.stop()

        if not self.running:

            if self.thread is None or not self.thread.is_alive():

                self.thread = None

            return

        # Fecha a pendente com veredito UNKNOWN em vez de perdê-la.
        self._flush_pending(UNKNOWN, None)

        self.running = False

        if self.thread:

            self.thread.join(timeout=10.0)

            if self.thread.is_alive():

                logger.warning(
                    t("messages.dataset_queue_draining")
                )

            else:

                self.thread = None

        logger.info(
            t("messages.dataset_summary"),
            self.saved,
            self.bytes_written / 1_000_000,
            self.duplicates,
            self.dropped,
            dict(sorted(self.by_action.items())) or "",
        )

    def prepare_for_training(self):
        with self.queue.all_tasks_done:
            if self.queue.unfinished_tasks:
                return False
        # A tela após o treino não pode julgar o resultado do último toque.
        self._flush_pending(UNKNOWN, None)
        with self.queue.all_tasks_done:
            return self.queue.unfinished_tasks == 0

    def on_action(
        self,
        action,
        action_kind,
        detection,
        click,
        state,
        frame,
        detections,
        lag,
        cycle_count=0,
    ):
        """Chamado quando uma ação SAIU de verdade. `click` está em coordenada do FRAME."""

        if not self.running:
            return

        self.empty_observation = None
        # Uma segunda ação (mesmo fora da coleta) impede atribuir o efeito à primeira.
        self._flush_pending(UNKNOWN, None)
        if action_kind not in ("scroll", "swipe"):
            self._resume_collection(time.time())

        if action not in DATASET_ACTIONS:
            return

        if frame is None:
            return

        if DATASET_MAX_SAMPLES and self.saved >= DATASET_MAX_SAMPLES:
            return

        agora = time.monotonic()

        # A ação pode ter sido escolhida pela IA, mas o rótulo só é aceito se
        # houver a mesma região confirmada pelo professor (templates).
        teacher = list(getattr(detections, "teacher_detections", detections) or [])
        target = self._teacher_target(detection, teacher)

        with self.lock:

            self.pending = Sample(
                id=uuid.uuid4().hex,
                created=datetime.now(timezone.utc),
                action_time=agora,

                # Cópia própria: o frame do ScreenCapture não sobrevive além do loop.
                frame=frame.copy(),

                detections=teacher,

                # Miniatura do ANTES, para julgar ação sem alvo pela mudança de tela.
                thumb=self._thumb(frame),

                action=action,
                action_kind=action_kind,
                target=target,
                click=click,
                state=state,
                lag=lag,
                cycle_count=cycle_count,
            )

    @staticmethod
    def _teacher_target(detection, teachers):
        if detection is None:
            return None
        category = detection.get("category")
        if not category:
            return None
        best, best_score = None, 0.0
        for candidate in teachers:
            if candidate.get("category") != category:
                continue
            if candidate is detection:
                return candidate
            width = max(1, int(detection.get("width", 0)))
            height = max(1, int(detection.get("height", 0)))
            dx = abs(int(candidate.get("x", 0)) - int(detection.get("x", 0))) / width
            dy = abs(int(candidate.get("y", 0)) - int(detection.get("y", 0))) / height
            score = 1.0 - max(dx, dy)
            if score > best_score:
                best, best_score = candidate, score
        return best if best_score >= 0.5 else None

    def observe(self, frame, detections, frame_time, state):
        """Julga a ação pendente e, de vez em quando, recolhe uma amostra NEGATIVA
        (tela sem alvo) — senão o detector aprende que sempre existe alvo."""

        if not self.running:
            return

        if self._resolve(frame, detections, frame_time):
            self.empty_observation = None
            return
        self._maybe_negative(frame, detections, frame_time, state)

    def _resolve(self, frame, detections, frame_time):

        with self.lock:

            pendente = self.pending

        if pendente is None:
            return False

        # O frame precisa ser POSTERIOR à ação, senão não pode
        # mostrar o efeito dela.
        if frame_time is None or frame_time <= pendente.action_time:

            espera = time.monotonic() - pendente.action_time

            if espera > DATASET_OUTCOME_TIMEOUT:

                self._flush_pending(UNKNOWN, espera)

            return True

        depois = frame_time - pendente.action_time

        outcome = UNKNOWN
        if depois <= DATASET_OUTCOME_TIMEOUT:
            teacher = getattr(detections, "teacher_detections", detections)
            outcome = self._julgar(pendente, frame, teacher)
        if outcome == CHANGED and pendente.target is not None:
            if not self._model_prediction(pendente.target):
                pendente.label_evidence = "target_changed"
        self._flush_pending(outcome, depois)
        return True

    @staticmethod
    def _model_prediction(detection):
        return str(detection.get("name", "")).startswith("modelo:")

    def _julgar(self, pendente, frame, detections):
        """Com alvo, julga se ele saiu da tela; sem alvo (swipe/scroll/dismiss), se a TELA mudou."""

        if frame is None or frame.shape != pendente.frame.shape or detections is None:
            return UNKNOWN

        if pendente.target is not None:
            if self._alvo_continua(pendente, detections):
                return UNCHANGED
            # O filtro por estado ou um erro do detector também pode ocultar
            # o alvo. Exige uma mudança de pixels dentro da caixa tocada.
            x, y, w, h = (int(pendente.target[k]) for k in ("x", "y", "width", "height"))
            altura, largura = frame.shape[:2]
            if not (0 <= x < x + w <= largura and 0 <= y < y + h <= altura):
                return UNKNOWN
            before = pendente.frame[y:y+h, x:x+w]
            after = frame[y:y+h, x:x+w]
            before = cv2.resize(before, (16, 16), interpolation=cv2.INTER_AREA)
            after = cv2.resize(after, (16, 16), interpolation=cv2.INTER_AREA)
            delta = cv2.absdiff(before, after).mean(axis=2)
            return CHANGED if np.mean(delta > 2 * DATASET_SCREEN_CHANGE) >= 0.25 else UNKNOWN

        mudou = self._mudou(pendente.thumb, self._thumb(frame))

        if mudou is None:
            return UNKNOWN

        return CHANGED if mudou else UNCHANGED

    def _alvo_continua(self, pendente, detections):
        """Checa categoria E posição — só categoria daria falso "continua" com
        outra instância da mesma categoria em outro canto da tela (comum com comida)."""

        alvo = pendente.target

        folga = max(
            20,
            alvo.get("width", 0) // 2,
            alvo.get("height", 0) // 2,
        )

        for outra in detections or []:

            if outra["category"] != alvo["category"]:
                continue

            if (
                abs(outra["x"] - alvo["x"]) <= folga
                and abs(outra["y"] - alvo["y"]) <= folga
            ):
                return True

        return False

    def _flush_pending(self, outcome, depois):

        with self.lock:

            pendente = self.pending

            self.pending = None

        if pendente is None:
            return

        pendente.outcome = outcome
        pendente.outcome_after = depois

        self._submit(pendente)

    def _maybe_negative(self, frame, detections, frame_time, state):

        teacher = getattr(detections, "teacher_detections", detections)
        if teacher or frame is None or frame_time is None:
            self.empty_observation = None
            return

        if DATASET_NEGATIVE_INTERVAL <= 0 or not getattr(detections, "full_teacher_scan", False):
            return

        # Duas capturas distintas sem encontrar nada em TODAS as categorias.
        # Uma lista vazia filtrada por estado não atesta que a tela esteja vazia.
        context = (state, frame.shape)
        previous = self.empty_observation
        self.empty_observation = (frame_time, context)
        if previous is None or previous[1] != context or frame_time <= previous[0]:
            return

        agora = time.monotonic()

        # Espaçadas no TEMPO, não sorteadas: observe() é chamada
        # a cada passada do loop (~30x/s), e uma probabilidade
        # por passada enche o disco.
        if agora - self.last_negative < DATASET_NEGATIVE_INTERVAL:
            return

        if DATASET_MAX_SAMPLES and self.saved >= DATASET_MAX_SAMPLES:
            return

        self.last_negative = agora

        self._submit(
            Sample(
                id=uuid.uuid4().hex,
                created=datetime.now(timezone.utc),
                action_time=time.monotonic(),
                frame=frame.copy(),
                detections=[],
                thumb=self._thumb(frame),
                action=None,
                action_kind=None,
                target=None,
                click=None,
                state=state,
                lag=0.0,
                cycle_count=0,
                outcome=None,
                outcome_after=None,
                label_evidence="detector_empty",
            )
        )

    def _submit(self, sample):

        try:

            self.queue.put_nowait(sample)

        except queue.Full:

            self.dropped += 1

            # Um aviso por bloco, não um por amostra.
            if self.dropped % 20 == 1:

                logger.warning(
                    t("messages.dataset_queue_full"),
                    self.dropped,
                )

    def discard_since(self, timestamp=None):
        """Descarta o intervalo sem ação útil, inclusive fila e arquivos já gravados."""
        cutoff = self.created_at if timestamp is None else float(timestamp)
        with self.lock:
            if not self._discard_ranges or self._discard_ranges[-1][1] is not None:
                self._discard_ranges.append([cutoff, None])
            if (
                self.pending is not None
                and self.pending.created.timestamp() > cutoff
            ):
                self.pending = None

        try:
            records, _ = snapshot_records(self.root)
        except OSError as error:
            logger.warning(t("messages.dataset_review_failed"), error)
            return 0
        discarded = [
            record for record in records
            if record.get("session") == self.session and self._record_timestamp(record) > cutoff
        ]
        if not discarded:
            return 0
        ids = {record["id"] for record in discarded if record.get("id")}
        result = cleanup_records(self.root, discarded, ids)
        removed = result.get("records_removed", 0)
        self.discarded += removed
        self.saved = max(0, self.saved - removed)
        logger.warning(
            t("messages.dataset_irrelevant_samples"),
            removed, result.get("images_removed", 0),
        )
        return removed

    @staticmethod
    def _record_timestamp(record):
        try:
            return datetime.fromisoformat(record["created_at"]).timestamp()
        except (KeyError, TypeError, ValueError):
            return 0.0

    def _discarded(self, sample):
        with self.lock:
            created_at = sample.created.timestamp()
            return any(
                created_at > start and (end is None or created_at < end)
                for start, end in self._discard_ranges
            )

    def _resume_collection(self, timestamp):
        with self.lock:
            if self._discard_ranges and self._discard_ranges[-1][1] is None:
                self._discard_ranges[-1][1] = timestamp

    def _run(self):

        # stop() interrompe a coleta; o que já entrou na fila ainda vale.
        while self.running or not self.queue.empty():

            try:

                sample = self.queue.get(timeout=0.2)

            except queue.Empty:
                continue

            try:

                self._write(sample)

            except Exception as error:

                logger.exception(
                    t("messages.sample_write_failed"),
                    error,
                )

            finally:

                self.queue.task_done()

    def _write(self, sample):

        frame = sample.frame

        if frame is None:
            return

        if self._discarded(sample):
            return

        # Resultados não confirmados são diagnóstico do runtime, não memória
        # de treino. Guardá-los só consome espaço e contamina o próximo lote.
        if sample.label_evidence not in {"target_changed", "detector_empty"}:
            return

        # A coleta pode enfileirar várias amostras antes de saved avançar.
        if DATASET_MAX_SAMPLES and self.saved >= DATASET_MAX_SAMPLES:
            return

        # Mesma ação dando unchanged repetidamente = bot preso; para de gravar a mesma tela.
        if sample.action and sample.outcome == UNCHANGED:

            if sample.action == self.unchanged_action:

                self.unchanged_streak += 1

            else:

                self.unchanged_action = sample.action
                self.unchanged_streak = 1

            if self.unchanged_streak > DATASET_MAX_UNCHANGED_STREAK:

                self.duplicates += 1

                if self.unchanged_streak == (
                    DATASET_MAX_UNCHANGED_STREAK + 1
                ):

                    logger.info(
                        t("messages.dataset_action_stuck"),
                        sample.action,
                    )

                return

        else:

            # Progresso (ou negativa): a contagem recomeça.
            self.unchanged_action = None
            self.unchanged_streak = 0

        # A coleta é uma memória de treino, não um log completo. Limitar por
        # ação/categoria impede que um único botão comum domine os exemplos e
        # deixa espaço para situações raras. A contagem só avança depois que o
        # arquivo e o índice foram gravados com sucesso.
        altura_frame, largura_frame = sample.frame.shape[:2]
        shard = layout.shard_root(
            self.root,
            largura_frame,
            altura_frame,
        )
        label = self._sample_label(sample)
        with index_lock(self.root):
            if not self._within_budget(shard, label):
                self.duplicates += 1
                return

        chave = self._perceptual_hash(frame)
        dia = sample.created.strftime("%Y-%m-%d")

        pasta = layout.images_dir(shard) / dia

        pasta.mkdir(parents=True, exist_ok=True)

        nome = f"{sample.id}.{DATASET_IMAGE_FORMAT}"

        caminho = pasta / nome

        params = []

        if DATASET_IMAGE_FORMAT.lower() in ("jpg", "jpeg"):

            params = [
                cv2.IMWRITE_JPEG_QUALITY,
                int(DATASET_JPEG_QUALITY),
            ]

        if not cv2.imwrite(str(caminho), frame, params):

            logger.warning(
                t("messages.file_write_failed"),
                caminho,
            )

            return

        registro = self._record(sample, caminho, chave, shard)

        with index_lock(self.root):
            if self._discarded(sample):
                caminho.unlink(missing_ok=True)
                return
            if not self._within_budget(shard, label):
                caminho.unlink(missing_ok=True)
                self.duplicates += 1
                return
            with layout.index_path(shard).open(
                "a", encoding="utf-8",
            ) as arquivo:
                arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
            self.budget.shards.pop(shard, None)
            self.budget.refresh(shard)

        self.saved += 1

        self.bytes_written += caminho.stat().st_size

        rotulo = sample.action or "negativo"

        self.by_action[rotulo] = self.by_action.get(rotulo, 0) + 1

    @staticmethod
    def _sample_label(sample):
        if sample.action is None:
            return "negative"
        category = (sample.target or {}).get("category")
        return sample.action, category

    def _within_budget(self, shard, label):
        inventory = self.budget.refresh(shard)
        limit = DATASET_MAX_DISK_MB * 1_000_000
        if limit and self.budget.size >= limit:
            if not self.disk_warned:
                logger.warning(t("messages.dataset_disk_limit"), DATASET_MAX_DISK_MB)
                self.disk_warned = True
            return False
        self.disk_warned = False
        if DATASET_MAX_SAMPLES_PER_LABEL and inventory.counts[label] >= DATASET_MAX_SAMPLES_PER_LABEL:
            return False
        return True

    def _record(self, sample, caminho, phash, shard):

        altura, largura = sample.frame.shape[:2]

        alvo = sample.target

        return {
            "id": sample.id,
            "session": self.session,
            "device_id": os.environ.get("EATVENTURE_DEVICE_ID") or None,
            "created_at": sample.created.isoformat(),

            # Relativo à raiz DA RESOLUÇÃO: a pasta daquela resolução
            # inteira pode ser movida, copiada ou apagada sem invalidar
            # o índice dela nem o das outras.
            "image": str(
                caminho.relative_to(shard).as_posix()
            ),

            "image_sha256": self._sha256(caminho),
            "phash": phash,

            "frame_width": largura,
            "frame_height": altura,

            "state": sample.state,

            "action": sample.action,
            "action_kind": sample.action_kind,

            "click_x": (
                None if sample.click is None else int(sample.click[0])
            ),
            "click_y": (
                None if sample.click is None else int(sample.click[1])
            ),

            "target_category": (
                None if alvo is None else alvo.get("category")
            ),
            "target_template": (
                None if alvo is None else alvo.get("template_path")
            ),
            "target_confidence": (
                None if alvo is None else float(alvo.get("confidence", 0.0))
            ),

            "detect_lag_ms": (
                None if sample.lag is None
                else round(sample.lag * 1000)
            ),

            "outcome": sample.outcome,
            "label_evidence": sample.label_evidence,
            "outcome_after_ms": (
                None if sample.outcome_after is None
                else round(sample.outcome_after * 1000)
            ),

            "cycle_count": sample.cycle_count,

            # TODAS as detecções do frame, não só a que virou
            # ação: as outras são rótulo grátis.
            "boxes": [
                {
                    "category": d["category"],
                    "template": d.get("name"),
                    "confidence": round(
                        float(d.get("confidence", 0.0)),
                        4,
                    ),
                    "color_similarity": round(
                        float(d.get("color_similarity", 0.0)),
                        4,
                    ),
                    "x": int(d["x"]),
                    "y": int(d["y"]),
                    "width": int(d["width"]),
                    "height": int(d["height"]),
                    "acted": bool(
                        alvo is not None
                        and d is alvo
                    ),
                }
                for d in sample.detections
            ],
        }

    def _thumb(self, frame):

        lado = DATASET_HASH_SIZE

        cinza = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        return cv2.resize(
            cinza,
            (lado, lado),
            interpolation=cv2.INTER_AREA,
        ).astype(np.int16)

    def _mudou(self, antes, depois):
        """Mudança GRANDE é confiável: rolagem move 8.9-59.8 (medido) vs ruído de
        animação abaixo de 5.8 — a distinção que a dedup por conteúdo não consegue fazer."""

        if antes is None or depois is None:
            return None

        return bool(
            np.abs(antes - depois).mean() > DATASET_SCREEN_CHANGE
        )

    def _perceptual_hash(self, frame):
        """Gravado só como metadado — não filtra aqui, é para dedup offline depois."""

        pequeno = self._thumb(frame)

        bits = (pequeno > pequeno.mean()).flatten()

        return "".join("1" if b else "0" for b in bits)

    def _sha256(self, caminho):
        """Identidade exata do arquivo, para detectar duplicata entre sessões."""

        digest = hashlib.sha256()

        with caminho.open("rb") as arquivo:

            for bloco in iter(lambda: arquivo.read(1 << 20), b""):

                digest.update(bloco)

        return digest.hexdigest()

    def stats(self):

        return {
            "saved": self.saved,
            "duplicates": self.duplicates,
            "dropped": self.dropped,
            "mb": round(self.bytes_written / 1_000_000, 1),
            "by_action": dict(self.by_action),
        }
