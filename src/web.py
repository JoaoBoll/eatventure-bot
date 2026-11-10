"""Painel opcional: python src/web.py."""

import argparse
import os
from pathlib import Path

from core.processes import process_alive


def _alive(pid):
    return process_alive(pid)


def _claim_pid(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        previous = None
    if previous and _alive(previous):
        raise RuntimeError(f"O painel já está em execução (PID {previous}).")
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
    parser = argparse.ArgumentParser(description="Painel web do EatVenture")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--require-token", action="store_true", help="Exige um token para acessar o painel")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parents[1] / "local")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("A porta deve ficar entre 1 e 65535.")
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
        raise SystemExit("Instale a interface opcional: python -m pip install -r requirements-web.txt") from error
    from core import config, log

    log.setup(config.LOG_LEVEL)
    app = create_app(state_root=args.state_dir, require_token=args.require_token)
    display_host = "localhost" if args.host == "0.0.0.0" else args.host
    print(f"Painel: http://{display_host}:{args.port}", flush=True)
    if args.host == "0.0.0.0":
        print(f"Na rede: http://IP_DO_SERVIDOR:{args.port}", flush=True)
    if app.state.auth_required and not os.environ.get("EATVENTURE_WEB_TOKEN"):
        print(f"Token de acesso salvo em: {args.state_dir.resolve() / 'dashboard.token'}", flush=True)
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
