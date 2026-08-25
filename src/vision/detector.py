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
    MAX_DETECTION_AGE,
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

        # Índice por categoria: com o filtro de estado ligado
        # a passada olha 2 categorias, não 43. Antes o loop
        # percorria os 106 templates para descartar 100.
        self.by_category = {}

        # -------------------------------------------------
        # TEMPLATES NA ESCALA DO FRAME
        # -------------------------------------------------
        #
        # Os templates foram recortados em REFERENCE_WIDTH x
        # REFERENCE_HEIGHT. Quando o frame do device vem em
        # outra resolução, alguém tem de mudar de tamanho — e
        # a escolha importa.
        #
        # Antes o VisionWorker fazia letterbox do FRAME para o
        # espaço de referência: 7.8 MB de canvas por passada,
        # mais um resize do frame inteiro, mais a inversão das
        # coordenadas depois. E o letterbox de uma tela deitada
        # dentro de um canvas em pé encolhia a imagem a ~0.45,
        # o que sozinho derrubava a confiança abaixo de
        # qualquer threshold — o bot não detectava nada.
        #
        # Agora reescalamos os TEMPLATES, uma vez por resolução
        # de frame, e a busca acontece no frame nativo. Sai o
        # resize por passada, sai a conversão de coordenada (a
        # detecção já nasce no espaço do frame real, que é onde
        # o clique precisa dela) e o frame conserva o detalhe.
        #
        # No caso comum (frame já na referência) nada disso
        # roda: a lista original é usada como está.
        self._scaled_cache = {}

        # ROI em pixels por (categoria, largura, altura).
        self._roi_cache = {}

        # Templates olhados na última passada.
        self.last_searched = 0

        # -------------------------------------------------
        # DIAGNÓSTICO DE QUASE-ACERTO
        # -------------------------------------------------
        #
        # Nada disto roda com DETECTOR_DEBUG_MISSES
        # desligado, que é o padrão: o custo é uma
        # comparação de bool por match.
        #
        # Para que serve: sem ele, "não detectou" e
        # "detectou e o threshold cortou" têm o mesmo
        # sintoma — bot parado — e pedem correções OPOSTAS.
        # Uma quer template novo, a outra quer baixar o
        # número.
        self._best = {}
        self._best_color = {}

        self._debug_category = None
        self._debug_at = 0.0

        # O relatório em texto, para o painel de status:
        # com o painel ligado o console fica em WARNING e o
        # log INFO não aparece.
        self._miss_text = ""
        self._miss_at = 0.0

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
        self.by_category.clear()
        self._scaled_cache.clear()

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

        for template in self.templates:

            self.by_category.setdefault(
                template["category"],
                [],
            ).append(template)

        logger.info(
            "Templates: %d em %d categorias %s",
            len(self.templates),
            len(self.by_category),
            {
                categoria: len(lista)
                for categoria, lista in sorted(
                    self.by_category.items()
                )
            },
        )

        # Sem template não existe detecção, e o sintoma é o
        # mesmo de detector quebrado: bot parado, HUD vazio.
        # Melhor uma linha de ERRO no start do que procurar
        # isso depois.
        if not self.templates:

            logger.error(
                "NENHUM template carregado de %s — o detector "
                "não tem com o que comparar, e o bot não vai "
                "agir.",
                TEMPLATES_DIR,
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

        return self._build_template(
            category,
            image_path.name,
            image,
            mask,
        )

    # --------------------------------------------------
    # Construção / reescala
    # --------------------------------------------------

    def _build_template(self, category, name, image, mask):
        """
        Deriva de UMA imagem BGR (+ máscara opcional) tudo o
        que a busca consome: cinza, HSV, versão reduzida do
        estágio grosso e a máscara nas duas escalas.

        Separado do carregamento de propósito: é o mesmo
        caminho para o PNG do disco e para a versão reescalada
        na resolução do frame, então as duas não podem
        divergir.
        """

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # HSV do template, UMA vez.
        #
        # Antes _color_similarity convertia o template a cada
        # candidato: com MAX_MATCHES_PER_TEMPLATE em 12 e 106
        # templates, isso é até 1272 conversões por passada de
        # uma imagem que nunca muda.
        template_hsv = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2HSV,
        ).astype(np.int16)

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
            "name": name,

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
        """
        A referência NA ORIENTAÇÃO do frame.

        Este jogo roda deitado, então o frame chega 2400x1080
        enquanto a referência está escrita 1080x2400. Sem a
        troca, qualquer razão sai absurda (0.45) e nenhum
        template passa do threshold — o sintoma de "não detecta
        nada".
        """

        ref_width = REFERENCE_WIDTH
        ref_height = REFERENCE_HEIGHT

        if (frame_width > frame_height) != (
            ref_width > ref_height
        ):
            ref_width, ref_height = ref_height, ref_width

        return ref_width, ref_height

    @classmethod
    def _frame_scale(cls, frame_width, frame_height):
        """
        Quanto o template precisa mudar de tamanho para valer
        neste frame.

        O critério vem de TEMPLATE_SCALE_BASIS, porque não
        existe um que sirva sempre: o que muda entre aparelhos
        não é só resolução, é PROPORÇÃO. Escalar pelo menor dos
        dois fatores (o antigo "min") só está certo quando a
        proporção é a mesma da referência — num 1080x1920 contra
        1080x2400 ele encolhia tudo 20% com a largura idêntica,
        e aí não detectava nada.
        """

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
        """
        O frame está exatamente na resolução de referência (em
        qualquer das duas orientações)?

        É o caso em que não há nada a reescalar e nem escala
        extra a procurar — custo zero.
        """

        return (frame_width, frame_height) in (
            (REFERENCE_WIDTH, REFERENCE_HEIGHT),
            (REFERENCE_HEIGHT, REFERENCE_WIDTH),
        )

    def _rescale_template(self, template, scale):
        """
        O mesmo template na escala do frame, ou None se
        encolher tanto que não sobra imagem.
        """

        # Escala 1.0: devolve o original. Passar pelo resize
        # daria a MESMA dimensão de volta, gastando tempo e
        # perdendo nitidez em cima de uma imagem que já estava
        # certa.
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
        )

    def _templates_for(self, frame_width, frame_height):
        """
        Os templates deste frame, indexados por categoria.

        Cacheado por resolução: o resize acontece uma vez por
        tamanho de frame visto, não uma vez por passada.
        """

        # Já na referência: nada a reescalar, e nenhuma escala
        # extra a procurar. É o caminho de custo zero.
        if self._is_reference(frame_width, frame_height):
            return self.by_category

        chave = (frame_width, frame_height)

        cacheados = self._scaled_cache.get(chave)

        if cacheados is not None:
            return cacheados

        base = self._frame_scale(frame_width, frame_height)

        # =================================================
        # VÁRIAS ESCALAS, NÃO UMA APOSTA
        # =================================================
        #
        # Nenhuma regra acerta todo aparelho: densidade de tela,
        # barra de status e a escolha de layout do jogo mudam o
        # tamanho do ícone alguns por cento — e template
        # matching não tolera isso (8% de erro de escala já
        # derruba a confiança abaixo de 0.95).
        #
        # Então o template entra em vários tamanhos e o melhor
        # ganha: a supressão por sobreposição colapsa os acertos
        # repetidos das escalas vizinhas, e a ordenação por
        # confiança escolhe. Não é preciso adivinhar a escala.
        escalas = sorted(
            {
                round(base * passo, 4)
                for passo in TEMPLATE_SCALE_STEPS
                if passo > 0
            }
        ) or [round(base, 4)]

        cacheados = {}

        for template in self.templates:

            for escala in escalas:

                variante = self._rescale_template(
                    template,
                    escala,
                )

                if variante is None:
                    continue

                cacheados.setdefault(
                    variante["category"],
                    [],
                ).append(variante)

        self._scaled_cache[chave] = cacheados

        logger.info(
            "Frame %dx%d fora da referência %dx%d — %d "
            "templates em %d escala(s) %s (base %.3f por '%s').",
            frame_width,
            frame_height,
            REFERENCE_WIDTH,
            REFERENCE_HEIGHT,
            sum(len(lista) for lista in cacheados.values()),
            len(escalas),
            escalas,
            base,
            TEMPLATE_SCALE_BASIS,
        )

        return cacheados

    # --------------------------------------------------
    # Similaridade de cor
    # --------------------------------------------------

    def _color_similarity(
        self,
        frame,
        template,
        x,
        y,
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

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------

    def detect(self, frame, categories=None):
        """
        Devolve as detecções aprovadas, ordenadas por
        confiança (maior primeiro).

        categories: se informado, só procura essas
        categorias. É o que permite a StateMachine buscar
        2 templates em vez de 43.
        """

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

        # Templates deste frame, por categoria (o índice
        # original quando o frame já está na referência).
        por_categoria = self._templates_for(
            frame_width,
            frame_height,
        )

        # =================================================
        # A ORDEM DAS CATEGORIAS É A PRIORIDADE
        # =================================================
        #
        # `categories` chega na ORDEM DAS REGRAS do estado, e a
        # StateMachine age na PRIMEIRA regra que casar. Então
        # tudo que vem depois de um acerto é trabalho que nunca
        # vira ação.
        #
        # Não é economia pequena: `food` são 124 dos 185
        # templates e 174 dos 312 ms de uma passada — e é a
        # ÚLTIMA prioridade em NORMAL. Medido nesta tela, um
        # frame em que uma regra de cima casa cai de 264 ms
        # para ~20 ms.
        #
        # A parada é por CATEGORIA, nunca dentro dela: duas
        # comidas na mesma tela são duas detecções da mesma
        # categoria, e cortar no primeiro template faria o bot
        # ver uma só.
        #
        # Sem filtro (categories None) não há prioridade que
        # respeitar e nada é interrompido: é o caminho do
        # overlay de diagnóstico e do dataset, que querem a
        # tela inteira.
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

                    # =====================================
                    # FILTRO DE COR
                    # =====================================

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

            if achou and parar_na_prioridade:
                break

        # =================================================
        # SUPRESSÃO DE SOBREPOSIÇÃO
        # =================================================
        #
        # Vários templates da mesma categoria acertam o
        # mesmo ícone. Sem isto, uma comida vira 5 detecções.
        #

        if DETECTOR_DEBUG_MISSES:

            # As categorias que a passada REALMENTE olhou: com
            # a parada por prioridade, as de baixo podem nem
            # ter sido procuradas, e listá-las como "não
            # detectado" mandaria caçar template que está bom.
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
        Devolve [(x, y, confiança)] em resolução cheia.
        """

        coarse_template = template["coarse_gray"]

        # -------------------------------------------------
        # Sem estágio grosso: busca direta
        # -------------------------------------------------

        if coarse_frame is None or coarse_template is None:

            return self._match(
                frame_gray,
                template["gray"],
                template["mask"],
                min_threshold,
                roi,
                MAX_MATCHES_PER_TEMPLATE,
            )

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

        # O máximo BRUTO, antes de qualquer corte: é a
        # única informação que o threshold esconde, e é
        # exatamente a que o diagnóstico precisa.
        if DETECTOR_DEBUG_MISSES and result.size:

            if self._debug_category is not None:

                self._best[self._debug_category] = max(
                    self._best.get(self._debug_category, 0.0),
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
    # Diagnóstico de quase-acerto
    # --------------------------------------------------

    def _report_misses(self, wanted, detections):
        """
        Por categoria procurada e NÃO encontrada, de quanto
        foi o melhor match.

            upgrade F:0.931/0.98
                o objeto está na tela e o threshold cortou.

            upgrade F:0.412/0.98
                o template não parece com o que está na
                tela. Threshold nenhum resolve.
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
        """
        O último relatório, ou "" quando não há — inclusive
        quando envelheceu: um texto de minutos atrás
        descreve outra tela e no painel pareceria atual.
        """

        if not DETECTOR_DEBUG_MISSES or not self._miss_text:
            return ""

        if (
            time.monotonic() - self._miss_at
            > DETECTOR_DEBUG_INTERVAL * 3
        ):
            return ""

        return self._miss_text

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

    def draw(self, frame, detections, stats=None, scale=1.0):
        """
        stats aceita: capture_fps, detect_fps, detect_ms, lag,
        battery (nível, carregando, idade_da_leitura),
        cycle (corrido, ultimo, quantos).
        Chaves ausentes simplesmente não aparecem.

        `scale` é o fator já aplicado ao frame que chega
        aqui. As detecções vêm em coordenada de frame
        CHEIO, então num frame reduzido elas precisam ser
        convertidas — senão as caixas aparecem fora de
        lugar e o overlay passa a mentir sobre onde o bot
        está vendo as coisas.

        Desenhar reduzido é o que barateia: retângulo e
        putText (com contorno, duas passadas) custam por
        pixel tocado.
        """

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

        # -------------------------------------------------
        # ESTADO E O QUE ESTÁ SENDO PROCURADO
        # -------------------------------------------------
        #
        # Sem estas linhas, "não detectou" e "não procurou"
        # ficam idênticos na tela. Com o filtro por estado
        # ligado o detector olha só as categorias do estado
        # atual, então saber QUAL estado e QUANTOS templates é
        # o que explica um overlay vazio.

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

        # Quantas detecções o frame analisado produziu. Zero em
        # vermelho: é a informação que faltava.
        #
        # Menos enquanto a tela estabiliza: ali o worker está
        # PULANDO frames de propósito (mostram a tela de antes
        # do efeito da ação), e pintar isso de vermelho acusaria
        # de defeito o que é o comportamento certo.
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

        # -------------------------------------------------
        # VISÃO CAÍDA
        # -------------------------------------------------
        #
        # A thread do detector morrer tem o mesmo sintoma de
        # tudo o mais: bot parado. Aqui ela fala.

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
