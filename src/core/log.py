"""Log central; timestamp com milissegundos permite separar "detectou errado" de "detectou tarde"."""

import logging
import sys


def setup(level="INFO"):

    root = logging.getLogger("eatventure")

    if root.handlers:
        return root

    root.setLevel(
        getattr(logging, level, logging.INFO)
    )

    handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname).1s "
                "[%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root.addHandler(handler)

    return root


def set_console_level(level):
    """
    Muda o nível do que APARECE no terminal, sem tocar no nível do
    logger — filtrar no logger perderia o registro para sempre, e o
    painel de status precisa só que o INFO pare de rolar na tela.
    """

    root = logging.getLogger("eatventure")

    alvo = (
        level
        if isinstance(level, int)
        else getattr(logging, str(level).upper(), logging.WARNING)
    )

    for handler in root.handlers:

        if isinstance(handler, logging.StreamHandler):
            handler.setLevel(alvo)

    return alvo


def get(name):

    return logging.getLogger(
        f"eatventure.{name}"
    )
