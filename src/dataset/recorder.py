"""
Gravação do dataset de treino.

Grava, para cada ação do bot, o frame que motivou a decisão e os
RÓTULOS que o template matcher produziu — as caixas com
categoria, o alvo escolhido, o ponto tocado e o RESULTADO da
ação.

Por que caixas e não só o ponto do clique:

    Um ponto por imagem é a formulação mais fraca possível —
    ambígua quando há vários alvos na tela, e não ensina quantos
    existem. Com as caixas, a tarefa é detecção de objetos: a
    mesma que o template matcher faz, com muito mais rótulo por
    imagem. O ponto do clique se deriva da caixa; o contrário
    não.

Por que gravar o resultado:

    Sem ele o treino herda todo erro do professor, e o modelo
    não passa do template matcher. Com ele dá para treinar só
    nas ações que mudaram a tela como esperado.

Nada disto roda no caminho crítico: codificar PNG de 1080x2400
custa mais que uma passada do detector. A gravação vai para uma
thread com fila LIMITADA que DESCARTA quando enche — perder
amostra é aceitável, atrasar o bot não é.
"""

import hashlib
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from core import log
from core.config import (
    DATASET_ACTIONS,
    DATASET_DIR,
    DATASET_HASH_SIZE,
    DATASET_IMAGE_FORMAT,
    DATASET_JPEG_QUALITY,
    DATASET_MAX_SAMPLES,
    DATASET_MAX_DISK_MB,
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
    """
    Uma amostra em construção.

    Fica pendente entre a ação e o frame que mostra o efeito
    dela — é essa espera que permite gravar o resultado.
    """

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
        "thumb",
    )

    def __init__(self, **campos):

        for nome in self.__slots__:
            setattr(self, nome, campos.get(nome))


