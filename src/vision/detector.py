"""Detector por template matching: 2-stage search, ROI/category filters, NMS."""

from pathlib import Path

import json
import math
import queue
import threading
import time

import cv2
import numpy as np

from core import log
from core.metrics import formata_duracao
from core.config import (
    CATEGORY_ROIS,
    CATEGORY_THRESHOLDS,
    COARSE_MARGIN,
    COARSE_SCALE,
    COLOR_THRESHOLD,
    DETECTOR_DEBUG_INTERVAL,
    DETECTOR_DEBUG_MISSES,
    LEARN_RESOLUTION_TEMPLATES,
    MAX_DETECTION_AGE,
    TEMPLATES_WATCH_INTERVAL,
    MAX_MATCHES_PER_TEMPLATE,
    NMS_IOU,
    REFINE_SLACK,
    SHAPE_THRESHOLD,
    TEMPLATE_SCALE_BASIS,
    TEMPLATE_SCALE_STEPS,
    VISION_PRIORITY_STOP,
    COARSE_SCALE_MIN,
    COARSE_SCALE_STEP,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    BATTERY_POLL_INTERVAL,
    BATTERY_WARNING_LEVEL,
    CYCLE_STALL_FACTOR,
    SHOW_BATTERY,
    SHOW_CYCLE_TIME,
    SHOW_DETECTION_LABELS,
    SHOW_DETECTION_LAG,
    SHOW_FPS,
)

logger = log.get("detector")


# Relativo ao arquivo, não ao cwd — não depende de rodar da raiz do repo.
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Templates comuns a todo device. Pastas irmãs desta, nomeadas "LARGURAxALTURA"
# (na resolução real do frame, sem normalizar orientação), guardam extras/
# overrides só daquele device — mescladas com estas em runtime.
DEFAULT_TEMPLATES_DIR = TEMPLATES_DIR / "default"

# Capturas manuais fora da resolução de referência (tools/template_selector.py):
# ficam aqui, cruas, sem entrar no default nem em nenhuma resolução — não são
# carregadas em runtime.
FALLBACK_SELECTOR_DIR = TEMPLATES_DIR / "default_selector"

# Abaixo deste tamanho a redução destrói o template.
MIN_COARSE_SIDE = 12


