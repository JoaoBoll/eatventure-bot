"""
Log central.

Substitui os prints espalhados. O timestamp com milissegundos
é o que permite separar "detectou errado" de "detectou tarde".
"""

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
    Muda o nível do que APARECE no terminal, sem tocar no
    nível do logger.

    Existe para o painel de status: ele precisa que o INFO de
    cada ação pare de rolar na tela, mas quem lê o log depois
    (arquivo, handler futuro) continua recebendo tudo — o
    logger segue em INFO, só este handler sobe.

    Filtrar no logger em vez de no handler perderia o registro
    de verdade, e aí a informação não estaria em lugar nenhum.
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