class DatasetRecorder:

    def __init__(
        self,
        directory=None,
        store=None,
        session=None,
        enabled=True,
    ):

        self.root = Path(
            DATASET_DIR if directory is None else directory
        )

        # Índice consultável (Postgres). None = só arquivos.
        self.store = store

        self.session = session or uuid.uuid4().hex

        self.enabled = enabled

        self.images_dir = self.root / "images"
        self.index_path = self.root / "samples.jsonl"

        # -------------------------------------------------
        # FILA
        # -------------------------------------------------
        #
        # Limitada e descartável: o bot nunca espera o disco.
        #
        self.queue = queue.Queue(maxsize=DATASET_QUEUE_SIZE)

        self.running = False
        self.thread = None

        # -------------------------------------------------
        # AMOSTRA PENDENTE
        # -------------------------------------------------

        self.pending = None
        self.lock = threading.Lock()

        # -------------------------------------------------
        # BOT TRAVADO
        # -------------------------------------------------
        #
        # Sem isto, um bot preso repetindo a mesma ação enche o
        # dataset com a mesma tela: medido, 35 amostras em 30 s.
        #
        # A deduplicação por CONTEÚDO foi tentada e não serve —
        # a tela do jogo anima sozinha, e as faixas de "mesma
        # tela" e "telas distintas" se sobrepõem. O que separa
        # limpo é o RESULTADO: travado dá `unchanged` sempre,
        # produtivo dá `changed` (open_box 5x seguidas, todas
        # changed).
        #
        self.unchanged_action = None
        self.unchanged_streak = 0

        # -------------------------------------------------
        # CONTADORES
        # -------------------------------------------------

        self.saved = 0
        self.dropped = 0
        self.duplicates = 0
        self.by_action = {}

        # Última negativa, para espaçá-las no TEMPO. Começa no
        # passado para a primeira sair logo.
        self.last_negative = 0.0

        # Bytes já gravados nesta execução, para o teto de
        # disco. Só conta o que esta sessão escreveu: varrer a
        # pasta a cada amostra custaria I/O à toa.
        self.bytes_written = 0

        self.disk_warned = False

    # =====================================================
    # CICLO DE VIDA
    # =====================================================

    def start(self):

        if self.running or not self.enabled:
            return

        self.images_dir.mkdir(parents=True, exist_ok=True)

        self.running = True

        self.thread = threading.Thread(
            target=self._run,
            name="dataset",
            daemon=True,
        )

        self.thread.start()

        logger.info(
            "Gravando dataset em %s (sessão %s)",
            self.root,
            self.session[:8],
        )

    def stop(self):

        if not self.running:
            return

        # Fecha a amostra pendente antes de sair: ela já tem o
        # frame e os rótulos, só não tem veredito.
        self._flush_pending(UNKNOWN, None)

        self.running = False

        try:
            self.queue.put_nowait(None)

        except queue.Full:
            pass

        if self.thread:

            self.thread.join(timeout=10.0)

            self.thread = None

        logger.info(
            "Dataset: %d gravadas (%.1f MB), %d duplicadas, "
            "%d descartadas por fila cheia %s",
            self.saved,
            self.bytes_written / 1_000_000,
            self.duplicates,
            self.dropped,
            dict(sorted(self.by_action.items())) or "",
        )

        if self.store:

            self.store.close()

    # =====================================================
    # ENTRADA: AÇÃO
    # =====================================================

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
        """
        Chamado quando uma ação SAIU de verdade.

        `click` é o ponto tocado em coordenada do FRAME (o
        mesmo espaço da imagem gravada), ou None para ações sem
        coordenada.
        """

        if not self.running:
            return

        if action not in DATASET_ACTIONS:
            return

        if frame is None:
            return

        if DATASET_MAX_SAMPLES and self.saved >= DATASET_MAX_SAMPLES:
            return

        # A pendente anterior nunca recebeu veredito.
        self._flush_pending(UNKNOWN, None)

        agora = time.monotonic()

        with self.lock:

            self.pending = Sample(
                id=uuid.uuid4().hex,
                created=datetime.now(timezone.utc),
                action_time=agora,

                # O frame já é cópia privada do ScreenCapture,
                # mas a partir daqui ele vive além do loop:
                # copiar evita depender disso.
                frame=frame.copy(),

                detections=list(detections or []),

                # Miniatura do ANTES, para julgar ação sem alvo
                # pontual pela mudança de tela.
                thumb=self._thumb(frame),

                action=action,
                action_kind=action_kind,
                target=detection,
                click=click,
                state=state,
                lag=lag,
                cycle_count=cycle_count,
            )

    # =====================================================
    # ENTRADA: CADA FRAME
    # =====================================================

    def observe(self, frame, detections, frame_time, state):
        """
        Chamado a cada passada, com o frame que gerou as
        detecções atuais.

        Faz duas coisas: julga a ação pendente, e recolhe
        amostra NEGATIVA (tela sem alvo nenhum) de vez em
        quando — um detector treinado só em telas com alvo
        aprende que sempre existe alvo.
        """

        if not self.running:
            return

        self._resolve(frame, detections, frame_time)

        self._maybe_negative(frame, detections, state)

    # =====================================================
    # RESULTADO
    # =====================================================

    def _resolve(self, frame, detections, frame_time):

        with self.lock:

            pendente = self.pending

        if pendente is None:
            return

        # O frame precisa ser POSTERIOR à ação, senão não pode
        # mostrar o efeito dela.
        if frame_time is None or frame_time <= pendente.action_time:

            espera = time.monotonic() - pendente.action_time

            if espera > DATASET_OUTCOME_TIMEOUT:

                self._flush_pending(UNKNOWN, espera)

            return

        depois = frame_time - pendente.action_time

        self._flush_pending(
            self._julgar(pendente, frame, detections),
            depois,
        )

    def _julgar(self, pendente, frame, detections):
        """
        A ação surtiu efeito?

        Com alvo, a pergunta é se ele saiu da tela. SEM alvo
        (swipe, scroll, dismiss), isso não quer dizer nada — a
        pergunta passa a ser se a TELA mudou.
        """

        if pendente.target is not None:

            return (
                UNCHANGED
                if self._alvo_continua(pendente, detections)
                else CHANGED
            )

        mudou = self._mudou(pendente.thumb, self._thumb(frame))

        if mudou is None:
            return UNKNOWN

        return CHANGED if mudou else UNCHANGED

    def _alvo_continua(self, pendente, detections):
        """
        O alvo ainda está lá, mais ou menos no mesmo lugar?

        Comparar só a categoria daria falso "continua" quando
        há outra instância da mesma categoria em outro canto da
        tela — comum com comida.
        """

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

    # =====================================================
    # NEGATIVAS
    # =====================================================

    def _maybe_negative(self, frame, detections, state):

        if detections or frame is None:
            return

        if DATASET_NEGATIVE_INTERVAL <= 0:
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
            )
        )

    # =====================================================
    # FILA
    # =====================================================

    def _submit(self, sample):

        try:

            self.queue.put_nowait(sample)

        except queue.Full:

            self.dropped += 1

            # Um aviso por bloco, não um por amostra.
            if self.dropped % 20 == 1:

                logger.warning(
                    "Fila do dataset cheia — %d amostra(s) "
                    "descartada(s). Disco lento, ou "
                    "DATASET_IMAGE_FORMAT em png com o bot "
                    "muito rápido?",
                    self.dropped,
                )

    # =====================================================
    # THREAD
    # =====================================================

    def _run(self):

        while self.running:

            try:

                sample = self.queue.get(timeout=0.2)

            except queue.Empty:
                continue

            if sample is None:
                break

            try:

                self._write(sample)

            except Exception as error:

                logger.exception(
                    "Erro gravando amostra: %s",
                    error,
                )

    # =====================================================
    # ESCRITA
    # =====================================================

    def _write(self, sample):

        frame = sample.frame

        if frame is None:
            return

        # -------------------------------------------------
        # TETO DE DISCO
        # -------------------------------------------------
        #
        # O bot roda horas sem ninguém olhando; sem teto, o
        # sintoma é disco cheio.
        #
        if (
            DATASET_MAX_DISK_MB
            and self.bytes_written >= DATASET_MAX_DISK_MB * 1_000_000
        ):

            if not self.disk_warned:

                logger.warning(
                    "Dataset chegou ao teto de %d MB — parando "
                    "de gravar. O bot continua jogando. Suba "
                    "DATASET_MAX_DISK_MB, mude "
                    "DATASET_IMAGE_FORMAT para jpg (4.3x menor) "
                    "ou mova a pasta.",
                    DATASET_MAX_DISK_MB,
                )

                self.disk_warned = True

            return

        # -------------------------------------------------
        # BOT TRAVADO?
        # -------------------------------------------------
        #
        # Mesma ação dando `unchanged` de novo e de novo = preso.
        # Guardar a terceira em diante só engorda o dataset com
        # a mesma tela.
        #
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
                        "'%s' preso em unchanged — parando de "
                        "gravar até mudar de ação ou dar certo",
                        sample.action,
                    )

                return

        else:

            # Progresso (ou negativa): a contagem recomeça.
            self.unchanged_action = None
            self.unchanged_streak = 0

        chave = self._perceptual_hash(frame)

        # -------------------------------------------------
        # IMAGEM
        # -------------------------------------------------

        dia = sample.created.strftime("%Y-%m-%d")

        pasta = self.images_dir / dia

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
                "Não conseguiu gravar %s",
                caminho,
            )

            return

        # -------------------------------------------------
        # REGISTRO
        # -------------------------------------------------

        registro = self._record(sample, caminho, chave)

        with self.index_path.open("a", encoding="utf-8") as arquivo:

            arquivo.write(
                json.dumps(registro, ensure_ascii=False) + "\n"
            )

        self.saved += 1

        self.bytes_written += caminho.stat().st_size

        rotulo = sample.action or "negativo"

        self.by_action[rotulo] = self.by_action.get(rotulo, 0) + 1

        # -------------------------------------------------
        # ÍNDICE NO BANCO (opcional)
        # -------------------------------------------------
        #
        # Falha de banco não pode derrubar a gravação: o JSONL
        # continua sendo a fonte de verdade do treino.
        #
        if self.store:

            try:

                self.store.insert(registro)

            except Exception as error:

                logger.warning(
                    "Falha ao indexar no banco (o arquivo "
                    "foi gravado): %s",
                    error,
                )

    def _record(self, sample, caminho, phash):

        altura, largura = sample.frame.shape[:2]

        alvo = sample.target

        return {
            "id": sample.id,
            "session": self.session,
            "created_at": sample.created.isoformat(),

            # Relativo à raiz do dataset: mover a pasta não
            # invalida o índice.
            "image": str(
                caminho.relative_to(self.root).as_posix()
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
                None if alvo is None else alvo.get("name")
            ),
            "target_confidence": (
                None if alvo is None else float(alvo.get("confidence", 0.0))
            ),

            "detect_lag_ms": (
                None if sample.lag is None
                else round(sample.lag * 1000)
            ),

            "outcome": sample.outcome,
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

    # =====================================================
    # MINIATURA
    # =====================================================

    def _thumb(self, frame):
        """
        Miniatura NxN em tons de cinza, para comparar telas.
        """

        lado = DATASET_HASH_SIZE

        cinza = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        return cv2.resize(
            cinza,
            (lado, lado),
            interpolation=cv2.INTER_AREA,
        ).astype(np.int16)

    def _mudou(self, antes, depois):
        """
        A tela mudou de forma visível entre os dois frames?

        Usado nas ações SEM alvo pontual (swipe, scroll,
        dismiss), onde "o alvo saiu da tela" não quer dizer
        nada — o que interessa é se a ação surtiu efeito.

        Detectar mudança GRANDE é confiável: uma rolagem move a
        vista inteira (8.9 a 59.8 nos dados medidos), enquanto o
        ruído de animação fica abaixo de 5.8. É a distinção que
        a deduplicação NÃO consegue fazer, porque lá o problema
        é separar "nada mudou" de "mudou pouco".
        """

        if antes is None or depois is None:
            return None

        return bool(
            np.abs(antes - depois).mean() > DATASET_SCREEN_CHANGE
        )

    def _perceptual_hash(self, frame):
        """
        Hash de similaridade, gravado como METADADO.

        Não é usado para filtrar — a deduplicação por conteúdo
        foi medida e não separa os casos aqui. Fica no índice
        para deduplicar offline depois, com métrica melhor e sem
        pressa.
        """

        pequeno = self._thumb(frame)

        bits = (pequeno > pequeno.mean()).flatten()

        return "".join("1" if b else "0" for b in bits)

    def _sha256(self, caminho):
        """
        Identidade exata do arquivo, para o treino poder
        detectar duplicata entre sessões e conferir integridade.
        """

        digest = hashlib.sha256()

        with caminho.open("rb") as arquivo:

            for bloco in iter(lambda: arquivo.read(1 << 20), b""):

                digest.update(bloco)

        return digest.hexdigest()

    # =====================================================
    # ESTATÍSTICAS
    # =====================================================

    def stats(self):

        return {
            "saved": self.saved,
            "duplicates": self.duplicates,
            "dropped": self.dropped,
            "mb": round(self.bytes_written / 1_000_000, 1),
            "by_action": dict(self.by_action),
        }
