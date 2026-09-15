import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

# Sem output_dir, grava em tests/capture/ — nunca em
# tests/images/, que são os fixtures da regressão.
from android_screenshot import AndroidScreenshot  # noqa: E402
from renumerar import proximo_numero, renumerar   # noqa: E402
import selector_layout as layout                  # noqa: E402
from core import devices, log                     # noqa: E402
from vision.detector import Detector               # noqa: E402
from core.config import (                         # noqa: E402
    DEVICE_SERIAL,
    LOG_LEVEL,
    SELECTOR_FALLBACK_HEIGHT,
    SELECTOR_FALLBACK_WIDTH,
    SELECTOR_HEIGHT_FRACTION,
    SELECTOR_MAX_HEIGHT,
    SELECTOR_MAX_WIDTH,
    SELECTOR_WIDTH_FRACTION,
)


WINDOW_NAME = "Template Selector"
TEMPLATES_DIR = ROOT / "src" / "vision" / "templates"
DEFAULT_TEMPLATES_DIR = TEMPLATES_DIR / "default"

# Captura fora da referência: fica aqui, crua, fora do runtime (o bot
# aprende o override certo da resolução dele sozinho, jogando aqui um
# recorte torto só pioraria o default para todo mundo).
FALLBACK_TEMPLATES_DIR = TEMPLATES_DIR / "default_selector"


