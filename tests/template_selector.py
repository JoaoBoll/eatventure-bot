import cv2
from pathlib import Path

from android_screenshot import AndroidScreenshot


WINDOW_NAME = "Template Selector"

TEMPLATES_DIR = Path(
    "src/vision/templates"
)


class TemplateSelector:

    def __init__(self):

        self.screenshot = AndroidScreenshot()

        # Imagem ORIGINAL
        self.image = None

        # Imagem usada apenas para visualização
        self.display = None

        # Escala da visualização
        self.scale = 1.0

        self.start_x = None
        self.start_y = None

        self.end_x = None
        self.end_y = None

        self.selecting = False

    # -----------------------------------------------------
    # Screenshot
    # -----------------------------------------------------

    def capture_screen(self):

        print()
        print("[SCREEN] Capturando nova tela...")

        path = self.screenshot.capture(
            "screen.png"
        )

        self.image = cv2.imread(
            str(path)
        )

        if self.image is None:

            raise RuntimeError(
                "Não foi possível carregar "
                "o screenshot."
            )

        # Sempre que uma nova imagem for capturada,
        # apagamos qualquer seleção anterior.
        self._reset_selection()

        height, width = self.image.shape[:2]

        print(
            f"[SCREEN] Resolução original: "
            f"{width} x {height}"
        )

        print(
            f"[SCREEN] Escala visual: "
            f"{self.scale:.3f}"
        )

    # -----------------------------------------------------
    # Display
    # -----------------------------------------------------

    def _prepare_display(self):

        height, width = self.image.shape[:2]

        # Tamanho máximo da janela.
        #
        # Isso NÃO altera a imagem original.
        # É somente o tamanho da visualização.

        max_width = 500
        max_height = 900

        scale_x = max_width / width
        scale_y = max_height / height

        self.scale = min(
            scale_x,
            scale_y,
            1.0
        )

        if self.scale < 1.0:

            self.display = cv2.resize(
                self.image,
                None,
                fx=self.scale,
                fy=self.scale,
                interpolation=cv2.INTER_AREA
            )

        else:

            self.display = self.image.copy()

    # -----------------------------------------------------
    # Mouse
    # -----------------------------------------------------

    def mouse_callback(
        self,
        event,
        x,
        y,
        flags,
        param
    ):

        if event == cv2.EVENT_LBUTTONDOWN:

            self.start_x = x
            self.start_y = y

            self.end_x = x
            self.end_y = y

            self.selecting = True

            self._draw_selection()

        elif event == cv2.EVENT_MOUSEMOVE:

            if self.selecting:

                self.end_x = x
                self.end_y = y

                self._draw_selection()

        elif event == cv2.EVENT_LBUTTONUP:

            self.end_x = x
            self.end_y = y

            self.selecting = False

            self._draw_selection()

    # -----------------------------------------------------
    # Desenho
    # -----------------------------------------------------

    def _draw_selection(self):

        self.display = cv2.resize(
            self.image,
            None,
            fx=self.scale,
            fy=self.scale,
            interpolation=cv2.INTER_AREA
        )

        if (
            self.start_x is None
            or self.start_y is None
            or self.end_x is None
            or self.end_y is None
        ):
            return

        cv2.rectangle(
            self.display,
            (
                self.start_x,
                self.start_y
            ),
            (
                self.end_x,
                self.end_y
            ),
            (0, 255, 0),
            2
        )

    # -----------------------------------------------------
    # Seleção
    # -----------------------------------------------------

    def select(self):

        cv2.namedWindow(
            WINDOW_NAME,
            cv2.WINDOW_AUTOSIZE
        )

        cv2.setMouseCallback(
            WINDOW_NAME,
            self.mouse_callback
        )

        print()
        print("===================================")
        print("       TEMPLATE SELECTOR")
        print("===================================")
        print()
        print("A imagem original é mantida intacta.")
        print("Apenas a visualização é reduzida.")
        print()
        print("Arraste sobre o item.")
        print()
        print("ENTER = salvar")
        print("F5    = nova captura da tela")
        print("R     = nova captura da tela")
        print("ESC   = sair")
        print()

        while True:

            cv2.imshow(
                WINDOW_NAME,
                self.display
            )

            key = cv2.waitKey(1) & 0xFF

            # -------------------------------------------------
            # ESC
            # -------------------------------------------------

            if key == 27:
                break

            # -------------------------------------------------
            # F5 / R → NOVA CAPTURA
            # -------------------------------------------------
            #
            # F5 no Windows normalmente retorna 116.
            # O 'r' também foi colocado como alternativa.
            #

            if key == 116 or key == ord("r"):

                print()
                print(
                    "[SCREEN] Atualizando captura..."
                )

                self.capture_screen()

                print(
                    "[SCREEN] Nova imagem carregada."
                )

                continue

            # -------------------------------------------------
            # ENTER → SALVAR
            # -------------------------------------------------

            if key == 13:

                if not self._valid_selection():

                    print(
                        "Seleção inválida."
                    )

                    continue

                self.save_selection()

                self._reset_selection()

        cv2.destroyWindow(
            WINDOW_NAME
        )

    # -----------------------------------------------------
    # Validação
    # -----------------------------------------------------

    def _valid_selection(self):

        if self.start_x is None:
            return False

        if self.start_y is None:
            return False

        if self.end_x is None:
            return False

        if self.end_y is None:
            return False

        width = abs(
            self.end_x - self.start_x
        )

        height = abs(
            self.end_y - self.start_y
        )

        return (
            width > 5
            and height > 5
        )

    # -----------------------------------------------------
    # Salvar
    # -----------------------------------------------------

    def save_selection(self):

        # -------------------------------------------------
        # Coordenadas da visualização
        # -------------------------------------------------

        display_x1 = min(
            self.start_x,
            self.end_x
        )

        display_x2 = max(
            self.start_x,
            self.end_x
        )

        display_y1 = min(
            self.start_y,
            self.end_y
        )

        display_y2 = max(
            self.start_y,
            self.end_y
        )

        # -------------------------------------------------
        # Converte para coordenadas ORIGINAIS
        # -------------------------------------------------

        x1 = int(
            display_x1 / self.scale
        )

        x2 = int(
            display_x2 / self.scale
        )

        y1 = int(
            display_y1 / self.scale
        )

        y2 = int(
            display_y2 / self.scale
        )

        # Garante que não ultrapasse a imagem.

        height, width = self.image.shape[:2]

        x1 = max(
            0,
            min(x1, width)
        )

        x2 = max(
            0,
            min(x2, width)
        )

        y1 = max(
            0,
            min(y1, height)
        )

        y2 = max(
            0,
            min(y2, height)
        )

        # -------------------------------------------------
        # Recorta a imagem ORIGINAL
        # -------------------------------------------------

        crop = self.image[
            y1:y2,
            x1:x2
        ]

        if crop.size == 0:

            print(
                "Recorte vazio."
            )

            return

        # -------------------------------------------------
        # Categoria
        # -------------------------------------------------

        category = self._select_category()

        if category is None:
            return

        # -------------------------------------------------
        # Pasta
        # -------------------------------------------------

        category_dir = (
            TEMPLATES_DIR
            / category
        )

        category_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        # -------------------------------------------------
        # Número
        # -------------------------------------------------

        existing = list(
            category_dir.glob(
                "item_*.png"
            )
        )

        number = len(existing) + 1

        output_path = (
            category_dir
            / f"item_{number:03d}.png"
        )

        # -------------------------------------------------
        # Salva
        # -------------------------------------------------

        success = cv2.imwrite(
            str(output_path),
            crop
        )

        if not success:

            raise RuntimeError(
                "Não foi possível salvar "
                "o template."
            )

        print()
        print("===================================")
        print("Template salvo!")
        print("===================================")
        print(
            f"Categoria: {category}"
        )
        print(
            f"Arquivo:   {output_path}"
        )
        print(
            f"Original:  "
            f"{x1},{y1} → {x2},{y2}"
        )
        print(
            f"Tamanho:   "
            f"{x2 - x1} x {y2 - y1}"
        )
        print("===================================")
        print()

    # -----------------------------------------------------
    # Reset
    # -----------------------------------------------------

    def _reset_selection(self):

        self.start_x = None
        self.start_y = None

        self.end_x = None
        self.end_y = None

        self.selecting = False

        # Só prepara o display se já houver imagem.
        if self.image is not None:

            self._prepare_display()

    # -----------------------------------------------------
    # Categorias
    # -----------------------------------------------------

    def _get_categories(self):

        if not TEMPLATES_DIR.exists():
            return []

        categories = [
            path.name
            for path in TEMPLATES_DIR.iterdir()
            if path.is_dir()
        ]

        return sorted(
            categories,
            key=str.lower
        )

    def _select_category(self):

        categories = self._get_categories()

        print()
        print("===================================")
        print("           CATEGORIAS")
        print("===================================")
        print()

        if categories:

            for index, category in enumerate(
                categories,
                start=1
            ):

                print(
                    f"{index} - {category}"
                )

            print()

        print("0 - Criar nova categoria")
        print()

        while True:

            choice = input(
                "Escolha: "
            ).strip()

            if not choice.isdigit():

                print(
                    "Digite apenas um número."
                )

                continue

            choice = int(choice)

            # Criar nova categoria

            if choice == 0:

                while True:

                    category = input(
                        "Nome da nova categoria: "
                    ).strip()

                    if not category:

                        print(
                            "Nome inválido."
                        )

                        continue

                    invalid_chars = (
                        '<>:"/\\|?*'
                    )

                    if any(
                        char in category
                        for char in invalid_chars
                    ):

                        print(
                            "Nome contém caracteres inválidos."
                        )

                        continue

                    return category

            # Categoria existente

            if 1 <= choice <= len(categories):

                return categories[
                    choice - 1
                ]

            print(
                "Opção inválida."
            )


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":

    selector = TemplateSelector()

    # Primeira captura
    selector.capture_screen()

    # Abre o selector
    selector.select()
