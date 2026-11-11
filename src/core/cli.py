"""Idioma explícito das opções e mensagens dos comandos do projeto."""

import argparse
import os
import sys

from core.i18n import SUPPORTED_LOCALES, localized, normalize_locale, t


def configure_locale(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    for index, argument in enumerate(arguments):
        if argument.startswith("--locale="):
            os.environ["EATVENTURE_LOCALE"] = normalize_locale(argument.split("=", 1)[1])
        elif argument == "--locale" and index + 1 < len(arguments):
            os.environ["EATVENTURE_LOCALE"] = normalize_locale(arguments[index + 1])


class _UsagePrefix:
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or t("terminal.usage"))


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        configure_locale()
        formatter = kwargs.pop("formatter_class", argparse.HelpFormatter)
        kwargs["formatter_class"] = type("LocalizedHelpFormatter", (_UsagePrefix, formatter), {})
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = t("terminal.positional_arguments")
        self._optionals.title = t("terminal.options")
        if add_help:
            self.add_argument("-h", "--help", action="help", help=t("terminal.help"))
        self.add_argument("--locale", choices=(*SUPPORTED_LOCALES, "pt", "en"), help=t("terminal.locale_help"))

    def parse_known_args(self, args=None, namespace=None):
        configure_locale(args)
        self.description = localized(self.description)
        self._positionals.title = t("terminal.positional_arguments")
        self._optionals.title = t("terminal.options")
        for action in self._actions:
            action.help = localized(action.help)
        return super().parse_known_args(args, namespace)
