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


def get(name):

    return logging.getLogger(
        f"eatventure.{name}"
    )
