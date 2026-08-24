"""
Detector por template matching.

Mudanças em relação à versão anterior:

1. BUSCA EM DOIS ESTÁGIOS
   Antes: 43 templates x matchTemplate em 1080x2400 = 1.7s
   por passada (medido), ou seja 0.6 FPS — enquanto o loop
   supunha 20 análises/segundo.

   Agora: busca numa cópia reduzida do frame e reconfirma
   cada candidato em resolução cheia. A confiança final
   continua sendo a de resolução cheia, então os thresholds
   já ajustados continuam valendo.

2. MÚLTIPLAS DETECÇÕES POR TEMPLATE
   Antes minMaxLoc devolvia só o melhor ponto: era
   impossível ver duas comidas na mesma tela. Agora usa
   todos os máximos locais + supressão de sobreposição.

3. ROI POR CATEGORIA
   Procurar o "X" de fechar no meio do cenário só gera
   custo e falso positivo.

4. FILTRO POR CATEGORIA
   A StateMachine só precisa de 2 categorias quando está
   dentro da tela de upgrade, não de 43.

5. MÉTRICA DE COR CORRIGIDA
   A antiga fazia média do absdiff em HSV. Dois erros:
   o matiz é circular (vermelho oscila entre 179 e 0 e era
   lido como diferença máxima), e a média deixava o brilho
   dominar — justamente o canal que a animação mexe.

6. MÁSCARA
   Se o PNG do template tiver alpha, os pixels
   transparentes são ignorados na comparação. Nenhum
   template usa isso hoje; é o caminho para os ícones
   redondos, cujo recorte retangular inclui cenário
   que muda.
"""

from collections import Counter
from pathlib import Path

import math
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
    DETECTOR_SCALE_COARSE_MARGIN,
    DETECTOR_SCALE_LOCK_AFTER,
    DETECTOR_SCALE_UNLOCK_AFTER,
    DETECTOR_SCALES,
    MAX_DETECTION_AGE,
    RESAMPLED_THRESHOLD_SLACK,
    MAX_MATCHES_PER_TEMPLATE,
    NMS_IOU,
    REFINE_SLACK,
    SHAPE_THRESHOLD,
    COARSE_SCALE_MIN,
    COARSE_SCALE_STEP,
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


