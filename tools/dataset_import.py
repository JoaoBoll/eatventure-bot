"""
Importa um samples.jsonl para o PostgreSQL.

    python tools/dataset_import.py
    python tools/dataset_import.py --dsn postgresql://...
    python tools/dataset_import.py --jsonl outro/samples.jsonl

Este é o caminho normal de uso: o bot grava arquivos, e o banco
entra DEPOIS — inclusive para sessões gravadas antes de existir
banco. Rodar o bot com o banco ligado é só conveniência.

Reimportar é seguro: as amostras têm chave primária e o INSERT
usa ON CONFLICT DO NOTHING.

Rode antes o DDL de docs/dataset.md.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

from core import log                              # noqa: E402
from core.config import (                         # noqa: E402
    DATASET_DB_DSN,
    DATASET_DB_SCHEMA,
    DATASET_DIR,
    LOG_LEVEL,
)
from dataset.store import importar_jsonl          # noqa: E402


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Importa o dataset para o PostgreSQL.",
    )

    parser.add_argument(
        "--jsonl",
        help=(
            "arquivo de índice (padrão: "
            "<DATASET_DIR>/samples.jsonl)"
        ),
    )

    parser.add_argument(
        "--dsn",
        help=(
            "postgresql://usuario:senha@host:5432/banco "
            "(padrão: DATASET_DB_DSN do config)"
        ),
    )

    parser.add_argument(
        "--schema",
        default=DATASET_DB_SCHEMA,
        help=f"schema (padrão: {DATASET_DB_SCHEMA})",
    )

    parser.add_argument(
        "--batch",
        type=int,
        default=200,
        help="amostras por ida ao banco (padrão: 200)",
    )

    args = parser.parse_args(argv)

    log.setup(LOG_LEVEL)

    caminho = Path(
        args.jsonl
        if args.jsonl
        else DATASET_DIR / "samples.jsonl"
    )

    if not caminho.exists():

        print(f"Índice não encontrado: {caminho}")
        print()
        print("Ligue DATASET_SAVE no config e rode o bot,")
        print("ou aponte --jsonl para o arquivo certo.")

        return 1

    dsn = args.dsn or DATASET_DB_DSN

    if not dsn:

        print("Sem DSN configurado. Não há banco para importar — pulando etapa de importação.")
        print()
        print("Se quiser realmente importar para um banco, passe --dsn ou preencha DATASET_DB_DSN em src/core/config.py")
        print("Formato:  ******host:5432/banco")


        return 0

    print(f"Importando {caminho}...")

    try:

        total = importar_jsonl(
            caminho,
            dsn,
            batch_size=args.batch,
            schema=args.schema,
        )

    except Exception as error:

        print()
        print(f"Falhou: {type(error).__name__}: {error}")
        print()
        print("Conferir:")
        print("  - as tabelas existem? (docs/dataset.md)")
        print("  - o DSN está certo?")
        print("  - psycopg instalado? "
              "pip install 'psycopg[binary]'")

        return 1

    print()
    print(f"{total} amostra(s) importada(s).")
    print()
    print("Reimportar é seguro: chave primária + "
          "ON CONFLICT DO NOTHING.")

    return 0


if __name__ == "__main__":

    sys.exit(main())
