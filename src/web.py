"""Painel opcional: python src/web.py."""

import argparse
import os
from pathlib import Path

from core.processes import process_alive
from core.cli import ArgumentParser, configure_locale
from core.i18n import t


def _alive(pid):
    return process_alive(pid)


def _claim_pid(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        previous = None
    if previous and _alive(previous):
        raise RuntimeError(t("terminal.already_running", pid=previous))
    temporary = path.with_suffix(".pid.tmp")
    temporary.write_text(f"{os.getpid()}\n", encoding="ascii")
    os.replace(temporary, path)


def _release_pid(path):
    try:
        if path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv=None):
    configure_locale(argv)
    parser = ArgumentParser(description=t("terminal.description"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--require-token", action="store_true", help=t("terminal.require_token_help"))
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parents[1] / "local", help=t("terminal.state_dir_help"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error(t("terminal.port_error"))
    pid_path = args.state_dir.resolve() / "web.pid"
    try:
        _claim_pid(pid_path)
    except RuntimeError as error:
        parser.error(str(error))
    try:
        import uvicorn
        from dashboard.server import create_app
    except ImportError as error:
        _release_pid(pid_path)
        raise SystemExit(t("terminal.install_web")) from error
    from core import config, log

    log.setup(config.LOG_LEVEL)
    app = create_app(state_root=args.state_dir, require_token=args.require_token)
    display_host = "localhost" if args.host == "0.0.0.0" else args.host
    print(t("terminal.panel_url", host=display_host, port=args.port), flush=True)
    if args.host == "0.0.0.0":
        print(t("terminal.network_url", port=args.port), flush=True)
    if app.state.auth_required and not os.environ.get("EATVENTURE_WEB_TOKEN"):
        print(t("terminal.token_path", path=args.state_dir.resolve() / "dashboard.token"), flush=True)
    # Um processo gerencia o catálogo e os filhos; múltiplos workers duplicariam esse estado.
    try:
        server = uvicorn.Server(uvicorn.Config(
            app, host=args.host, port=args.port, workers=1, access_log=False,
        ))
        app.state.web_server = server
        server.run()
    finally:
        _release_pid(pid_path)


if __name__ == "__main__":
    main()