class TemplateSelector:

    def __init__(self, serial=None):
        self.screenshot = AndroidScreenshot(serial=serial)
        self.image = None  # imagem original
        self.display = None  # visualização reduzida
        self.scale = 1.0
        self.start_x = None  # coordenadas da imagem original, não da janela
        self.start_y = None
        self.end_x = None
        self.end_y = None
        self.selecting = False

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

    def _janela(self):
        """Tamanho máximo da janela, em pixels de tela."""

        largura = SELECTOR_MAX_WIDTH
        altura = SELECTOR_MAX_HEIGHT

        if largura and altura:
            return largura, altura

        tela = layout.tela_disponivel(
            SELECTOR_HEIGHT_FRACTION,
            SELECTOR_WIDTH_FRACTION,
        )

        if tela is None:

            tela = (
                SELECTOR_FALLBACK_WIDTH,
                SELECTOR_FALLBACK_HEIGHT,
            )

        return (
            largura or tela[0],
            altura or tela[1],
        )

    def _prepare_display(self):
        """Calcula a escala; não altera a imagem original."""

        height, width = self.image.shape[:2]

        max_width, max_height = self._janela()

        self.scale = layout.escala(
            width,
            height,
            max_width,
            max_height,
        )

        janela = layout.tamanho_canvas(width, height, self.scale)

        print(
            f"[JANELA] {janela[0]}x{janela[1]} | "
            f"escala {self.scale:.3f} "
            f"(1 px na tela = "
            f"{1 / self.scale:.1f} px do device)"
        )

        self._render()

    def _render(self):
        """Monta a visualização: a imagem inteira, reduzida."""

        height, width = self.image.shape[:2]

        if self.scale == 1.0:

            self.display = self.image.copy()

            return

        self.display = cv2.resize(
            self.image,
            layout.tamanho_canvas(width, height, self.scale),
            interpolation=cv2.INTER_AREA,
        )

    def mouse_callback(
        self,
        event,
        x,
        y,
        flags,
        param
    ):
        """Guarda coordenadas da imagem original, não da janela."""

        if event == cv2.EVENT_LBUTTONDOWN:

            self.start_x, self.start_y = self._para_imagem(x, y)

            self.end_x = self.start_x
            self.end_y = self.start_y

            self.selecting = True

            self._draw_selection()

        elif event == cv2.EVENT_MOUSEMOVE:

            if self.selecting:

                self.end_x, self.end_y = self._para_imagem(x, y)

                self._draw_selection()

        elif event == cv2.EVENT_LBUTTONUP:

            self.end_x, self.end_y = self._para_imagem(x, y)

            self.selecting = False

            self._draw_selection()

    def _para_imagem(self, x, y):

        height, width = self.image.shape[:2]

        return layout.para_imagem(
            x,
            y,
            width,
            height,
            self.scale,
        )

    def _draw_selection(self):

        self._render()

        if (
            self.start_x is None
            or self.start_y is None
            or self.end_x is None
            or self.end_y is None
        ):
            return

        canto_a = layout.para_canvas(
            self.start_x,
            self.start_y,
            self.scale,
        )

        canto_b = layout.para_canvas(
            self.end_x,
            self.end_y,
            self.scale,
        )

        cv2.rectangle(
            self.display,
            canto_a,
            canto_b,
            (0, 255, 0),
            2
        )

        # tamanho real do recorte (pixels do device), não o tamanho na tela
        largura = abs(self.end_x - self.start_x)
        altura = abs(self.end_y - self.start_y)

        etiqueta = f"{largura}x{altura}"

        posicao = (
            min(canto_a[0], canto_b[0]),
            max(min(canto_a[1], canto_b[1]) - 8, 14),
        )

        cv2.putText(
            self.display,
            etiqueta,
            posicao,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )

        cv2.putText(
            self.display,
            etiqueta,
            posicao,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

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

            if key == 27:
                break

            # F5 no Windows retorna 116; 'r' é alternativa
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

    def save_selection(self):

        # coordenadas já vêm convertidas do mouse_callback; dividir pela escala de novo recortaria errado
        x1 = min(self.start_x, self.end_x)
        x2 = max(self.start_x, self.end_x)

        y1 = min(self.start_y, self.end_y)
        y2 = max(self.start_y, self.end_y)

        height, width = self.image.shape[:2]

        x1 = max(0, min(x1, width))
        x2 = max(0, min(x2, width))

        y1 = max(0, min(y1, height))
        y2 = max(0, min(y2, height))

        crop = self.image[
            y1:y2,
            x1:x2
        ]

        if crop.size == 0:

            print(
                "Recorte vazio."
            )

            return

        # Fora da referência o recorte não é reescalado: o bot aprende o
        # override certo daquela resolução em runtime, então um "default"
        # torto só pioraria a detecção para todo mundo.
        group_dir = (
            DEFAULT_TEMPLATES_DIR
            if Detector._is_reference(width, height)
            else FALLBACK_TEMPLATES_DIR
        )

        category = self._select_category()

        if category is None:
            return

        category_dir = (
            group_dir
            / category
        )

        category_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        if category == "food":

            output_path = self._food_output_path(
                category_dir
            )

        else:

            # compacta a sequência antes de salvar: len(existing)+1 sobrescreveria
            # em silêncio se houvesse lacuna (ex.: food com 17 arquivos indo até item_020)
            renomeados = renumerar(
                category_dir,
                aplicar=True
            )

            if renomeados:

                print()
                print(
                    f"[NUMERAÇÃO] {len(renomeados)} arquivo(s) "
                    f"compactado(s) em {category}:"
                )

                for origem, destino in renomeados:

                    print(
                        f"  {origem.name} -> {destino.name}"
                    )

            number = proximo_numero(category_dir)

            output_path = (
                category_dir
                / f"item_{number:03d}.png"
            )

            while output_path.exists():

                number += 1

                output_path = (
                    category_dir
                    / f"item_{number:03d}.png"
                )

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
            f"Pasta:     {group_dir.name}"
        )
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
            f"Recorte:   "
            f"{x2 - x1} x {y2 - y1} (device)"
        )
        print("===================================")
        print()

    def _food_output_path(self, category_dir):
        """Food é nomeado pelo item, não por número — vários bots/estágios usam o mesmo template e o nome no arquivo ajuda a identificar qual é qual."""

        invalid_chars = '<>:"/\\|?*'

        while True:

            name = input(
                "Nome do food: "
            ).strip()

            if not name:

                print(
                    "Nome inválido."
                )

                continue

            if any(
                char in name
                for char in invalid_chars
            ):

                print(
                    "Nome contém caracteres inválidos."
                )

                continue

            break

        name = name.replace(" ", "_")

        output_path = category_dir / f"{name}.png"

        numero = 2

        while output_path.exists():

            output_path = category_dir / f"{name}_{numero}.png"

            numero += 1

        return output_path

    def _reset_selection(self):

        self.start_x = None
        self.start_y = None

        self.end_x = None
        self.end_y = None

        self.selecting = False

        if self.image is not None:

            self._prepare_display()

    def _get_categories(self):
        """Categorias do default: toda pasta de resolução nasce com este mesmo conjunto como padrão."""

        if not DEFAULT_TEMPLATES_DIR.exists():
            return []

        categories = [
            path.name
            for path in DEFAULT_TEMPLATES_DIR.iterdir()
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

            if 1 <= choice <= len(categories):

                return categories[
                    choice - 1
                ]

            print(
                "Opção inválida."
            )


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Recorta templates da tela do device",
    )

    parser.add_argument(
        "--device",
        metavar="SERIAL",
        help=(
            "serial do device (adb devices). Sem isto, usa "
            "DEVICE_SERIAL do config; sem os dois, pergunta "
            "quando houver mais de um conectado."
        ),
    )

    args = parser.parse_args(argv)

    log.setup(LOG_LEVEL)

    try:

        serial = devices.resolver(args.device or DEVICE_SERIAL)

    except KeyboardInterrupt:

        print("Cancelado.")

        return 1

    selector = TemplateSelector(serial)

    selector.capture_screen()
    selector.select()

    return 0


if __name__ == "__main__":

    sys.exit(main())
