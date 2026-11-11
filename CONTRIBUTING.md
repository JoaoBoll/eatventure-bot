# Contributing

## Translations

The documentation is available in English and Brazilian Portuguese. To add a
language, create `README.<locale>.md` using the appropriate BCP 47 locale
code, such as `README.es.md` or `README.fr.md`.

Keep commands, file paths, option names, configuration keys, template category
names and product names unchanged. Translate explanatory text, headings and
link labels. Preserve the same sections and relative links as `README.md`.

Add the new language to the **Languages** list in both `README.md` and
`README.pt-BR.md`. When changing the English README, update every existing
translation in the same pull request when possible; otherwise, mention the
sections that need review.

## Interface localization

The terminal and web panel use explicit JSON catalogs in `src/locales/`,
currently `pt-BR.json` and `en-US.json`. Text keys are grouped into namespaces,
such as `terminal`, `web` and `messages`, and referenced as `web.device_count`.
Use short English `snake_case` names describing the purpose of the text, not
the entire sentence. Text keys are limited to 40 characters.

Keep placeholders unchanged, for example `{host}`, `{port}` and `{pid}`.
Every new visible message must have the same key and matching placeholders
in both catalogs. Use `t("web.device_count", count=total)` in Python and
`t("web.device_count", { count: total })` in JavaScript. There is no automatic
translation, lookup by message contents or generated key fallback.

The terminal locale is selected with `--locale` or `EATVENTURE_LOCALE`; the
dashboard has its own language selector. Do not translate internal category
identifiers, commands, paths or option names. Display labels for known
identifiers belong in their explicit catalog mappings.

Run `python tools/sync_locale_catalog.py --check` for static catalog validation.
Despite its historical name, this command only checks catalogs and references;
it never generates translations. See `docs/i18n.md` and `docs/training.md` for
the localization and web training workflows.

## Pull requests

Keep each pull request focused. Explain the user-visible change and any part
of the documentation that could not be verified.
