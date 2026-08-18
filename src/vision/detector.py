import cv2
from pathlib import Path


TEMPLATES_DIR = Path(
    "src/vision/templates"
)


class Detector:

    def __init__(
        self,
        threshold=0.80,
        color_threshold=0.85,
        category_thresholds=None
    ):

        # --------------------------------------------------
        # THRESHOLDS
        # --------------------------------------------------

        # Similaridade do formato/padrão
        self.threshold = threshold

        # Similaridade das cores
        self.color_threshold = color_threshold

        self.templates = []

        self.load_templates()

        # Threshold específico por categoria.
        # Se uma categoria não estiver aqui,
        # usa self.threshold como padrão.

        self.category_thresholds = (
            category_thresholds or {}
        )

    # --------------------------------------------------
    # Templates
    # --------------------------------------------------

    def load_templates(self):

        self.templates.clear()

        if not TEMPLATES_DIR.exists():

            print(
                "Pasta de templates não encontrada:"
            )

            print(
                TEMPLATES_DIR
            )

            return

        for category_dir in sorted(
            TEMPLATES_DIR.iterdir()
        ):

            if not category_dir.is_dir():
                continue

            category = category_dir.name

            for image_path in sorted(
                category_dir.glob("*.png")
            ):

                image = cv2.imread(
                    str(image_path),
                    cv2.IMREAD_COLOR
                )

                if image is None:

                    print(
                        f"Erro ao carregar: "
                        f"{image_path}"
                    )

                    continue

                gray = cv2.cvtColor(
                    image,
                    cv2.COLOR_BGR2GRAY
                )

                self.templates.append(
                    {
                        "category": category,
                        "name": image_path.name,
                        "image": image,
                        "gray": gray,
                        "width": image.shape[1],
                        "height": image.shape[0],
                    }
                )

        print(
            f"Templates carregados: "
            f"{len(self.templates)}"
        )

        for template in self.templates:

            print(
                f"  [{template['category']}] "
                f"{template['name']} "
                f"{template['width']}x"
                f"{template['height']}"
            )

    # --------------------------------------------------
    # COLOR SIMILARITY
    # --------------------------------------------------

    def _color_similarity(
        self,
        frame,
        template,
        x,
        y
    ):

        template_height, template_width = (
            template.shape[:2]
        )

        # Região da imagem original correspondente
        # exatamente ao tamanho do template.

        roi = frame[
            y:y + template_height,
            x:x + template_width
        ]

        # Segurança caso a região ultrapasse o frame.

        if roi.shape != template.shape:

            return 0.0

        # --------------------------------------------------
        # BGR → HSV
        # --------------------------------------------------
        #
        # HSV é mais adequado para comparar cores.
        #
        # H = matiz
        # S = saturação
        # V = brilho
        #

        roi_hsv = cv2.cvtColor(
            roi,
            cv2.COLOR_BGR2HSV
        )

        template_hsv = cv2.cvtColor(
            template,
            cv2.COLOR_BGR2HSV
        )

        # --------------------------------------------------
        # Diferença de cor
        # --------------------------------------------------

        difference = cv2.absdiff(
            roi_hsv,
            template_hsv
        )

        mean_difference = (
            difference.mean()
        )

        # --------------------------------------------------
        # Normalização
        # --------------------------------------------------

        similarity = max(
            0.0,
            1.0 - (
                mean_difference / 255.0
            )
        )

        return similarity

    # --------------------------------------------------
    # Detect
    # --------------------------------------------------

    def detect(self, frame):
        
        detections = []

        if frame is None:
            return detections

        if len(frame.shape) == 3:
            frame_gray = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY
            )
        else:
            frame_gray = frame

        frame_height, frame_width = (
            frame.shape[:2]
        )

        # =================================================
        # ANALISA CADA TEMPLATE
        # =================================================

        for template in self.templates:

            image = template["image"]
            template_gray = template["gray"]

            template_height = (
                template["height"]
            )

            template_width = (
                template["width"]
            )

            category = template["category"]

            # =================================================
            # TEMPLATE MAIOR QUE O FRAME
            # =================================================

            if (
                template_width > frame_width
                or template_height > frame_height
            ):
                continue

            # =================================================
            # MATCH TEMPLATE
            # =================================================

            result = cv2.matchTemplate(
                frame_gray,
                template_gray,
                cv2.TM_CCOEFF_NORMED
            )

            _, confidence, _, location = (
                cv2.minMaxLoc(result)
            )

            # =================================================
            # THRESHOLD DA CATEGORIA
            # =================================================
            #
            # Se existir um threshold específico para
            # essa categoria, usamos ele.
            #
            # Caso contrário, usamos self.threshold.
            #
            # Exemplo:
            #
            # food       → 0.90
            # upgrade    → 0.95
            # plane      → 0.80 (padrão)
            #

            min_threshold = (
                self.category_thresholds.get(
                    category,
                    self.threshold
                )
            )

            # =================================================
            # FILTRO DE FORMATO
            # =================================================

            if confidence < min_threshold:
                continue

            x, y = location

            # =================================================
            # FILTRO DE COR
            # =================================================

            color_similarity = (
                self._color_similarity(
                    frame,
                    image,
                    x,
                    y
                )
            )

            if (
                color_similarity
                < self.color_threshold
            ):
                continue

            # =================================================
            # DETECÇÃO APROVADA
            # =================================================

            detections.append(
                {
                    "category": category,

                    "name": template[
                        "name"
                    ],

                    # Confiança do formato
                    "confidence": float(
                        confidence
                    ),

                    # Confiança das cores
                    "color_similarity": float(
                        color_similarity
                    ),

                    # Threshold que foi usado
                    "min_threshold": float(
                        min_threshold
                    ),

                    "x": x,
                    "y": y,

                    "width": template_width,
                    "height": template_height,
                }
            )

        return detections

    # --------------------------------------------------
    # Draw
    # --------------------------------------------------

    def draw(
        self,
        frame,
        detections
    ):

        output = frame.copy()

        for detection in detections:

            x = detection["x"]
            y = detection["y"]

            width = detection["width"]
            height = detection["height"]

            category = detection[
                "category"
            ]

            confidence = detection[
                "confidence"
            ]

            color_similarity = detection.get(
                "color_similarity",
                0.0
            )

            # --------------------------------------------------
            # Caixa
            # --------------------------------------------------

            cv2.rectangle(
                output,
                (x, y),
                (
                    x + width,
                    y + height
                ),
                (0, 0, 0),
                4
            )

            # --------------------------------------------------
            # Texto
            # --------------------------------------------------

            text = (
                f"{category} "
                f"F:{confidence:.2f} "
                f"C:{color_similarity:.2f}"
            )

            cv2.putText(
                output,
                text,
                (
                    x,
                    max(
                        y - 10,
                        20
                    )
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.3,
                (0, 0, 0),
                13
            )

        return output