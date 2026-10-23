"""Entrada isolada usada pelo supervisor; não inicia interface gráfica."""

import os
from pathlib import Path


def main():
    required = ("EATVENTURE_PROFILE", "EATVENTURE_DEVICE_ID", "EATVENTURE_STATUS_FILE", "EATVENTURE_STOP_FILE")
    if any(not os.environ.get(key) for key in required):
        raise SystemExit("Este processo deve ser iniciado pelo painel web.")
    from core import config
    from core.runtime import RuntimeControl
    for name in ("DEVICE_TEMPLATE_DIR", "AI_DEVICE_MODEL_DIR"):
        directory = getattr(config, name, None)
        if directory is not None:
            Path(directory).mkdir(parents=True, exist_ok=True)
    runtime = RuntimeControl(
        os.environ["EATVENTURE_DEVICE_ID"], os.environ["EATVENTURE_STATUS_FILE"],
        os.environ["EATVENTURE_STOP_FILE"], os.environ.get("EATVENTURE_SESSION_ID", ""),
    )
    runtime.install_signals()
    runtime.publish({"estado": "iniciando"}, force=True)
    try:
        import main as bot
        if not runtime.should_stop():
            return bot.main(runtime=runtime)
    finally:
        runtime.preview.stop()
        runtime.publish({"estado": "encerrado"}, force=True)


if __name__ == "__main__":
    raise SystemExit(main())
