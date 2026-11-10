"""Seletor visual de múltiplas áreas nas quais templates serão ignorados."""

import argparse
import sys
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from template_selector import TemplateSelector  # noqa: E402
from core import devices, log  # noqa: E402
from core.block_regions import (  # noqa: E402
    MAX_BLOCK_REGIONS,
    load_block_regions,
    save_block_regions,
)
from core.config import DEVICE_SERIAL, LOG_LEVEL  # noqa: E402
import selector_layout as layout  # noqa: E402


WINDOW_NAME = "BLOCK Selector"


class BlockSelector(TemplateSelector):
    def __init__(self, serial, regions=None, state_root=None):
        self.regions = [tuple(region) for region in (regions or [])]
        self.state_root = state_root
        super().__init__(serial)

    def _render(self):
        super()._render()
        if self.display is None or self.image is None:
            return
        height, width = self.image.shape[:2]
        for index, (x, y, region_width, region_height) in enumerate(self.regions, 1):
            start = layout.para_canvas(round(x * width), round(y * height), self.scale)
            end = layout.para_canvas(
                round((x + region_width) * width),
                round((y + region_height) * height),
                self.scale,
            )
            overlay = self.display.copy()
            cv2.rectangle(overlay, start, end, (0, 0, 220), -1)
            cv2.addWeighted(overlay, 0.28, self.display, 0.72, 0, self.display)
            cv2.rectangle(self.display, start, end, (0, 0, 255), 2)
            cv2.putText(
                self.display, f"BLOCK {index}",
                (start[0] + 5, min(end[1] - 5, start[1] + 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA,
            )

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_RBUTTONUP:
            self.undo()
            return
        super().mouse_callback(event, x, y, flags, param)
        if event != cv2.EVENT_LBUTTONUP or not self._valid_selection():
            return
        if len(self.regions) >= MAX_BLOCK_REGIONS:
            print(f"Limite de {MAX_BLOCK_REGIONS} BLOCKs atingido.")
            self._reset_selection()
            return
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)
        height, width = self.image.shape[:2]
        self.regions.append((x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height))
        print(f"BLOCK {len(self.regions)} adicionado: {x1},{y1} -> {x2},{y2}")
        self._reset_selection()

    def undo(self):
        if not self.regions:
            return
        removed = len(self.regions)
        self.regions.pop()
        self._reset_selection()
        print(f"BLOCK {removed} removido.")

    def save(self):
        save_block_regions(self.capture.serial, self.regions, self.state_root)
        print(f"{len(self.regions)} área(s) BLOCK salva(s) para {self.capture.serial}.")

    def select(self):
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW_NAME, self.mouse_callback)
        print("\nBLOCK SELECTOR")
        print("Afeta somente: box, food, new_point e up_food.")
        print("Arraste com o botão esquerdo para adicionar várias áreas.")
        print("Botão direito, U ou Backspace = remover a última área")
        print("Enter ou S = salvar todas | R ou F5 = atualizar frame | Esc = sair\n")
        try:
            while True:
                cv2.imshow(WINDOW_NAME, self.display)
                key = cv2.waitKey(1) & 0xFF
                if key == 27:
                    break
                if key in (8, ord("u")):
                    self.undo()
                elif key in (13, ord("s")):
                    self.save()
                elif key in (116, ord("r")):
                    self.capture_screen()
        finally:
            cv2.destroyWindow(WINDOW_NAME)
            self._tk_root.destroy()
            self.capture.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seleciona áreas BLOCK na tela do device")
    parser.add_argument("--device", metavar="SERIAL", help="serial exibido por adb devices")
    parser.add_argument("--state-dir", type=Path, help="pasta local usada pelo painel (padrão: local/)")
    args = parser.parse_args(argv)
    log.setup(LOG_LEVEL)
    try:
        serial = devices.resolver(args.device or DEVICE_SERIAL)
    except KeyboardInterrupt:
        print("Cancelado.")
        return 1
    selector = BlockSelector(serial, load_block_regions(serial, args.state_dir), args.state_dir)
    selector.capture_screen()
    selector.select()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