# Caminho relativo ao ARQUIVO, não ao diretório de trabalho.
# Antes era Path("src/vision/templates"), então o projeto só
# rodava a partir da raiz do repositório.
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Abaixo deste tamanho o estágio grosso não compensa e a
# redução destrói o template.
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

        # Melhor match visto por categoria na passada atual,
        # para o diagnóstico de quase-acerto. Só é preenchido
        # com DETECTOR_DEBUG_MISSES ligado.
        self._best = {}
        self._best_color = {}

        self._debug_at = 0.0

        # Último relatório de quase-acerto, em texto, para o
        # painel de status poder mostrá-lo. Com STATUS_PANEL
        # ligado o console está em WARNING e o log INFO abaixo
        # não aparece — sem isto o diagnóstico ficaria invisível
        # exatamente para quem usa o painel.
        self._miss_text = ""
        self._miss_at = 0.0

        # =================================================
        # TRAVA DE ESCALA
        # =================================================
        #
        # A escala da UI do device não muda durante a sessão.
        # Descobri-la a cada quadro é caro (5x a confirmação) e
        # perigoso (o máximo de 5 correlações passa ruído do
        # threshold). Então: descobre, trava, e segue com uma.
        self._scale_locked = None
        self._scale_votes = Counter()
        self._scale_seen_at = 0.0

        self.load_templates()

    # --------------------------------------------------
    # Templates
    # --------------------------------------------------

    def _coarse_scale_for(self, smallest_side):
        """
        Escala do estágio grosso para UM template.

        A menor escala em que o template ainda tem
        MIN_COARSE_SIDE de lado — abaixo disso o estágio grosso
        é abandonado e a busca cai em resolução cheia, que custa
        uma ordem de grandeza mais.

        Arredonda PARA CIMA na grade: escala maior é sempre a
        opção segura, e a grade limita quantos tamanhos
        distintos de frame reduzido precisam existir.
        """

        if smallest_side <= 0:
            return self.coarse_scale

        exata = MIN_COARSE_SIDE / smallest_side

        na_grade = (
            math.ceil(exata / COARSE_SCALE_STEP)
            * COARSE_SCALE_STEP
        )

        # O arredondamento de float deixa 0.15000000000000002,
        # e aí duas escalas iguais viram chaves diferentes no
        # cache de frames reduzidos — um resize a mais por
        # passada, de graça.
        return round(
            min(
                self.coarse_scale,
                max(COARSE_SCALE_MIN, na_grade),
            ),
            4,
        )

    def load_templates(self):

        self.templates.clear()

        if not TEMPLATES_DIR.exists():

            logger.error(
                "Pasta de templates não encontrada: %s",
                TEMPLATES_DIR,
            )

            return

        for category_dir in sorted(TEMPLATES_DIR.iterdir()):

            if not category_dir.is_dir():
                continue

            category = category_dir.name

            for image_path in sorted(
                category_dir.glob("*.png")
            ):

                template = self._load_template(
                    category,
                    image_path,
                )

                if template is not None:

                    self.templates.append(template)

        by_category = {}

        for template in self.templates:

            by_category[template["category"]] = (
                by_category.get(template["category"], 0)
                + 1
            )

        logger.info(
            "Templates: %d em %d categorias %s",
            len(self.templates),
            len(by_category),
            by_category,
        )

    def _load_template(self, category, image_path):

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

        # -------------------------------------------------
        # ALPHA -> MÁSCARA
        # -------------------------------------------------

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

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        height, width = gray.shape[:2]

        # -------------------------------------------------
        # VERSÃO REDUZIDA (estágio grosso)
        # -------------------------------------------------

        # Escala PRÓPRIA deste template, derivada do tamanho
        # dele. Antes era a global, travada pelo menor template
        # de todos.
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
            "name": image_path.name,

            "image": image,
            "gray": gray,
            "mask": mask,

            "coarse_gray": coarse_gray,
            "coarse_mask": coarse_mask,
            "coarse_scale": scale,

            "width": width,
            "height": height,
        }

    # --------------------------------------------------
    # Trava de escala
    # --------------------------------------------------

    def _active_scales(self):
        """
        As escalas a testar AGORA.

        Travada: uma só — o custo e o rigor voltam ao de antes
        do multi-escala.

        Destravada: todas, até juntar votos suficientes.
        """

        if self._scale_locked is not None:
            return (self._scale_locked,)

        return tuple(DETECTOR_SCALES)

    def _vote_scale(self, detections):
        """
        Conta em que escala as detecções saíram e trava quando
        houver consenso.

        Vota só o que passou pelos DOIS filtros (formato e cor):
        um candidato descartado não é evidência de escala.
        """

        if self._scale_locked is not None or not detections:
            return

        for deteccao in detections:
            self._scale_votes[deteccao.get("scale", 1.0)] += 1

        if sum(self._scale_votes.values()) < DETECTOR_SCALE_LOCK_AFTER:
            return

        escala, votos = self._scale_votes.most_common(1)[0]

        self._scale_locked = escala

        logger.info(
            "Escala travada em %.2f (%d de %d acertos). "
            "A busca volta a testar uma escala só.",
            escala,
            votos,
            sum(self._scale_votes.values()),
        )

    def _check_unlock(self, detections):
        """
        Destrava depois de um tempo sem ver nada.

        Uma trava na escala errada — azar nos primeiros acertos,
        ou o device trocado no meio — deixaria o bot cego para
        sempre. O silêncio prolongado é o sintoma, e destravar é
        baratíssimo comparado a não detectar mais nada.
        """

        agora = time.monotonic()

        if detections:

            self._scale_seen_at = agora

            return

        if self._scale_locked is None:
            return

        if not self._scale_seen_at:

            self._scale_seen_at = agora

            return

        if agora - self._scale_seen_at < DETECTOR_SCALE_UNLOCK_AFTER:
            return

        logger.warning(
            "%.0fs sem detecção nenhuma — destravando a escala "
            "%.2f e procurando de novo.",
            agora - self._scale_seen_at,
            self._scale_locked,
        )

        self._scale_locked = None
        self._scale_votes.clear()
        self._scale_seen_at = agora

    # --------------------------------------------------
    # Variantes por escala
    # --------------------------------------------------

    def _variant(self, template, scale):
        """
        O template redimensionado, com gray, cor e máscara
        coerentes entre si.

        Cacheado no próprio template: são poucas escalas e
        redimensionar 184 templates por quadro seria mais caro
        que a busca.

        Devolve None quando a escala deixaria o template menor
        que 4 px — abaixo disso não há forma a comparar.
        """

        if scale == 1.0:
            return template

        cache = template.setdefault("_variants", {})

        if scale in cache:
            return cache[scale]

        largura = int(round(template["width"] * scale))
        altura = int(round(template["height"] * scale))

        if largura < 4 or altura < 4:

            cache[scale] = None

            return None

        tamanho = (largura, altura)

        # INTER_AREA para reduzir, INTER_CUBIC para ampliar: o
        # AREA ampliando devolve bloco, e bloco não casa com o
        # frame, que foi ampliado de outro jeito.
        interpolacao = (
            cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        )

        variante = {
            "category": template["category"],
            "name": template["name"],

            "image": cv2.resize(
                template["image"],
                tamanho,
                interpolation=interpolacao,
            ),

            "gray": cv2.resize(
                template["gray"],
                tamanho,
                interpolation=interpolacao,
            ),

            "mask": (
                None
                if template["mask"] is None
                else cv2.resize(
                    template["mask"],
                    tamanho,
                    interpolation=cv2.INTER_NEAREST,
                )
            ),

            "width": largura,
            "height": altura,

            "scale": scale,
        }

        cache[scale] = variante

        return variante

    # --------------------------------------------------
    # Similaridade de cor
    # --------------------------------------------------

    def _color_similarity(
        self,
        frame,
        template,
        x,
        y,
        mask=None,
    ):
        """
        Compara cor em HSV levando em conta que:

        - o matiz é CIRCULAR, então a distância entre 179
          e 0 é 1, não 179;
        - o matiz de um pixel dessaturado é ruído, então
          ele pesa proporcionalmente à saturação;
        - o brilho (V) muda com animação e iluminação,
          então pesa pouco.
        """

        template_height, template_width = template.shape[:2]

        roi = frame[
            y:y + template_height,
            x:x + template_width
        ]

        if roi.shape != template.shape:
            return 0.0

        roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        template_hsv = cv2.cvtColor(
            template,
            cv2.COLOR_BGR2HSV,
        )

        roi_hsv = roi_hsv.astype(np.int16)
        template_hsv = template_hsv.astype(np.int16)

        # -------------------------------------------------
        # MATIZ (circular)
        # -------------------------------------------------
        #
        # OpenCV usa H em 0..179, então a volta completa
        # é 180 e a distância máxima real é 90.
        #

        hue_difference = np.abs(
            roi_hsv[:, :, 0] - template_hsv[:, :, 0]
        )

        hue_difference = np.minimum(
            hue_difference,
            180 - hue_difference,
        )

        hue_difference = hue_difference / 90.0

        # -------------------------------------------------
        # SATURAÇÃO E BRILHO
        # -------------------------------------------------

        saturation_difference = np.abs(
            roi_hsv[:, :, 1] - template_hsv[:, :, 1]
        ) / 255.0

        value_difference = np.abs(
            roi_hsv[:, :, 2] - template_hsv[:, :, 2]
        ) / 255.0

        # -------------------------------------------------
        # PESO DO MATIZ POR SATURAÇÃO
        # -------------------------------------------------
        #
        # Num pixel cinza o matiz não significa nada.
        #

        saturation_weight = np.minimum(
            roi_hsv[:, :, 1],
            template_hsv[:, :, 1],
        ) / 255.0

        difference = (
            0.60 * hue_difference * saturation_weight
            + 0.30 * saturation_difference
            + 0.10 * value_difference
        )

        # -------------------------------------------------
        # MÁSCARA
        # -------------------------------------------------

        if mask is not None:

            valid = mask > 0

            if not valid.any():
                return 0.0

            mean_difference = float(
                difference[valid].mean()
            )

        else:

            mean_difference = float(difference.mean())

        return max(0.0, 1.0 - mean_difference)

    # --------------------------------------------------
    # ROI
    # --------------------------------------------------

    def _resolve_roi(
        self,
        category,
        frame_width,
        frame_height,
    ):
        """
        Converte a ROI fracionária da categoria em pixels.
        Devolve (x1, y1, x2, y2) em resolução cheia.
        """

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

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------

    def detect(self, frame, categories=None, resampled=False):
        """
        Devolve as detecções aprovadas, ordenadas por
        confiança (maior primeiro).

        categories: se informado, só procura essas
        categorias. É o que permite a StateMachine buscar
        2 templates em vez de 43.

        resampled: este frame passou por resize antes de chegar
        aqui? Se sim, os thresholds ganham
        RESAMPLED_THRESHOLD_SLACK de folga — eles foram
        calibrados em frame nativo, onde o match é quase pixel a
        pixel. Quem sabe a resposta é o VisionWorker, que fez o
        resize; o detector não tem como adivinhar.
        """

        if frame is None:
            return []

        slack = (
            RESAMPLED_THRESHOLD_SLACK if resampled else 0.0
        )

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

        # =================================================
        # CÓPIA REDUZIDA (uma vez por frame)
        # =================================================

        # Cada template tem a escala dele, então pode haver mais
        # de um frame reduzido. São construídos SOB DEMANDA: no
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

        wanted = (
            set(categories)
            if categories is not None
            else None
        )

        detections = []

        for template in self.templates:

            category = template["category"]

            if wanted is not None and category not in wanted:
                continue

            if (
                template["width"] > frame_width
                or template["height"] > frame_height
            ):
                continue

            min_threshold = self.category_thresholds.get(
                category,
                self.threshold,
            )

            # A folga é do frame, não da categoria: um frame
            # reamostrado baixa o teto de TODAS elas.
            if slack:

                min_threshold = max(0.0, min_threshold - slack)

            roi = self._resolve_roi(
                category,
                frame_width,
                frame_height,
            )

            # Qual categoria está sendo procurada agora, para
            # o _match saber onde anotar o melhor match.
            self._debug_category = category

            candidates = self._search(
                frame_gray,
                coarse_for(template["coarse_scale"]),
                template,
                min_threshold,
                roi,
            )

            for x, y, confidence, variant in candidates:

                # =========================================
                # FILTRO DE COR
                # =========================================
                #
                # Na variante, não no template original: o
                # recorte do frame tem o tamanho da variante, e
                # comparar com outra dimensão devolve 0.0 e
                # descarta um acerto bom.

                color_similarity = self._color_similarity(
                    frame_color,
                    variant["image"],
                    x,
                    y,
                    variant["mask"],
                )

                if DETECTOR_DEBUG_MISSES:

                    self._best_color[category] = max(
                        self._best_color.get(category, 0.0),
                        color_similarity,
                    )

                if color_similarity < self.color_threshold:
                    continue

                detections.append(
                    {
                        "category": category,
                        "name": template["name"],

                        "confidence": float(confidence),
                        "color_similarity": float(
                            color_similarity
                        ),
                        "min_threshold": float(
                            min_threshold
                        ),

                        "x": int(x),
                        "y": int(y),

                        # Da variante: é o tamanho REAL da caixa
                        # casada, e é dele que sai o centro onde
                        # o toque cai.
                        "width": variant["width"],
                        "height": variant["height"],

                        "scale": variant.get("scale", 1.0),
                    }
                )

        # =================================================
        # SUPRESSÃO DE SOBREPOSIÇÃO
        # =================================================
        #
        # Vários templates da mesma categoria acertam o
        # mesmo ícone. Sem isto, uma comida vira 5 detecções.
        #

        if DETECTOR_DEBUG_MISSES:

            self._report_misses(wanted, detections, slack)

        detections = self._suppress(detections)

        # Depois da supressão: uma comida que casou em 5
        # templates é UM acerto, e votaria 5 vezes se contada
        # antes.
        self._vote_scale(detections)

        self._check_unlock(detections)

        # Maior confiança primeiro: a StateMachine passa a
        # agir sobre o MELHOR match, não sobre o primeiro
        # em ordem alfabética de arquivo.
        detections.sort(
            key=lambda item: item["confidence"],
            reverse=True,
        )

        return detections

    # --------------------------------------------------
    # Busca em dois estágios
    # --------------------------------------------------

    def _search(
        self,
        frame_gray,
        coarse_frame,
        template,
        min_threshold,
        roi,
    ):
        """
        Devolve [(x, y, confiança, variante)] em resolução
        cheia.

        A variante diz em QUE escala o match saiu — é dela que
        vêm a largura e a altura da caixa, e portanto o ponto do
        clique.
        """

        coarse_template = template["coarse_gray"]

        escalas = [
            escala
            for escala in self._active_scales()
            if self._variant(template, escala) is not None
        ] or [1.0]

        # -------------------------------------------------
        # Sem estágio grosso: busca direta
        # -------------------------------------------------

        if coarse_frame is None or coarse_template is None:

            resultados = []

            for escala in escalas:

                variante = self._variant(template, escala)

                resultados.extend(
                    (x, y, confianca, variante)
                    for x, y, confianca in self._match(
                        frame_gray,
                        variante["gray"],
                        variante["mask"],
                        min_threshold,
                        roi,
                        MAX_MATCHES_PER_TEMPLATE,
                    )
                )

            return resultados

        # -------------------------------------------------
        # ESTÁGIO 1 — grosso
        # -------------------------------------------------
        #
        # Threshold mais permissivo: a redução degrada a
        # confiança, e descartar aqui é irreversível.
        #

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

        # Com multi-escala, o estágio grosso precisa de folga
        # EXTRA: ele usa o template em escala 1, e o que ele
        # descartar o estágio fino nunca vê. Sem isto as
        # escalas extras não serviriam para nada.
        margem = self.coarse_margin

        if len(escalas) > 1:
            margem += DETECTOR_SCALE_COARSE_MARGIN

        coarse_hits = self._match(
            coarse_frame,
            coarse_template,
            template["coarse_mask"],
            max(0.0, min_threshold - margem),
            coarse_roi,
            MAX_MATCHES_PER_TEMPLATE,
        )

        if not coarse_hits:
            return []

        # -------------------------------------------------
        # ESTÁGIO 2 — fino
        # -------------------------------------------------
        #
        # Reconfirma cada candidato em resolução cheia,
        # numa janelinha. A confiança devolvida é a de
        # resolução cheia, comparável à da versão antiga.
        #

        results = []

        template_height = template["height"]
        template_width = template["width"]

        frame_height, frame_width = frame_gray.shape[:2]

        # A maior escala manda no tamanho da janela: uma janela
        # do tamanho da escala 1 não cabe o template a 1.06, e
        # o _match devolveria vazio para as escalas grandes.
        maior = max(escalas)

        for coarse_x, coarse_y, _ in coarse_hits:

            estimated_x = int(coarse_x / scale)
            estimated_y = int(coarse_y / scale)

            window = (
                max(0, estimated_x - REFINE_SLACK),
                max(0, estimated_y - REFINE_SLACK),
                min(
                    frame_width,
                    estimated_x
                    + int(round(template_width * maior))
                    + REFINE_SLACK,
                ),
                min(
                    frame_height,
                    estimated_y
                    + int(round(template_height * maior))
                    + REFINE_SLACK,
                ),
            )

            # -----------------------------------------
            # A MELHOR ESCALA, não a primeira
            # -----------------------------------------
            #
            # Escalas vizinhas casam no mesmo objeto com
            # confianças parecidas. Aceitar a primeira acima do
            # corte devolveria uma caixa de tamanho arbitrário
            # entre as candidatas — e a caixa define o centro,
            # ou seja, onde o dedo cai. Fica a de maior
            # confiança.
            melhor = None

            for escala in escalas:

                variante = self._variant(template, escala)

                refined = self._match(
                    frame_gray,
                    variante["gray"],
                    variante["mask"],
                    min_threshold,
                    window,

                    # Na janela fina só interessa o melhor ponto.
                    1,
                )

                if not refined:
                    continue

                x, y, confianca = refined[0]

                if melhor is None or confianca > melhor[2]:
                    melhor = (x, y, confianca, variante)

            if melhor is not None:
                results.append(melhor)

        return results

    def _report_misses(self, wanted, detections, slack):
        """
        Diz, por categoria procurada e não encontrada, de quanto
        foi o melhor match.

        É a diferença entre dois diagnósticos opostos:

            upgrade: melhor formato 0.93 (corta em 0.98)
                -> o objeto ESTÁ na tela e o threshold cortou.
                   Baixe o número, ou suba a folga de
                   reamostragem.

            upgrade: melhor formato 0.41 (corta em 0.98)
                -> o template não parece com o que está na tela.
                   Threshold nenhum resolve; precisa de template
                   dessa tela.
        """

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

            # Nada faltando agora: apaga o relatório anterior em
            # vez de deixá-lo na tela. Texto velho de problema
            # resolvido é pior que texto nenhum.
            self._miss_text = ""
            self._miss_at = agora

            return

        partes = []

        for categoria in faltando:

            corte = self.category_thresholds.get(
                categoria,
                self.threshold,
            )

            if slack:
                corte = max(0.0, corte - slack)

            forma = self._best.get(categoria)
            cor = self._best_color.get(categoria)

            texto = f"{categoria} F:"

            texto += (
                "-" if forma is None else f"{forma:.3f}"
            )

            texto += f"/{corte:.2f}"

            # Cor só aparece quando o formato passou: senão a
            # comparação de cor nem foi feita, e um "C:-" sem
            # explicação parece falha.
            if cor is not None:
                texto += f" C:{cor:.3f}/{self.color_threshold:.2f}"

            partes.append(texto)

        self._miss_text = "  ".join(partes)
        self._miss_at = agora

        # Continua no log: com STATUS_PANEL desligado é aqui que
        # a informação aparece, e é o que fica gravado para ler
        # depois.
        logger.info(
            "não detectado (melhor match / corte): %s",
            self._miss_text,
        )

    def miss_report(self):
        """
        O último relatório de quase-acerto, ou "" se não houver.

        Vazio também quando o relatório envelheceu: um texto de
        três minutos atrás descreve outra tela, e no painel ele
        pareceria atual.
        """

        if not DETECTOR_DEBUG_MISSES:
            return ""

        if not self._miss_text:
            return ""

        if (
            time.monotonic() - self._miss_at
            > DETECTOR_DEBUG_INTERVAL * 3
        ):
            return ""

        return self._miss_text

    def _match(
        self,
        image,
        template_image,
        mask,
        min_threshold,
        roi,
        max_matches,
    ):
        """
        matchTemplate dentro de uma região, devolvendo os
        máximos locais acima do threshold em coordenada
        absoluta da imagem.
        """

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

        # Máscara só é suportada em TM_CCORR_NORMED /
        # TM_SQDIFF. Sem máscara mantemos TM_CCOEFF_NORMED
        # para não mudar a escala das confianças já
        # ajustadas nos thresholds.
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

        # O máximo bruto, antes de qualquer corte. É o número
        # que o diagnóstico precisa: o threshold esconde
        # exatamente a informação de quanto faltou.
        if DETECTOR_DEBUG_MISSES and result.size:

            categoria = getattr(self, "_debug_category", None)

            if categoria is not None:

                self._best[categoria] = max(
                    self._best.get(categoria, 0.0),
                    float(result.max()),
                )

        # -------------------------------------------------
        # Apenas o melhor ponto
        # -------------------------------------------------

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

        # -------------------------------------------------
        # Todos os pontos acima do threshold
        # -------------------------------------------------

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

    # --------------------------------------------------
    # Supressão de sobreposição
    # --------------------------------------------------

    def _suppress(self, detections):
        """
        Mantém, por categoria, a melhor detecção de cada
        grupo de caixas sobrepostas.
        """

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

    # --------------------------------------------------
    # Draw
    # --------------------------------------------------

    # Cor por categoria: dá para ver o que é o que sem ler
    # o texto. Antes tudo era preto.
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
        "open_store": (255, 180, 0),
        "renovate_coin": (0, 255, 255),
    }

    DEFAULT_COLOR = (255, 255, 255)

    # Cores do HUD
    HUD_OK = (0, 255, 0)
    HUD_BAD = (0, 0, 255)
    HUD_NEUTRAL = (255, 255, 255)

    def _hud_text(self, output, text, y, color, scale=0.8):
        """
        Texto com contorno escuro, legível sobre qualquer
        fundo do jogo.
        """

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

    def draw(self, frame, detections, stats=None):
        """
        stats aceita: capture_fps, detect_fps, detect_ms, lag,
        battery (nível, carregando, idade_da_leitura),
        cycle (corrido, ultimo, quantos).
        Chaves ausentes simplesmente não aparecem.
        """

        output = frame

        for detection in detections:

            x = detection["x"]
            y = detection["y"]

            width = detection["width"]
            height = detection["height"]

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

        # -------------------------------------------------
        # HUD
        # -------------------------------------------------

        if not stats:
            return output

        y = 50

        # -------------------------------------------------
        # FPS
        # -------------------------------------------------
        #
        # Dois números distintos de propósito: a captura pode
        # ir a 60 enquanto o detector faz 3 passadas por
        # segundo. Quem manda na reação do bot é o detector.
        #

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

                # Abaixo de 1/MAX_DETECTION_AGE as detecções
                # nascem velhas e a StateMachine para de
                # clicar. Não é limite inventado: é o mesmo
                # da checagem de idade.
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

        # -------------------------------------------------
        # ATRASO
        # -------------------------------------------------
        #
        # Sem isso, detecção atrasada parece detecção errada.
        #

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

        # -------------------------------------------------
        # BATERIA
        # -------------------------------------------------
        #
        # Sessão que morre por bateria descarregada não deixa
        # rastro no log — o bot só para de agir.
        #

        battery = stats.get("battery")

        if SHOW_BATTERY and battery:

            level, charging, age = battery

            if level is not None:

                label = f"bateria  {level:5d} %"

                if charging:
                    label += "  carregando"

                # Leitura velha é pior que leitura ausente: um
                # número parado parece atual. O intervalo é
                # conhecido, então passar do dobro dele é
                # sinal de adb travado.
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

        # -------------------------------------------------
        # TEMPO CORRIDO ENTRE REFORMAS
        # -------------------------------------------------
        #
        # O único número do HUD que mede PROGRESSO. Os FPS
        # dizem que a visão está saudável; este diz se o bot
        # está andando. Ele pode estar a 30 fps clicando em
        # nada há vinte minutos.
        #

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
