"""Painel opcional: python src/web.py --host 0.0.0.0."""

import argparse
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Painel web do EatVenture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parents[1] / "local")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("A porta deve ficar entre 1 e 65535.")
    try:
        import uvicorn
        from dashboard.server import create_app
    except ImportError as error:
        raise SystemExit("Instale a interface opcional: python -m pip install -r requirements-web.txt") from error
    app = create_app(state_root=args.state_dir)
    print(f"Painel: http://{args.host}:{args.port}", flush=True)
    if not os.environ.get("EATVENTURE_WEB_TOKEN"):
        print(f"Token de acesso salvo em: {args.state_dir.resolve() / 'dashboard.token'}", flush=True)
    # Um processo gerencia o catálogo e os filhos; múltiplos workers duplicariam esse estado.
    uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
