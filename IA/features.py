"""
Extração de features. FONTE ÚNICA para treino e inferência — antes
train_ai.py e bot_ai.py tinham cópias próprias, e se uma mudasse sem a
outra o modelo recebia entrada diferente da que treinou (sem erro, só
previsão ruim).

Recorta o objeto e usa 32x32 NELE, em vez de reduzir a tela inteira: o alvo
mediano (95x94 px) desapareceria (2.8x1.3 px) mesmo reduzindo a tela toda a
256x256.
"""

import cv2
import numpy as np

# 32x32 com margem: contexto ajuda (ícone fora vs dentro painel)
PATCH_SIZE = 32
PATCH_MARGIN = 0.25

# Histograma HSV: captura cor invariante a posição
HSV_BINS = 16

FEATURE_SIZE = PATCH_SIZE * PATCH_SIZE * 3 + HSV_BINS * 3


def crop_box(image, box, margin=PATCH_MARGIN):
    if isinstance(box, dict):
        x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    else:
        x, y, w, h = box

    altura, largura = image.shape[:2]
    folga_x = int(round(w * margin))
    folga_y = int(round(h * margin))

    x1 = max(0, x - folga_x)
    y1 = max(0, y - folga_y)
    x2 = min(largura, x + w + folga_x)
    y2 = min(altura, y + h + folga_y)

    if x2 <= x1 or y2 <= y1:
        return None

    return image[y1:y2, x1:x2]


def patch_features(patch):
    """Pixels 32x32 RGB (forma/layout) + histograma HSV (cor). Redimensiona
    preservando proporção e faz padding preto para caber em múltiplas resoluções."""

    if patch is None or patch.size == 0:
        return None

    if patch.shape[0] < 2 or patch.shape[1] < 2:
        return None

    h, w = patch.shape[:2]
    ratio = w / h

    if ratio > 1:
        novo_w = PATCH_SIZE
        novo_h = int(round(PATCH_SIZE / ratio))
    else:
        novo_h = PATCH_SIZE
        novo_w = int(round(PATCH_SIZE * ratio))

    novo_w = max(1, novo_w)
    novo_h = max(1, novo_h)

    pequeno = cv2.resize(
        patch,
        (novo_w, novo_h),
        interpolation=cv2.INTER_AREA,
    )

    top = (PATCH_SIZE - novo_h) // 2
    bottom = PATCH_SIZE - novo_h - top
    left = (PATCH_SIZE - novo_w) // 2
    right = PATCH_SIZE - novo_w - left

    quadrado = cv2.copyMakeBorder(
        pequeno,
        top, bottom, left, right,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )

    rgb = cv2.cvtColor(quadrado, cv2.COLOR_BGR2RGB)

    pixels = rgb.astype(np.float32).reshape(-1) / 255.0

    hsv = cv2.cvtColor(quadrado, cv2.COLOR_BGR2HSV)

    histogramas = []

    for canal in range(3):

        hist = cv2.calcHist(
            [hsv],
            [canal],
            None,
            [HSV_BINS],
            [0, 180 if canal == 0 else 256],
        ).reshape(-1)

        total = hist.sum()

        histogramas.append(
            hist / total if total > 0 else hist
        )

    return np.concatenate(
        [pixels] + histogramas
    ).astype(np.float32)


def box_features(image, box, margin=PATCH_MARGIN):
    return patch_features(crop_box(image, box, margin))


SCREEN_WIDTH = 48
SCREEN_HEIGHT = 96


def screen_features(image):
    # modelo de comparação: tela inteira -> ação (alvo fica pequeno demais)
    if image is None or image.size == 0:
        return None

    pequeno = cv2.resize(image, (SCREEN_WIDTH, SCREEN_HEIGHT), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(pequeno, cv2.COLOR_BGR2RGB)
    return (rgb.astype(np.float32).reshape(-1) / 255.0).astype(np.float32)


SCREEN_FEATURE_SIZE = SCREEN_WIDTH * SCREEN_HEIGHT * 3
