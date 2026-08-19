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

import cv2
import numpy as np

from core import log
from core.config import (
    CATEGORY_ROIS,
    CATEGORY_THRESHOLDS,
    COARSE_MARGIN,
    COARSE_SCALE,
    COLOR_THRESHOLD,
    MAX_DETECTION_AGE,
    MAX_MATCHES_PER_TEMPLATE,
    NMS_IOU,
    REFINE_SLACK,
    SHAPE_THRESHOLD,
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

        self.load_templates()

    # --------------------------------------------------
    # Templates
    # --------------------------------------------------

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

        scale = self.coarse_scale

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

            "width": width,
            "height": height,
        }

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

        scale = self.coarse_scale

        coarse_frame = None

        if scale < 1.0:

            coarse_frame = cv2.resize(
                frame_gray,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )

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

            roi = self._resolve_roi(
                category,
                frame_width,
                frame_height,
            )

            candidates = self._search(
                frame_gray,
                coarse_frame,
                template,
                min_threshold,
                roi,
            )

            for x, y, confidence in candidates:

                # =========================================
                # FILTRO DE COR
                # =========================================

                color_similarity = self._color_similarity(
                    frame_color,
                    template["image"],
                    x,
                    y,
                    template["mask"],
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

                        "width": template["width"],
                        "height": template["height"],
                    }
                )

        # =================================================
        # SUPRESSÃO DE SOBREPOSIÇÃO
        # =================================================
        #
        # Vários templates da mesma categoria acertam o
        # mesmo ícone. Sem isto, uma comida vira 5 detecções.
        #

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

        scale = self.coarse_scale

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
        stats aceita: capture_fps, detect_fps, detect_ms, lag.
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

        return output