class Detector:

    def __init__(
        self,
        threshold=SHAPE_THRESHOLD,
        color_threshold=COLOR_THRESHOLD,
        category_thresholds=None,
        category_rois=None,
        coarse_scale=COARSE_SCALE,
        coarse_margin=COARSE_MARGIN,
    ):

        # Similaridade do formato/padrão
        self.threshold = threshold

        # Similaridade das cores
        self.color_threshold = color_threshold

        self.category_thresholds = (
            CATEGORY_THRESHOLDS
            if category_thresholds is None
            else category_thresholds
        )

        self.category_rois = (
            CATEGORY_ROIS
            if category_rois is None
            else category_rois
        )

        self.coarse_scale = coarse_scale
        self.coarse_margin = coarse_margin

        self.templates = []

        # Índice por categoria: com filtro de estado a passada olha 2 categorias, não 43.
        self.by_category = {}

        # Templates são reescalados para o frame (1x por resolução), não o
        # inverso: evita letterbox do frame inteiro e conversão de coordenada
        # por passada. No caso comum (frame já na referência) nada disso roda.
        self._scaled_cache = {}

        # ROI em pixels por (categoria, largura, altura).
        self._roi_cache = {}

        # Templates extras/override por device: (frame_width, frame_height) ->
        # {categoria: [template]}. Carregados 1x do disco, sem reescala (já
        # nasceram na resolução do device).
        self._resolution_templates = {}

        # (chave, categoria, nome) -> confiança do match que gerou a versão
        # salva do override. Nova candidata só substitui se vier com
        # confiança maior — o override melhora com o tempo, nunca piora.
        self._fidelity = {}

        # Protege _fidelity/_resolution_templates/_scaled_cache de corrida
        # entre a thread de visão (detect) e a thread de aprendizado.
        self._learn_lock = threading.Lock()

        # Fila descartável: a thread de visão só copia o recorte (barato) e
        # devolve — quem grava em disco é a thread de aprendizado, para
        # não bloquear o detect() com I/O.
        self._learn_queue = queue.Queue()

        self._learn_thread = threading.Thread(
            target=self._learn_worker,
            name="template-learner",
            daemon=True,
        )

        self._learn_thread.start()

        # (mtime mais recente, total de .png) na última checagem — permite
        # notar template novo/editado na pasta sem reiniciar o bot.
        self._templates_signature_cache = None
        self._templates_checked_at = 0.0

        # Templates olhados na última passada.
        self.last_searched = 0

        # Só populado com DETECTOR_DEBUG_MISSES ligado (custo: 1 bool por
        # match). Distingue "não detectou" de "detectou e o threshold
        # cortou" — correções opostas com o mesmo sintoma (bot parado).
        self._best = {}
        self._best_color = {}

        self._debug_category = None
        self._debug_at = 0.0

        # Relatório em texto para o painel: com o painel ligado o console
        # fica em WARNING e o log INFO não aparece.
        self._miss_text = ""
        self._miss_at = 0.0

        self.load_templates()

    def _coarse_scale_for(self, smallest_side):
        """Menor escala com MIN_COARSE_SIDE de lado; arredonda para cima na grade (opção mais segura)."""

        if smallest_side <= 0:
            return self.coarse_scale

        exata = MIN_COARSE_SIDE / smallest_side

        na_grade = (
            math.ceil(exata / COARSE_SCALE_STEP)
            * COARSE_SCALE_STEP
        )

        # round() evita 0.15000000000000002 virar chave duplicada no cache de escalas.
        return round(
            min(
                self.coarse_scale,
                max(COARSE_SCALE_MIN, na_grade),
            ),
            4,
        )

    def load_templates(self):

        with self._learn_lock:
            self._load_templates_locked()

    def _load_templates_locked(self):
        """Corpo de load_templates() protegido pelo lock — a thread de
        aprendizado também muda _resolution_templates/_fidelity."""

        self.templates.clear()
        self.by_category.clear()
        self._scaled_cache.clear()
        self._resolution_templates.clear()
        self._fidelity.clear()

        if not TEMPLATES_DIR.exists():

            logger.error(
                "Pasta de templates não encontrada: %s",
                TEMPLATES_DIR,
            )

            self._templates_signature_cache = (0.0, 0)
            self._templates_checked_at = time.monotonic()

            return

        self.templates = self._load_category_dirs(
            DEFAULT_TEMPLATES_DIR,
            origin="default",
        )

        for template in self.templates:

            self.by_category.setdefault(
                template["category"],
                [],
            ).append(template)

        for resolution_dir in sorted(TEMPLATES_DIR.iterdir()):

            if (
                not resolution_dir.is_dir()
                or resolution_dir in (
                    DEFAULT_TEMPLATES_DIR,
                    FALLBACK_SELECTOR_DIR,
                )
            ):
                continue

            resolucao = self._parse_resolution_dir_name(
                resolution_dir.name
            )

            if resolucao is None:

                logger.warning(
                    "Pasta de templates ignorada (nome não é "
                    "'LARGURAxALTURA'): %s",
                    resolution_dir.name,
                )

                continue

            extras = self._load_category_dirs(
                resolution_dir,
                origin="override",
            )

            por_categoria = {}

            for template in extras:

                por_categoria.setdefault(
                    template["category"],
                    [],
                ).append(template)

            if por_categoria:

                self._resolution_templates[resolucao] = por_categoria

                self._load_fidelity(resolucao, resolution_dir)

        logger.info(
            "Templates: %d default em %d categorias %s | "
            "overrides para %d resolução(ões) %s",
            len(self.templates),
            len(self.by_category),
            {
                categoria: len(lista)
                for categoria, lista in sorted(
                    self.by_category.items()
                )
            },
            len(self._resolution_templates),
            sorted(
                f"{w}x{h}"
                for w, h in self._resolution_templates
            ),
        )

        # Sem template não há detecção — mesmo sintoma de detector quebrado (bot parado).
        if not self.templates:

            logger.error(
                "NENHUM template carregado de %s — o detector "
                "não tem com o que comparar, e o bot não vai "
                "agir.",
                DEFAULT_TEMPLATES_DIR,
            )

        self._templates_signature_cache = self._templates_signature()
        self._templates_checked_at = time.monotonic()

    def _templates_signature(self):
        """(mtime mais recente, total de .png) nas pastas realmente
        carregadas — muda com qualquer template novo, editado ou removido,
        sem precisar comparar conteúdo arquivo por arquivo."""

        pastas = [DEFAULT_TEMPLATES_DIR]

        if TEMPLATES_DIR.exists():

            for pasta in sorted(TEMPLATES_DIR.iterdir()):

                if not pasta.is_dir():
                    continue

                if pasta in (
                    DEFAULT_TEMPLATES_DIR,
                    FALLBACK_SELECTOR_DIR,
                ):
                    continue

                if self._parse_resolution_dir_name(pasta.name) is None:
                    continue

                pastas.append(pasta)

        ultima = 0.0
        total = 0

        for pasta in pastas:

            if not pasta.exists():
                continue

            for arquivo in pasta.rglob("*.png"):

                try:
                    mtime = arquivo.stat().st_mtime

                except OSError:
                    continue

                ultima = max(ultima, mtime)
                total += 1

        return (round(ultima, 3), total)

    def _maybe_reload_templates(self):
        """Recarrega do disco quando a pasta de templates muda — permite
        adicionar/editar templates (ex.: tools/template_selector.py) sem
        reiniciar o bot."""

        if not TEMPLATES_WATCH_INTERVAL:
            return

        agora = time.monotonic()

        if (
            agora - self._templates_checked_at
            < TEMPLATES_WATCH_INTERVAL
        ):
            return

        self._templates_checked_at = agora

        assinatura = self._templates_signature()

        if assinatura == self._templates_signature_cache:
            return

        logger.info(
            "Pasta de templates mudou — recarregando."
        )

        self.load_templates()

    def _load_category_dirs(self, base_dir, origin):
        """[template] de todas as subpastas de categoria dentro de base_dir."""

        templates = []

        if not base_dir.exists():
            return templates

        for category_dir in sorted(base_dir.iterdir()):

            if not category_dir.is_dir():
                continue

            category = category_dir.name

            for image_path in sorted(
                category_dir.glob("*.png")
            ):

                template = self._load_template(
                    category,
                    image_path,
                    origin,
                )

                if template is not None:
                    templates.append(template)

        return templates

    @staticmethod
    def _parse_resolution_dir_name(name):
        """"LARGURAxALTURA" -> (largura, altura), ou None se o nome não seguir o padrão."""

        largura, _, altura = name.partition("x")

        if not largura.isdigit() or not altura.isdigit():
            return None

        return int(largura), int(altura)

    @staticmethod
    def _fidelity_path(resolution_dir):
        return resolution_dir / "_fidelity.json"

    def _load_fidelity(self, chave, resolution_dir):
        """Carrega a confiança salva de cada override desta resolução — sem
        entrada aqui, a próxima candidata (mesmo fraca) substitui de cara."""

        caminho = self._fidelity_path(resolution_dir)

        if not caminho.exists():
            return

        try:

            dados = json.loads(caminho.read_text(encoding="utf-8"))

        except (OSError, ValueError) as error:

            logger.warning(
                "Não consegui ler %s: %s",
                caminho,
                error,
            )

            return

        for chave_item, confianca in dados.items():

            categoria, _, nome = chave_item.partition("/")

            if not categoria or not nome:
                continue

            self._fidelity[(chave, categoria, nome)] = float(confianca)

    def _save_fidelity(self, chave):
        """Persiste a fidelidade dos overrides desta resolução — sem isto, um
        restart esquece a confiança e a próxima candidata substitui à toa."""

        resolution_dir = TEMPLATES_DIR / f"{chave[0]}x{chave[1]}"

        dados = {
            f"{categoria}/{nome}": confianca
            for (res, categoria, nome), confianca in self._fidelity.items()
            if res == chave
        }

        try:

            self._fidelity_path(resolution_dir).write_text(
                json.dumps(dados, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        except OSError as error:

            logger.warning(
                "Não consegui salvar %s: %s",
                self._fidelity_path(resolution_dir),
                error,
            )

    def _load_template(self, category, image_path, origin):

        raw = cv2.imread(
            str(image_path),
            cv2.IMREAD_UNCHANGED,
        )

        if raw is None:

            logger.error(
                "Erro ao carregar: %s",
                image_path,
            )

            return None

        mask = None

        if raw.ndim == 3 and raw.shape[2] == 4:

            alpha = raw[:, :, 3]

            image = raw[:, :, :3]

            # Só vale a pena se houver transparência real.
            if (alpha < 255).any():

                mask = (alpha > 0).astype(np.uint8) * 255

        elif raw.ndim == 2:

            image = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)

        else:

            image = raw

        return self._build_template(
            category,
            image_path.name,
            image,
            mask,
            origin,
        )

    def _build_template(self, category, name, image, mask, origin):
        """Separado do carregamento: mesmo caminho para o PNG original e para a versão reescalada, para as duas não divergirem."""

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # HSV calculado uma vez aqui — não a cada candidato em
        # _color_similarity (imagem nunca muda).
        template_hsv = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2HSV,
        ).astype(np.int16)

        height, width = gray.shape[:2]

        # Escala própria deste template (não a global, travada pelo menor de todos).
        scale = self._coarse_scale_for(min(height, width))

        coarse_gray = None
        coarse_mask = None

        if (
            scale < 1.0
            and min(height, width) * scale >= MIN_COARSE_SIDE
        ):

            coarse_gray = cv2.resize(
                gray,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )

            if mask is not None:

                coarse_mask = cv2.resize(
                    mask,
                    (
                        coarse_gray.shape[1],
                        coarse_gray.shape[0],
                    ),
                    interpolation=cv2.INTER_NEAREST,
                )

        return {
            "category": category,
            "name": name,
            "origin": origin,

            "image": image,
            "gray": gray,
            "hsv": template_hsv,
            "mask": mask,

            # Bool pré-calculado: `mask > 0` por candidato é
            # uma passada a mais numa máscara constante.
            "mask_valid": None if mask is None else mask > 0,

            "coarse_gray": coarse_gray,
            "coarse_mask": coarse_mask,
            "coarse_scale": scale,

            "width": width,
            "height": height,
        }

    @staticmethod
    def _reference_size(frame_width, frame_height):
        """Referência na orientação do frame: o jogo roda deitado (2400x1080) mas a referência está escrita 1080x2400 — sem a troca a razão sai absurda e nada detecta."""

        ref_width = REFERENCE_WIDTH
        ref_height = REFERENCE_HEIGHT

        if (frame_width > frame_height) != (
            ref_width > ref_height
        ):
            ref_width, ref_height = ref_height, ref_width

        return ref_width, ref_height

    @classmethod
    def _frame_scale(cls, frame_width, frame_height):
        """Fator de escala do template para este frame, via TEMPLATE_SCALE_BASIS — não existe critério universal porque o que varia entre aparelhos é proporção, não só resolução."""

        if not REFERENCE_WIDTH or not REFERENCE_HEIGHT:
            return 1.0

        if not frame_width or not frame_height:
            return 1.0

        ref_width, ref_height = cls._reference_size(
            frame_width,
            frame_height,
        )

        por_largura = frame_width / ref_width
        por_altura = frame_height / ref_height

        if TEMPLATE_SCALE_BASIS == "width":
            return por_largura

        if TEMPLATE_SCALE_BASIS == "height":
            return por_altura

        if TEMPLATE_SCALE_BASIS == "min":
            return min(por_largura, por_altura)

        if TEMPLATE_SCALE_BASIS == "long_side":

            return (
                max(frame_width, frame_height)
                / max(ref_width, ref_height)
            )

        if TEMPLATE_SCALE_BASIS != "short_side":

            logger.warning(
                "TEMPLATE_SCALE_BASIS desconhecido (%r) — "
                "usando 'short_side'.",
                TEMPLATE_SCALE_BASIS,
            )

        # UI de jogo mobile se ancora na dimensão ESTREITA: o
        # excedente da outra vira mais cenário, não interface
        # maior.
        return (
            min(frame_width, frame_height)
            / min(ref_width, ref_height)
        )

    @classmethod
    def _is_reference(cls, frame_width, frame_height):
        """Frame já na resolução de referência (em qualquer orientação) — caso de custo zero, nada a reescalar."""

        return (frame_width, frame_height) in (
            (REFERENCE_WIDTH, REFERENCE_HEIGHT),
            (REFERENCE_HEIGHT, REFERENCE_WIDTH),
        )

    def _rescale_template(self, template, scale):
        """Mesmo template na escala do frame, ou None se encolher demais."""

        # Escala 1.0: devolve o original — resize daria a mesma dimensão
        # de volta, perdendo nitidez à toa.
        if abs(scale - 1.0) <= 0.005:
            return template

        width = int(round(template["width"] * scale))
        height = int(round(template["height"] * scale))

        if width < 4 or height < 4:
            return None

        interpolation = (
            cv2.INTER_AREA
            if scale < 1.0
            else cv2.INTER_LINEAR
        )

        image = cv2.resize(
            template["image"],
            (width, height),
            interpolation=interpolation,
        )

        mask = None

        if template["mask"] is not None:

            mask = cv2.resize(
                template["mask"],
                (width, height),
                interpolation=cv2.INTER_NEAREST,
            )

        return self._build_template(
            template["category"],
            template["name"],
            image,
            mask,
            template["origin"],
        )

    def _templates_for(self, frame_width, frame_height):
        """Templates deste frame por categoria: default (reescalado, ou direto se já na referência) + overrides da pasta do device, cacheados por resolução."""

        chave = (frame_width, frame_height)

        cacheados = self._scaled_cache.get(chave)

        if cacheados is not None:
            return cacheados

        # Snapshot rápido sob lock: a thread de aprendizado também escreve
        # em _resolution_templates/_scaled_cache, e o resto desta função
        # (reescala, merge) é caro demais pra fazer com o lock preso.
        with self._learn_lock:

            extras_brutos = self._resolution_templates.get(chave)

            extras = (
                None
                if extras_brutos is None
                else {
                    categoria: list(lista)
                    for categoria, lista in extras_brutos.items()
                }
            )

            # Já na referência e sem override: nada a reescalar nem mesclar. É o caminho de custo zero.
            if self._is_reference(frame_width, frame_height) and not extras:

                cacheados = self.by_category
                self._scaled_cache[chave] = cacheados

                return cacheados

        default_categorias = (
            self.by_category
            if self._is_reference(frame_width, frame_height)
            else self._rescale_defaults(frame_width, frame_height)
        )

        if not extras:

            cacheados = default_categorias

        else:

            # Override não substitui o default na busca — só entra na
            # frente. Se ele não bater neste frame, o default do mesmo
            # nome ainda está na lista e pode salvar a detecção.
            cacheados = {
                categoria: list(lista)
                for categoria, lista in default_categorias.items()
            }

            for categoria, lista in extras.items():

                cacheados[categoria] = (
                    lista + cacheados.get(categoria, [])
                )

        with self._learn_lock:
            self._scaled_cache[chave] = cacheados

        return cacheados

    def _rescale_defaults(self, frame_width, frame_height):
        """Templates default reescalados para este frame, em várias escalas ao redor da base (nenhuma aposta única: 8% de erro de escala já derruba a confiança abaixo de 0.95, e a supressão por sobreposição + ordenação por confiança escolhe o melhor entre elas)."""

        base = self._frame_scale(frame_width, frame_height)

        escalas_base = sorted(
            {
                round(base * passo, 4)
                for passo in TEMPLATE_SCALE_STEPS
                if passo > 0
            }
        ) or [round(base, 4)]

        categorias = {}

        for template in self.templates:

            for escala in escalas_base:

                variante = self._rescale_template(
                    template,
                    escala,
                )

                if variante is None:
                    continue

                categorias.setdefault(
                    variante["category"],
                    [],
                ).append(variante)

        logger.info(
            "Frame %dx%d fora da referência %dx%d — %d "
            "templates default em %d escala(s) %s (base %.3f por '%s').",
            frame_width,
            frame_height,
            REFERENCE_WIDTH,
            REFERENCE_HEIGHT,
            sum(len(lista) for lista in categorias.values()),
            len(escalas_base),
            escalas_base,
            base,
            TEMPLATE_SCALE_BASIS,
        )

        return categorias

    def _queue_learn_candidate(
        self,
        frame_color,
        template,
        x,
        y,
        frame_width,
        frame_height,
        confidence,
    ):
        """Chamado de dentro do detect() — tem que ser barato. Só copia o
        recorte (pequeno) e entrega pra thread de aprendizado decidir; nada
        de disco aqui, pra não atrasar a visão."""

        chave = (frame_width, frame_height)
        categoria = template["category"]
        nome = template["name"]

        # Checagem sem lock: leitura de dict é uma operação atômica o
        # bastante pra esse fim — o pior caso é uma candidata a mais na
        # fila, que a thread de aprendizado descarta na conferência final.
        if confidence <= self._fidelity.get((chave, categoria, nome), 0.0):
            return

        altura = template["height"]
        largura = template["width"]

        crop = frame_color[y:y + altura, x:x + largura]

        if crop.shape[:2] != (altura, largura):
            return

        try:

            self._learn_queue.put_nowait(
                (categoria, nome, chave, crop.copy(), confidence)
            )

        except queue.Full:
            pass

    def _learn_worker(self):
        """Roda numa thread própria: grava em disco e atualiza os overrides
        em uso sem bloquear o detect() da thread de visão."""

        while True:

            item = self._learn_queue.get()

            try:

                self._process_learn_candidate(*item)

            except Exception:

                logger.exception(
                    "Erro processando candidata de aprendizado"
                )

    def _process_learn_candidate(
        self,
        categoria,
        nome,
        chave,
        crop,
        confidence,
    ):

        fidelity_id = (chave, categoria, nome)

        with self._learn_lock:

            if confidence <= self._fidelity.get(fidelity_id, 0.0):
                return

        frame_width, frame_height = chave

        destino_dir = (
            TEMPLATES_DIR
            / f"{frame_width}x{frame_height}"
            / categoria
        )

        destino_dir.mkdir(parents=True, exist_ok=True)

        destino = destino_dir / nome

        if not cv2.imwrite(str(destino), crop):

            logger.warning(
                "Não conseguiu gravar template aprendido: %s",
                destino,
            )

            return

        novo = self._build_template(
            categoria,
            nome,
            crop,
            None,
            "override",
        )

        with self._learn_lock:

            # Outra candidata pode ter vencido enquanto gravávamos.
            if confidence <= self._fidelity.get(fidelity_id, 0.0):
                return

            anterior = self._fidelity.get(fidelity_id)

            self._fidelity[fidelity_id] = confidence

            lista = self._resolution_templates.setdefault(
                chave, {}
            ).setdefault(categoria, [])

            lista[:] = [t for t in lista if t["name"] != nome]

            lista.append(novo)

            # Cache mesclado desta resolução ficou velho: sem isto, a
            # passada seguinte continuaria usando a versão antiga.
            self._scaled_cache.pop(chave, None)

            self._save_fidelity(chave)

        logger.info(
            "Override %dx%d %s/%s: fidelidade %s -> %.4f",
            frame_width,
            frame_height,
            categoria,
            nome,
            "-" if anterior is None else f"{anterior:.4f}",
            confidence,
        )

    def _color_similarity(
        self,
        frame,
        template,
        x,
        y,
    ):
        """Compara em HSV: matiz é circular (distância 179<->0 é 1), pesado por saturação (dessaturado = matiz é ruído); brilho pesa pouco (varia com animação/iluminação)."""

        template_height = template["height"]
        template_width = template["width"]

        roi = frame[
            y:y + template_height,
            x:x + template_width
        ]

        if roi.shape[:2] != (template_height, template_width):
            return 0.0

        # Só a ROI é convertida: o HSV do template já veio
        # pronto de _build_template.
        roi_hsv = cv2.cvtColor(
            roi,
            cv2.COLOR_BGR2HSV,
        ).astype(np.int16)

        template_hsv = template["hsv"]

        # OpenCV usa H em 0..179 — volta completa é 180, distância máxima real é 90.
        hue_difference = np.abs(
            roi_hsv[:, :, 0] - template_hsv[:, :, 0]
        )

        hue_difference = np.minimum(
            hue_difference,
            180 - hue_difference,
        )

        hue_difference = hue_difference / 90.0

        saturation_difference = np.abs(
            roi_hsv[:, :, 1] - template_hsv[:, :, 1]
        ) / 255.0

        value_difference = np.abs(
            roi_hsv[:, :, 2] - template_hsv[:, :, 2]
        ) / 255.0

        # Num pixel cinza o matiz não significa nada — por isso o peso por saturação.
        saturation_weight = np.minimum(
            roi_hsv[:, :, 1],
            template_hsv[:, :, 1],
        ) / 255.0

        difference = (
            0.60 * hue_difference * saturation_weight
            + 0.30 * saturation_difference
            + 0.10 * value_difference
        )

        valid = template["mask_valid"]

        if valid is not None:

            if not valid.any():
                return 0.0

            mean_difference = float(
                difference[valid].mean()
            )

        else:

            mean_difference = float(difference.mean())

        return max(0.0, 1.0 - mean_difference)

    def _resolve_roi(
        self,
        category,
        frame_width,
        frame_height,
    ):
        """Converte a ROI fracionária da categoria em pixels: (x1, y1, x2, y2)."""

        chave = (category, frame_width, frame_height)

        cacheada = self._roi_cache.get(chave)

        if cacheada is not None:
            return cacheada

        resolvida = self._compute_roi(
            category,
            frame_width,
            frame_height,
        )

        self._roi_cache[chave] = resolvida

        return resolvida

    def _compute_roi(
        self,
        category,
        frame_width,
        frame_height,
    ):

        roi = self.category_rois.get(category)

        if not roi:

            return (0, 0, frame_width, frame_height)

        x1 = max(0, int(roi[0] * frame_width))
        y1 = max(0, int(roi[1] * frame_height))

        x2 = min(frame_width, int(roi[2] * frame_width))
        y2 = min(frame_height, int(roi[3] * frame_height))

        if x2 <= x1 or y2 <= y1:

            logger.warning(
                "ROI inválida para %s: %s",
                category,
                roi,
            )

            return (0, 0, frame_width, frame_height)

        return (x1, y1, x2, y2)

    def detect(self, frame, categories=None):
        """Detecções aprovadas, ordenadas por confiança (maior primeiro). `categories` filtra a busca (permite a StateMachine procurar 2 templates em vez de 43)."""

        self._maybe_reload_templates()

        if frame is None:
            return []

        if DETECTOR_DEBUG_MISSES:

            self._best = {}
            self._best_color = {}

        if frame.ndim == 3:

            frame_gray = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY,
            )

            frame_color = frame

        else:

            frame_gray = frame

            frame_color = cv2.cvtColor(
                frame,
                cv2.COLOR_GRAY2BGR,
            )

        frame_height, frame_width = frame_gray.shape[:2]

        # Frames reduzidos sob demanda (cada template tem sua escala): no
        # estado UPGRADE, com 4 templates, sai 1 resize e não 7.
        coarse_frames = {}

        def coarse_for(scale):

            if scale >= 1.0:
                return None

            if scale not in coarse_frames:

                coarse_frames[scale] = cv2.resize(
                    frame_gray,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )

            return coarse_frames[scale]

        # Templates deste frame por categoria (índice original se já na referência).
        por_categoria = self._templates_for(
            frame_width,
            frame_height,
        )

        # `categories` vem na ordem das regras do estado; a StateMachine age
        # na primeira que casar, então parar aqui poupa trabalho que nunca
        # viraria ação (medido: 264ms -> ~20ms quando uma regra de cima casa).
        # Parada é por categoria, nunca dentro dela (evita perder detecções
        # repetidas da mesma categoria). Sem filtro, nada é interrompido —
        # caminho do overlay de diagnóstico e do dataset, que querem a tela inteira.
        if categories is not None:

            ordem = []
            vistas = set()

            for category in categories:

                if category in vistas:
                    continue

                vistas.add(category)

                if category in por_categoria:
                    ordem.append(category)

            parar_na_prioridade = VISION_PRIORITY_STOP

        else:

            ordem = list(por_categoria)

            parar_na_prioridade = False

        # Quantos templates a passada de fato olhou: é o
        # número que explica um HUD vazio ("procurou 0").
        self.last_searched = 0

        detections = []

        procuradas = []

        for category in ordem:

            procuradas.append(category)

            min_threshold = self.category_thresholds.get(
                category,
                self.threshold,
            )

            roi = self._resolve_roi(
                category,
                frame_width,
                frame_height,
            )

            # Onde o _match anota o melhor match desta
            # categoria, para o diagnóstico.
            self._debug_category = category

            achou = False

            for template in por_categoria[category]:

                if (
                    template["width"] > frame_width
                    or template["height"] > frame_height
                ):
                    continue

                self.last_searched += 1

                candidates = self._search(
                    frame_gray,
                    coarse_for(template["coarse_scale"]),
                    template,
                    min_threshold,
                    roi,
                )

                for x, y, confidence in candidates:

                    color_similarity = self._color_similarity(
                        frame_color,
                        template,
                        x,
                        y,
                    )

                    if DETECTOR_DEBUG_MISSES:

                        self._best_color[category] = max(
                            self._best_color.get(
                                category,
                                0.0,
                            ),
                            color_similarity,
                        )

                    if (
                        color_similarity
                        < self.color_threshold
                    ):
                        continue

                    achou = True

                    detections.append(
                        {
                            "category": category,
                            "name": template["name"],
                            "origin": template["origin"],

                            "confidence": float(confidence),
                            "color_similarity": float(
                                color_similarity
                            ),
                            "min_threshold": float(
                                min_threshold
                            ),

                            "x": int(x),
                            "y": int(y),

                            "width": template["width"],
                            "height": template["height"],
                        }
                    )

                    if LEARN_RESOLUTION_TEMPLATES:

                        self._queue_learn_candidate(
                            frame_color,
                            template,
                            x,
                            y,
                            frame_width,
                            frame_height,
                            confidence,
                        )

            if achou and parar_na_prioridade:
                break

        # Vários templates da mesma categoria acertam o mesmo ícone — sem
        # supressão, uma comida vira 5 detecções.
        if DETECTOR_DEBUG_MISSES:

            # Só as categorias que a passada realmente olhou — com parada por
            # prioridade, listar as de baixo como "não detectado" seria falso.
            self._report_misses(set(procuradas), detections)

        detections = self._suppress(detections)

        # Maior confiança primeiro: a StateMachine passa a
        # agir sobre o MELHOR match, não sobre o primeiro
        # em ordem alfabética de arquivo.
        detections.sort(
            key=lambda item: item["confidence"],
            reverse=True,
        )

        return detections

    def _search(
        self,
        frame_gray,
        coarse_frame,
        template,
        min_threshold,
        roi,
    ):
        """Devolve [(x, y, confiança)] em resolução cheia."""

        coarse_template = template["coarse_gray"]

        # Sem estágio grosso: busca direta.
        if coarse_frame is None or coarse_template is None:

            return self._match(
                frame_gray,
                template["gray"],
                template["mask"],
                min_threshold,
                roi,
                MAX_MATCHES_PER_TEMPLATE,
            )

        # Estágio 1 (grosso): threshold mais permissivo — a redução degrada
        # a confiança, e descartar aqui é irreversível.
        scale = template["coarse_scale"]

        coarse_roi = (
            int(roi[0] * scale),
            int(roi[1] * scale),
            min(
                coarse_frame.shape[1],
                int(round(roi[2] * scale)),
            ),
            min(
                coarse_frame.shape[0],
                int(round(roi[3] * scale)),
            ),
        )

        coarse_hits = self._match(
            coarse_frame,
            coarse_template,
            template["coarse_mask"],
            max(0.0, min_threshold - self.coarse_margin),
            coarse_roi,
            MAX_MATCHES_PER_TEMPLATE,
        )

        if not coarse_hits:
            return []

        # Estágio 2 (fino): reconfirma cada candidato em resolução cheia
        # numa janela — a confiança devolvida já é comparável à versão sem estágios.
        results = []

        template_height = template["height"]
        template_width = template["width"]

        frame_height, frame_width = frame_gray.shape[:2]

        for coarse_x, coarse_y, _ in coarse_hits:

            estimated_x = int(coarse_x / scale)
            estimated_y = int(coarse_y / scale)

            window = (
                max(0, estimated_x - REFINE_SLACK),
                max(0, estimated_y - REFINE_SLACK),
                min(
                    frame_width,
                    estimated_x + template_width
                    + REFINE_SLACK,
                ),
                min(
                    frame_height,
                    estimated_y + template_height
                    + REFINE_SLACK,
                ),
            )

            refined = self._match(
                frame_gray,
                template["gray"],
                template["mask"],
                min_threshold,
                window,

                # Na janela fina só interessa o melhor ponto.
                1,
            )

            results.extend(refined)

        return results

    def _match(
        self,
        image,
        template_image,
        mask,
        min_threshold,
        roi,
        max_matches,
    ):
        """matchTemplate numa região; devolve os máximos locais acima do threshold em coordenada absoluta da imagem."""

        x1, y1, x2, y2 = roi

        template_height, template_width = (
            template_image.shape[:2]
        )

        if (
            x2 - x1 < template_width
            or y2 - y1 < template_height
        ):
            return []

        region = image[y1:y2, x1:x2]

        # Máscara só é suportada em TM_CCORR_NORMED/TM_SQDIFF; sem máscara
        # mantemos TM_CCOEFF_NORMED para não mudar a escala dos thresholds.
        if mask is not None:

            result = cv2.matchTemplate(
                region,
                template_image,
                cv2.TM_CCORR_NORMED,
                mask=mask,
            )

        else:

            result = cv2.matchTemplate(
                region,
                template_image,
                cv2.TM_CCOEFF_NORMED,
            )

        result = np.nan_to_num(
            result,
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )

        # Máximo bruto antes do corte — é a informação que o threshold
        # esconde e que o diagnóstico precisa.
        if DETECTOR_DEBUG_MISSES and result.size:

            if self._debug_category is not None:

                self._best[self._debug_category] = max(
                    self._best.get(self._debug_category, 0.0),
                    float(result.max()),
                )

        if max_matches == 1:

            _, confidence, _, location = cv2.minMaxLoc(result)

            if confidence < min_threshold:
                return []

            return [
                (
                    x1 + location[0],
                    y1 + location[1],
                    confidence,
                )
            ]

        ys, xs = np.where(result >= min_threshold)

        if len(xs) == 0:
            return []

        confidences = result[ys, xs]

        # Melhores primeiro, para a supressão manter o bom.
        order = np.argsort(-confidences)

        hits = []

        for index in order:

            x = int(xs[index]) + x1
            y = int(ys[index]) + y1

            confidence = float(confidences[index])

            # Descarta vizinho do mesmo pico.
            if any(
                abs(x - other_x) < template_width * 0.5
                and abs(y - other_y) < template_height * 0.5
                for other_x, other_y, _ in hits
            ):
                continue

            hits.append((x, y, confidence))

            if len(hits) >= max_matches:
                break

        return hits

    def _report_misses(self, wanted, detections):
        """Por categoria não encontrada, o melhor match visto: F:0.931/0.98 = threshold cortou (baixe o threshold); F:0.412/0.98 = template não parece com a tela (threshold não ajuda)."""

        agora = time.monotonic()

        if agora - self._debug_at < DETECTOR_DEBUG_INTERVAL:
            return

        self._debug_at = agora

        encontradas = {d["category"] for d in detections}

        procuradas = (
            wanted
            if wanted is not None
            else {t["category"] for t in self.templates}
        )

        faltando = sorted(procuradas - encontradas)

        if not faltando:

            # Apaga o relatório anterior: texto de problema
            # já resolvido é pior que texto nenhum.
            self._miss_text = ""
            self._miss_at = agora

            return

        partes = []

        for categoria in faltando:

            corte = self.category_thresholds.get(
                categoria,
                self.threshold,
            )

            forma = self._best.get(categoria)
            cor = self._best_color.get(categoria)

            texto = f"{categoria} F:"
            texto += "-" if forma is None else f"{forma:.3f}"
            texto += f"/{corte:.2f}"

            # Cor só quando o formato passou: senão a
            # comparação de cor nem aconteceu, e um "C:-"
            # pareceria falha.
            if cor is not None:
                texto += (
                    f" C:{cor:.3f}"
                    f"/{self.color_threshold:.2f}"
                )

            partes.append(texto)

        self._miss_text = "  ".join(partes)
        self._miss_at = agora

        logger.info(
            "não detectado (melhor match / corte): %s",
            self._miss_text,
        )

    def miss_report(self):
        """Último relatório, ou "" se ausente ou velho demais (texto de minutos atrás pareceria atual no painel)."""

        if not DETECTOR_DEBUG_MISSES or not self._miss_text:
            return ""

        if (
            time.monotonic() - self._miss_at
            > DETECTOR_DEBUG_INTERVAL * 3
        ):
            return ""

        return self._miss_text

    def _suppress(self, detections):
        """Mantém, por categoria, a melhor detecção de cada grupo de caixas sobrepostas."""

        if len(detections) < 2:
            return detections

        by_category = {}

        for detection in detections:

            by_category.setdefault(
                detection["category"],
                [],
            ).append(detection)

        kept = []

        for group in by_category.values():

            group.sort(
                key=lambda item: item["confidence"],
                reverse=True,
            )

            for candidate in group:

                overlapped = any(
                    self._iou(candidate, chosen) > NMS_IOU
                    for chosen in kept
                    if chosen["category"]
                    == candidate["category"]
                )

                if not overlapped:

                    kept.append(candidate)

        return kept

    @staticmethod
    def _iou(a, b):

        ax2 = a["x"] + a["width"]
        ay2 = a["y"] + a["height"]

        bx2 = b["x"] + b["width"]
        by2 = b["y"] + b["height"]

        inter_width = min(ax2, bx2) - max(a["x"], b["x"])
        inter_height = min(ay2, by2) - max(a["y"], b["y"])

        if inter_width <= 0 or inter_height <= 0:
            return 0.0

        intersection = inter_width * inter_height

        union = (
            a["width"] * a["height"]
            + b["width"] * b["height"]
            - intersection
        )

        if union <= 0:
            return 0.0

        return intersection / union

    # Cor por categoria: dá para ver o que é o que sem ler o texto.
    COLORS = {
        "food": (0, 220, 255),
        "upgrade": (0, 255, 0),
        "up_upgrade": (0, 200, 120),
        "up_food": (120, 255, 0),
        "new_point": (255, 120, 0),
        "box": (255, 0, 220),
        "close": (0, 0, 255),
        "plane": (255, 255, 0),
        "build": (180, 180, 255),
        "fly": (200, 255, 255),
        "gray_max": (128, 128, 128),
        "gray_coin": (90, 160, 200),
        "open_store": (255, 180, 0),
        "renovate_coin": (0, 255, 255),
    }

    DEFAULT_COLOR = (255, 255, 255)

    # Cores do HUD
    HUD_OK = (0, 255, 0)
    HUD_BAD = (0, 0, 255)
    HUD_NEUTRAL = (255, 255, 255)

    def _hud_text(self, output, text, y, color, scale=0.8):
        """Texto com contorno escuro, legível sobre qualquer fundo do jogo."""

        for thickness, text_color in (
            (5, (0, 0, 0)),
            (2, color),
        ):

            cv2.putText(
                output,
                text,
                (20, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                scale,
                text_color,
                thickness,
                cv2.LINE_AA,
            )

    def draw(self, frame, detections, stats=None, scale=1.0):
        """stats: capture_fps, detect_fps, detect_ms, lag, battery, cycle — chaves ausentes não aparecem. `scale` reconverte detecções (em coords de frame cheio) para o frame reduzido que chega aqui, senão as caixas aparecem fora de lugar."""

        output = frame

        for detection in detections:

            x = int(detection["x"] * scale)
            y = int(detection["y"] * scale)

            width = int(detection["width"] * scale)
            height = int(detection["height"] * scale)

            category = detection["category"]

            color = self.COLORS.get(
                category,
                self.DEFAULT_COLOR,
            )

            cv2.rectangle(
                output,
                (x, y),
                (x + width, y + height),
                color,
                3,
            )

            if not SHOW_DETECTION_LABELS:
                continue

            text = (
                f"{category} "
                f"F:{detection['confidence']:.2f} "
                f"C:{detection.get('color_similarity', 0.0):.2f}"
            )

            text_y = max(y - 10, 24)

            # Contorno escuro + texto colorido.
            for thickness, text_color in (
                (6, (0, 0, 0)),
                (2, color),
            ):

                cv2.putText(
                    output,
                    text,
                    (x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    text_color,
                    thickness,
                    cv2.LINE_AA,
                )

        if not stats:
            return output

        y = 50

        # Dois números distintos de propósito: a captura pode ir a 60 fps
        # enquanto o detector faz 3 passadas por segundo — quem manda na
        # reação do bot é o detector.
        if SHOW_FPS:

            capture_fps = stats.get("capture_fps")

            if capture_fps is not None:

                self._hud_text(
                    output,
                    f"captura  {capture_fps:5.1f} fps",
                    y,
                    self.HUD_NEUTRAL,
                )

                y += 40

            detect_fps = stats.get("detect_fps")

            if detect_fps is not None:

                label = f"detector {detect_fps:5.1f} fps"

                detect_ms = stats.get("detect_ms")

                if detect_ms:

                    label += f"  {detect_ms:.0f} ms"

                # Abaixo de 1/MAX_DETECTION_AGE as detecções já nascem velhas
                # e a StateMachine para de clicar — mesmo limite da checagem de idade.
                floor = (
                    1.0 / MAX_DETECTION_AGE
                    if MAX_DETECTION_AGE
                    else 0.0
                )

                self._hud_text(
                    output,
                    label,
                    y,
                    self.HUD_OK
                    if detect_fps >= floor
                    else self.HUD_BAD,
                )

                y += 40

        # Sem isso, detecção atrasada parece detecção errada.
        lag = stats.get("lag")

        if SHOW_DETECTION_LAG and lag is not None:

            limit = (
                MAX_DETECTION_AGE
                if MAX_DETECTION_AGE
                else 1.0
            )

            self._hud_text(
                output,
                f"atraso   {lag * 1000:5.0f} ms",
                y,
                self.HUD_OK
                if lag <= limit * 0.5
                else self.HUD_BAD,
            )

            y += 40

        # Sessão que morre por bateria descarregada não deixa rastro no log — o bot só para de agir.
        battery = stats.get("battery")

        if SHOW_BATTERY and battery:

            level, charging, age = battery

            if level is not None:

                label = f"bateria  {level:5d} %"

                if charging:
                    label += "  carregando"

                # Leitura velha é pior que ausente (parece atual); passar do
                # dobro do intervalo é sinal de adb travado.
                elif age > BATTERY_POLL_INTERVAL * 2:
                    label += f"  ({age:.0f}s atras)"

                self._hud_text(
                    output,
                    label,
                    y,
                    self.HUD_OK
                    if charging or level > BATTERY_WARNING_LEVEL
                    else self.HUD_BAD,
                )

                y += 40

        # Tempo corrido entre reformas é o único número do HUD que mede
        # progresso (os FPS só dizem que a visão está saudável).

        # Sem "estado", "não detectou" e "não procurou" ficam idênticos no
        # overlay; com filtro por estado, saber qual estado e quantos
        # templates explica um overlay vazio.
        estado = stats.get("state")

        if estado:

            label = f"estado   {estado}"

            procurados = stats.get("searched")

            if procurados is not None:
                label += f"  ({procurados} templates)"

            self._hud_text(
                output,
                label,
                y,
                self.HUD_NEUTRAL,
            )

            y += 40

        # Zero detecções em vermelho — exceto durante settle, onde o worker
        # pula frames de propósito e isso não é um defeito.
        quantas = stats.get("detections")

        aguardando = stats.get("waiting_settle")

        if quantas is not None:

            label = f"deteccoes {quantas:4d}"

            if aguardando:
                label += "  (aguardando a tela parar)"

            self._hud_text(
                output,
                label,
                y,
                self.HUD_NEUTRAL
                if aguardando
                else (
                    self.HUD_OK if quantas else self.HUD_BAD
                ),
            )

            y += 40

        # Thread do detector morrer tem o mesmo sintoma de tudo o mais (bot
        # parado) — aqui ela fala.
        erro = stats.get("vision_error")

        if erro:

            self._hud_text(
                output,
                f"VISAO: {erro[:48]}",
                y,
                self.HUD_BAD,
                scale=0.6,
            )

            y += 40

        cycle = stats.get("cycle")

        if SHOW_CYCLE_TIME and cycle:

            corrido, ultimo, quantos = cycle

            label = f"reforma {quantos:5d}  {formata_duracao(corrido)}"

            if ultimo is not None:
                label += f"  (ult {formata_duracao(ultimo)})"

            # Vermelho só quando há com o que comparar: sem
            # ciclo anterior, "demorado" não quer dizer nada.
            travado = (
                ultimo is not None
                and corrido > ultimo * CYCLE_STALL_FACTOR
            )

            self._hud_text(
                output,
                label,
                y,
                self.HUD_BAD if travado else self.HUD_NEUTRAL,
            )

        return output
