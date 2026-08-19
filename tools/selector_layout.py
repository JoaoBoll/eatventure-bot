"""
Escala e coordenadas do seletor de templates.

Funções puras, sem OpenCV e sem GUI, porque um erro de
mapeamento aqui salva o recorte ERRADO em silêncio — e o
template ruim só aparece como "o bot clica no lugar errado"
semanas depois.

Regra única: a janela acompanha a ALTURA da tela (fração
configurável) e a proporção da imagem é mantida. Nada de
fatiar em colunas — a tela do device aparece inteira, de uma
vez, do jeito que ela é.

    1080x2400 numa tela de 1440 de altura, fração 0.80

      altura disponível = 1152
      escala            = 1152 / 2400 = 0.480
      janela            = 518 x 1152
"""


def escala(
    largura,
    altura,
    max_largura,
    max_altura,
    permitir_ampliar=False,
):
    """
    Maior escala em que a imagem inteira cabe na janela,
    mantendo a proporção.

    Na prática quem manda é a ALTURA: a tela do device é alta e
    estreita, então a largura nunca é o limite. O teto de
    largura fica como proteção para monitor deitado/estreito —
    sem ele a janela sairia da tela.
    """

    valor = min(
        max_largura / largura,
        max_altura / altura,
    )

    if not permitir_ampliar:
        valor = min(valor, 1.0)

    return valor


def tamanho_canvas(largura, altura, valor_escala):
    """
    Tamanho da janela, em pixels de tela.
    """

    return (
        int(round(largura * valor_escala)),
        int(round(altura * valor_escala)),
    )


def para_imagem(x_canvas, y_canvas, largura, altura, valor_escala):
    """
    Coordenada da janela -> coordenada da IMAGEM ORIGINAL,
    já presa aos limites da imagem.
    """

    x = max(0, min(largura, int(round(x_canvas / valor_escala))))
    y = max(0, min(altura, int(round(y_canvas / valor_escala))))

    return x, y


def para_canvas(x_imagem, y_imagem, valor_escala):
    """
    Coordenada da imagem -> janela.

    Usado só para desenhar o retângulo da seleção.
    """

    return (
        int(round(x_imagem * valor_escala)),
        int(round(y_imagem * valor_escala)),
    )


def tela_disponivel(fracao_altura=0.80, fracao_largura=0.95):
    """
    Espaço útil da tela: (largura, altura).

    A altura é a que interessa e vem da fração pedida. A
    largura é folgada de propósito — ela não deve limitar nada
    numa tela de device alta e estreita.

    Fora do Windows devolve None e quem chama usa o fallback
    do config.
    """

    try:

        import ctypes

        usuario = ctypes.windll.user32

        usuario.SetProcessDPIAware()

        largura = usuario.GetSystemMetrics(0)
        altura = usuario.GetSystemMetrics(1)

    except Exception:

        return None

    if not largura or not altura:
        return None

    return (
        int(largura * fracao_largura),
        int(altura * fracao_altura),
    )
