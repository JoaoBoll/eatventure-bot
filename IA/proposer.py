"""Proposer: candidates por saturação + contorno (experimental, validar com --debug-proposals)."""

import cv2
import numpy as np

# Faixa de tamanho aceitável, medida no dataset com folga.
MIN_SIDE = 24
MAX_WIDTH = 460
MAX_HEIGHT = 200

# Saturação mínima para um pixel ser "elemento de interface".
MIN_SATURATION = 90

# Brilho mínimo: sombra saturada não é botão.
MIN_VALUE = 70

# Fechamento morfológico, para o badge e o botão embaixo dele
# virarem um candidato só em vez de dois pedaços.
CLOSE_KERNEL = 9

# Proporção aceitável (largura/altura). `fly` é 414x102 = 4.1,
# `up_upgrade` é 32x88 = 0.36.
MIN_ASPECT = 0.2
MAX_ASPECT = 5.0

# Teto de candidatos por frame. Cada um custa uma classificação,
# então sem teto uma tela cheia de cor derruba o FPS.
MAX_PROPOSALS = 60


def propose(frame, escala=0.5):
    """
    Devolve [(x, y, w, h)] de candidatos, em coordenada do FRAME.

    `escala` reduz o frame antes de achar contorno — a conta cai
    4x e a caixa é reescalada de volta. Blocos de interface são
    grandes, então a redução não os perde.
    """

    if frame is None or frame.size == 0:
        return []

    pequeno = (
        cv2.resize(
            frame,
            None,
            fx=escala,
            fy=escala,
            interpolation=cv2.INTER_AREA,
        )
        if escala < 1.0
        else frame
    )

    hsv = cv2.cvtColor(pequeno, cv2.COLOR_BGR2HSV)

    mascara = cv2.inRange(
        hsv,
        (0, MIN_SATURATION, MIN_VALUE),
        (180, 255, 255),
    )

    lado = max(3, int(round(CLOSE_KERNEL * escala)))

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (lado, lado),
    )

    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, kernel)

    contornos, _ = cv2.findContours(
        mascara,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidatos = []

    for contorno in contornos:

        x, y, w, h = cv2.boundingRect(contorno)

        # De volta ao espaço do frame.
        x = int(round(x / escala))
        y = int(round(y / escala))
        w = int(round(w / escala))
        h = int(round(h / escala))

        if w < MIN_SIDE or h < MIN_SIDE:
            continue

        if w > MAX_WIDTH or h > MAX_HEIGHT:
            continue

        proporcao = w / h

        if proporcao < MIN_ASPECT or proporcao > MAX_ASPECT:
            continue

        candidatos.append((x, y, w, h, w * h))

    # Maiores primeiro: se o teto cortar, corta os menores, que
    # são os mais prováveis de serem ruído de cenário.
    candidatos.sort(key=lambda c: -c[4])

    return [c[:4] for c in candidatos[:MAX_PROPOSALS]]


def suppress(deteccoes, limite=0.4):
    """
    Remove sobreposições da MESMA categoria, mantendo a de maior
    confiança.

    Sem isto o mesmo botão aparece três vezes, e a máquina de
    estados agiria sobre a primeira que encontrasse — que não é
    necessariamente a melhor.
    """

    if not deteccoes:
        return []

    ordenadas = sorted(
        deteccoes,
        key=lambda d: -d["confidence"],
    )

    mantidas = []

    for candidata in ordenadas:

        repetida = False

        for guardada in mantidas:

            if guardada["category"] != candidata["category"]:
                continue

            if _iou(candidata, guardada) > limite:

                repetida = True

                break

        if not repetida:
            mantidas.append(candidata)

    return mantidas


def _iou(a, b):

    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["width"], ay1 + a["height"]

    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["width"], by1 + b["height"]

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    intersecao = (ix2 - ix1) * (iy2 - iy1)

    uniao = (
        a["width"] * a["height"]
        + b["width"] * b["height"]
        - intersecao
    )

    return intersecao / uniao if uniao > 0 else 0.0
