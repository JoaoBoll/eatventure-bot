"""Catálogos JSON explícitos para textos de apresentação."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import json
import os
from pathlib import Path


LOCALES = Path(__file__).resolve().parents[1] / "locales"
DEFAULT_LOCALE = "pt-BR"
SUPPORTED_LOCALES = ("pt-BR", "en-US")
_request_locale = ContextVar("eatventure_locale", default=None)


def normalize_locale(value):
    value = str(value or "").replace("_", "-").lower()
    return {"en": "en-US", "en-us": "en-US", "pt": "pt-BR", "pt-br": "pt-BR"}.get(value, DEFAULT_LOCALE)


def locale():
    return _request_locale.get() or normalize_locale(os.environ.get("EATVENTURE_LOCALE"))


@contextmanager
def locale_scope(language):
    token = _request_locale.set(normalize_locale(language))
    try:
        yield
    finally:
        _request_locale.reset(token)


@lru_cache(maxsize=2)
def messages(language):
    return json.loads((LOCALES / f"{normalize_locale(language)}.json").read_text(encoding="utf-8"))


def template(key, language=None):
    surface, name = key.split(".", 1)
    return messages(language or locale())[surface][name]


def localized(value, language=None):
    if isinstance(value, Text):
        return value.render(language)
    if isinstance(value, BaseException) and len(value.args) == 1 and isinstance(value.args[0], Text):
        return value.args[0].render(language)
    if isinstance(value, tuple):
        return tuple(localized(item, language) for item in value)
    if isinstance(value, list):
        return [localized(item, language) for item in value]
    if isinstance(value, dict):
        return {key: localized(item, language) for key, item in value.items()}
    return value


class Text(str):
    def __new__(cls, key, values):
        instance = super().__new__(cls, cls._render(key, values, locale()))
        instance.key = key
        instance.values = values
        return instance

    @staticmethod
    def _render(key, values, language):
        text = template(key, language)
        return text.format(**localized(values, language)) if values else text

    def render(self, language=None):
        return self._render(self.key, self.values, language or locale())


def t(key, /, **values):
    return Text(key, values)


def display_code(surface, code):
    """Rótulos conhecidos; identificadores externos continuam sendo dados."""
    if code in messages(locale()).get(surface, {}):
        return t(f"{surface}.{code}")
    return code
