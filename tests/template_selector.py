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

# Relativo ao ARQUIVO: antes só funcionava rodando a partir
# da raiz do repositório.
TEMPLATES_DIR = ROOT / "src" / "vision" / "templates"


class TemplateSelector:

    def __init__(self, serial=None):

        # O serial TEM de chegar no screencap: sem o -s, com
        # dois devices na lista o adb recusa a captura.
        self.screenshot = AndroidScreenshot(serial=serial)

        # Imagem ORIGINAL
        self.image = None

        # Imagem usada apenas para visualização
        self.display = None

        # Escala da visualização
        self.scale = 1.0

        # Coordenadas da IMAGEM ORIGINAL, não da janela: a
        # janela é reduzida, e converter só na hora de salvar
        # seria uma chance a mais de recortar do lugar errado.
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

    def _janela(self):
        """
        Tamanho máximo da janela, em pixels de tela.
        """

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
        """
        Calcula a escala. NÃO altera a imagem original.
        """

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
        """
        Monta a visualização: a imagem inteira, reduzida.
        """

        height, width = self.image.shape[:2]

        if self.scale == 1.0:

            self.display = self.image.copy()

            return

        self.display = cv2.resize(
            self.image,
            layout.tamanho_canvas(width, height, self.scale),
            interpolation=cv2.INTER_AREA,
        )

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
        """
        Guarda coordenadas da IMAGEM ORIGINAL, não da janela.
        """

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

    # -----------------------------------------------------
    # Desenho
    # -----------------------------------------------------

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

        # Tamanho real do recorte, em pixels do device: é o que
        # importa para o template, não o tamanho na tela.
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
        # Coordenadas da IMAGEM ORIGINAL
        # -------------------------------------------------
        #
        # Já vêm convertidas do mouse_callback. Dividir pela
        # escala aqui de novo recortaria do lugar errado.
        #

        x1 = min(self.start_x, self.end_x)
        x2 = max(self.start_x, self.end_x)

        y1 = min(self.start_y, self.end_y)
        y2 = max(self.start_y, self.end_y)

        height, width = self.image.shape[:2]

        x1 = max(0, min(x1, width))
        x2 = max(0, min(x2, width))

        y1 = max(0, min(y1, height))
        y2 = max(0, min(y2, height))

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
        #
        # Antes era len(existing) + 1, que SOBRESCREVE em
        # silêncio quando há lacuna na sequência: food tinha
        # 17 arquivos mas ia até item_020, então o próximo
        # calculado era item_018 — que já existia.
        #
        # Agora a sequência é COMPACTADA antes de salvar
        # (item_005 faltando entre 004 e 006 faz o 006 virar
        # 005, o 007 virar 006, e assim por diante), e o novo
        # template entra no último número.
        #
        # Assim "próximo número" volta a ser simplesmente
        # len + 1, sem ambiguidade.
        #

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

    # Primeira captura
    selector.capture_screen()

    # Abre o selector
    selector.select()

    return 0


if __name__ == "__main__":

    sys.exit(main())
