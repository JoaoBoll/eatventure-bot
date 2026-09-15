"""
Escala e coordenadas do seletor de templates. Funções puras (sem
OpenCV/GUI) porque um erro de mapeamento aqui salva o recorte errado
em silêncio. A janela acompanha a ALTURA da tela (fração
configurável) mantendo a proporção da imagem inteira.
"""


def escala(
    largura,
    altura,
    max_largura,
    max_altura,
    permitir_ampliar=False,
):
    """Maior escala em que a imagem cabe na janela, mantendo a proporção. Quem manda é a altura; o teto de largura só protege monitores estreitos."""

    valor = min(
        max_largura / largura,
        max_altura / altura,
    )

    if not permitir_ampliar:
        valor = min(valor, 1.0)

    return valor


def tamanho_canvas(largura, altura, valor_escala):
    """Tamanho da janela, em pixels de tela."""

    return (
        int(round(largura * valor_escala)),
        int(round(altura * valor_escala)),
    )


def para_imagem(x_canvas, y_canvas, largura, altura, valor_escala):
    """Coordenada da janela -> coordenada da imagem original, presa aos limites."""

    x = max(0, min(largura, int(round(x_canvas / valor_escala))))
    y = max(0, min(altura, int(round(y_canvas / valor_escala))))

    return x, y


def para_canvas(x_imagem, y_imagem, valor_escala):
    """Coordenada da imagem -> janela. Usado só para desenhar o retângulo da seleção."""

    return (
        int(round(x_imagem * valor_escala)),
        int(round(y_imagem * valor_escala)),
    )


def tela_disponivel(fracao_altura=0.80, fracao_largura=0.95):
    """Espaço útil da tela: (largura, altura). Windows via WinAPI; em Linux/Mac devolve None e quem chama usa o fallback do config."""

    try:

        import ctypes
        import platform

        if platform.system() != "Windows":
            return None

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
