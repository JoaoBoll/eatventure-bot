"""Valida referências e formatos dos catálogos; nunca gera traduções."""

import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from string import Formatter


ROOT = Path(__file__).resolve().parent.parent
FORMATTER = Formatter()


def flatten(catalog):
    return {
        f"{surface}.{key}": value
        for surface, values in catalog.items()
        for key, value in values.items()
    }


def fields(text):
    return sorted((field, conversion, spec) for _, field, spec, conversion in FORMATTER.parse(text) if field is not None)


def percent_fields(text):
    return re.findall(r"%(?:\([^)]+\))?[-+#0 ]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]", text.replace("%%", ""))


def main():
    catalogs = {
        language: flatten(json.loads((ROOT / "src/locales" / f"{language}.json").read_text(encoding="utf-8")))
        for language in ("pt-BR", "en-US")
    }
    base = catalogs["pt-BR"]
    errors = []
    for language, catalog in catalogs.items():
        for key in base.keys() ^ catalog.keys():
            errors.append(f"{language}: {key}")
        for key, value in catalog.items():
            surface, name = key.split(".", 1)
            if surface in {"web", "messages", "terminal", "http"} and (
                len(name) > 40 or re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name) is None
            ):
                errors.append(f"{language}: {key} (key name)")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{language}: {key}")
                continue
            if key in base and (fields(value) != fields(base[key]) or percent_fields(value) != percent_fields(base[key])):
                errors.append(f"{language}: {key} (placeholders)")

    def require(key, location):
        if key not in base:
            errors.append(f"{location}: {key}")

    for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "IA").glob("*.py")]:
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            key = node.args[0].value
            location = f"{path.relative_to(ROOT)}:{node.lineno}"
            require(key, location)
            if key in base:
                expected = {field for field, _, _ in fields(base[key])}
                supplied = {item.arg for item in node.keywords}
                if expected != supplied:
                    errors.append(f"{location}: {key} ({expected} != {supplied})")

    javascript = (ROOT / "src/dashboard/static/app.js").read_text(encoding="utf-8")
    for key in re.findall(r'["\']((?:web|terminal|messages|states|actions|categories)\.[^"\']+)["\']', javascript):
        require(key, "app.js")

    class Markup(HTMLParser):
        def handle_starttag(self, tag, attributes):
            names = [name for name, _ in attributes]
            if len(names) != len(set(names)):
                errors.append(f"index.html:{self.getpos()[0]}: {tag} (attributes)")
            for name, value in attributes:
                if name == "data-i18n" or name.startswith("data-i18n-"):
                    require(value, f"index.html:{self.getpos()[0]}")

    markup = (ROOT / "src/dashboard/static/index.html").read_text(encoding="utf-8")
    Markup().feed(markup)
    for key in re.findall(r"\{\{([\w.]+)\}\}", markup):
        require(key, "index.html")
    if errors:
        raise SystemExit("\n".join(errors))
    print(base["terminal.catalog_valid"].format(count=len(base)))


if __name__ == "__main__":
    main()
