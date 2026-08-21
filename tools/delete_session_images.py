"""
Deleta imagens do dataset por session id.

Uso:
    python tools/delete_session_images.py --session <SESSION_ID> [--yes] [--prune-index]

- Lê <DATASET_DIR>/samples.jsonl (padrão do projeto).
- Para cada amostra cujo campo "session" bate com SESSION_ID,
  resolve o caminho da imagem (campo "image") relativo a DATASET_DIR
  e deleta o arquivo se existir.

Opções:
  --session   ID da sessão (obrigatório)
  --jsonl     caminho para um samples.jsonl alternativo
  --yes       confirma sem pedir prompt (padrão: pede confirmação)
  --prune-index
              se passado, reescreve o samples.jsonl removendo as
              linhas cuja session == SESSION_ID (atenção: destrutivo)
  --dry-run   não apaga nada, só mostra o que faria

Segurança:
- Só deleta arquivos cujo caminho final esteja dentro do DATASET_DIR
  (evita apagar acidentalmente /etc/ ou outras pastas).

"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.config import DATASET_DIR, LOG_LEVEL  # noqa: E402
from core import log  # noqa: E402

log.setup(LOG_LEVEL)
logger = log.get("tools.delete_session_images")


def load_samples(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignorando linha inválida %d em %s", lineno, path)


def is_within_dir(candidate: Path, directory: Path) -> bool:
    try:
        candidate_resolved = candidate.resolve()
        directory_resolved = directory.resolve()
        return directory_resolved in candidate_resolved.parents or candidate_resolved == directory_resolved
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deleta imagens do dataset por session id")

    parser.add_argument("--session", required=True, help="ID da session a remover")

    parser.add_argument(
        "--jsonl",
        help=f"arquivo samples.jsonl (padrão: {DATASET_DIR / 'samples.jsonl'})",
    )

    parser.add_argument("--yes", action="store_true", help="Confirma sem prompt")
    parser.add_argument("--prune-index", action="store_true", help="Remove também as linhas do samples.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="Não apaga nada, só mostra")

    args = parser.parse_args(argv)

    jsonl_path = Path(args.jsonl) if args.jsonl else (DATASET_DIR / "samples.jsonl")

    if not jsonl_path.exists():
        print(f"Índice não encontrado: {jsonl_path}")
        return 1

    session = args.session

    to_delete = []  # list of Path
    lines_to_keep = []
    lines_to_drop = []

    for lineno, sample in load_samples(jsonl_path):
        sid = sample.get("session")
        if sid == session:
            image_rel = sample.get("image")
            if not image_rel:
                logger.warning("Amostra %s sem campo 'image' (linha %d), pulando", sample.get("id"), lineno)
                lines_to_drop.append(lineno)
                continue
            image_path = (DATASET_DIR / image_rel).resolve()
            # Segurança: só apagar se dentro de DATASET_DIR
            if not is_within_dir(image_path, DATASET_DIR):
                logger.warning("Caminho %s fora de %s — não será apagado", image_path, DATASET_DIR)
                lines_to_drop.append(lineno)
                continue
            to_delete.append((lineno, image_path))
            lines_to_drop.append(lineno)
        else:
            # keep this line
            lines_to_keep.append((lineno, sample))

    if not to_delete:
        print(f"Nenhuma amostra encontrada para session '{session}' no índice {jsonl_path}.")
        return 0

    print(f"Acharam {len(to_delete)} imagem(ns) para session '{session}':")
    for ln, p in to_delete:
        print(f"  [line {ln}] {p}")

    if args.dry_run:
        print("Dry-run: nada será apagado.")
        return 0

    if not args.yes:
        resp = input("Confirma apagar estes arquivos? [y/N]: ").strip().lower()
        if resp not in ("y", "yes"):
            print("Abortado pelo usuário.")
            return 0

    deleted = 0
    missing = 0

    for ln, p in to_delete:
        try:
            if p.exists():
                p.unlink()
                deleted += 1
                logger.info("Apagado %s", p)
            else:
                missing += 1
                logger.warning("Arquivo ausente: %s (linha %d)", p, ln)
        except Exception as e:
            logger.exception("Falha apagando %s: %s", p, e)

    print(f"Deletados: {deleted}; faltantes: {missing}.")

    if args.prune_index:
        # Reescrever o índice sem as linhas a remover. Simples e seguro: cria um temp e substitui.
        backup = jsonl_path.with_suffix(jsonl_path.suffix + ".bak")
        jsonl_path.rename(backup)
        try:
            with backup.open("r", encoding="utf-8") as rfh, jsonl_path.open("w", encoding="utf-8") as wfh:
                for lineno, line in enumerate(rfh, start=1):
                    if lineno in lines_to_drop:
                        continue
                    wfh.write(line)
        except Exception as e:
            logger.exception("Erro reescrevendo índice: %s", e)
            # Tentar restaurar backup
            if jsonl_path.exists():
                jsonl_path.unlink()
            backup.rename(jsonl_path)
            print("Falha reescrevendo índice; backup restaurado.")
            return 1

        print(f"Índice atualizado (linhas removidas): {jsonl_path} — backup em {backup}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
