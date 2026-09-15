"""
Extração de features. FONTE ÚNICA para treino e inferência.

Existe por um motivo específico: antes o `train_ai.py` tinha
`load_image_features` e o `bot_ai.py` tinha `preprocess_frame`,
duas cópias da mesma conta. Se uma mudasse sem a outra, o modelo
passaria a receber entrada diferente da que treinou — e isso não
dá erro nenhum, só previsão ruim. Aqui existe uma versão só.

--------------------------------------------------------------
O DIAGNÓSTICO QUE MOTIVOU ESTE ARQUIVO
--------------------------------------------------------------

A versão anterior reduzia o frame INTEIRO de 1080x2400 para
32x32 e mandava os 3072 pixels crus para o classificador.

Medido no dataset (29.297 amostras, 65.730 caixas):

    alvo mediano ................ 95 x 94 px
    a 32x32 ele vira ............ 2.8 x 1.3 px

O objeto DESAPARECIA antes de chegar ao modelo. Daí a fidelidade
abaixo de 30%, contra:

    chutar a classe majoritária ....... 16.7%
    só olhar o estado da máquina ...... 46.2%
    estado + categorias na tela ....... 96.0%   <- teto real

O modelo estava PIOR que olhar só o estado.

A correção não é aumentar a resolução da tela toda: mesmo a
256x256 o alvo teria 22 x 10 px. É recortar o OBJETO e usar a
mesma 32x32 nele — aí o alvo preenche o quadro em vez de ocupar
3 pixels.
"""

import cv2
import numpy as np

# Lado do recorte alimentado ao classificador.
#
# 32 é o mesmo número de antes, de propósito: o que mudou não é
# a resolução, é O QUE está dentro dela. Antes, a tela inteira
# (alvo com 3 px); agora, o objeto (alvo preenchendo o quadro).
PATCH_SIZE = 32

# Margem em volta da caixa, em fração do lado.
#
# Contexto ajuda: o mesmo ícone significa coisas diferentes
# dentro e fora de um painel. Margem grande demais, porém, volta
# a diluir o objeto.
PATCH_MARGIN = 0.25

# Bins do histograma HSV, por canal.
#
# O jogo codifica muito por COR (botão azul, badge vermelho,
# painel cinza). O histograma captura isso de forma invariante à
# posição dentro do recorte, por 48 features baratas.
HSV_BINS = 16

FEATURE_SIZE = PATCH_SIZE * PATCH_SIZE * 3 + HSV_BINS * 3


# =========================================================
# RECORTE
# =========================================================

def crop_box(image, box, margin=PATCH_MARGIN):
    """
    Recorta a região de uma caixa, com margem, presa aos limites
    da imagem.

    `box` aceita dict com x/y/width/height (o formato do
    samples.jsonl) ou tupla (x, y, w, h).
    """

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


# =========================================================
# FEATURES
# =========================================================

def patch_features(patch):
    """
    Vetor de features de um recorte já cortado.

    Duas partes:

      pixels 32x32 RGB ..... forma e layout (3072)
      histograma HSV ....... cor, invariante à posição (48)

    Normalização de aspect ratio: redimensiona preservando a proporção
    e adiciona padding preto, garantindo compatibilidade entre múltiplas
    resoluções de tela.

    Devolve None se o recorte for vazio.
    """

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
    """
    Recorta e extrai, num passo. É o caminho usado tanto no
    treino quanto na inferência.
    """

    return patch_features(crop_box(image, box, margin))


# =========================================================
# FEATURES DE TELA INTEIRA (modelo de comparação)
# =========================================================

SCREEN_WIDTH = 48
SCREEN_HEIGHT = 96


def screen_features(image):
    """
    Features da tela inteira, para o modelo `action` que existe
    apenas como COMPARAÇÃO.

    Aspecto preservado (48x96 contra 1080x2400 é 1:2, igual ao
    original), ao contrário do 32x32 anterior, que espremia
    2.2:1 em 1:1. Mas isto não conserta o problema de fundo: o
    alvo continua com poucos pixels. Serve para medir o quanto o
    recorte por objeto melhora.
    """

    if image is None or image.size == 0:
        return None

    pequeno = cv2.resize(
        image,
        (SCREEN_WIDTH, SCREEN_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )

    rgb = cv2.cvtColor(pequeno, cv2.COLOR_BGR2RGB)

    return (
        rgb.astype(np.float32).reshape(-1) / 255.0
    ).astype(np.float32)


SCREEN_FEATURE_SIZE = SCREEN_WIDTH * SCREEN_HEIGHT * 3
