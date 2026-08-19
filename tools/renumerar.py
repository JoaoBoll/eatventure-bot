"""
Compacta a numeração dos templates: item_001..item_NNN, sem
lacunas.

    python tools/renumerar.py              # mostra o que mudaria
    python tools/renumerar.py --aplicar    # renomeia
    python tools/renumerar.py --aplicar --categoria food

Por padrão só MOSTRA, porque renomear arquivo é irreversível.

O template_selector chama isto sozinho antes de salvar, então o
template novo sempre entra no último número da sequência.

Apagar um template no meio deixa lacuna (item_005 faltando entre
004 e 006), e daí o "próximo número" fica ambíguo. Compactar
resolve na origem.

Segurança:

  - processa em ordem CRESCENTE, então o destino de cada
    arquivo já está livre: alvo <= origem, e quem ainda não foi
    processado tem número maior que a origem
  - nunca sobrescreve: se o destino existir, pula e avisa
  - a numeração não aparece em nenhuma parte do runtime além do
    log, e o golden da regressão não guarda o nome do arquivo,
    então renomear não invalida a linha de base
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEMPLATES_DIR = ROOT / "src" / "vision" / "templates"


def numerados(category_dir):
    """
    [(numero, caminho)] dos item_NNN.png, em ordem crescente.
    Arquivos com nome fora do padrão são ignorados.
    """

    encontrados = []

    for path in category_dir.glob("item_*.png"):

        sufixo = path.stem.split("_")[-1]

        if sufixo.isdigit():

            encontrados.append((int(sufixo), path))

    encontrados.sort(key=lambda item: item[0])

    return encontrados


def planejar(category_dir):
    """
    Devolve [(origem, destino)] para deixar a sequência
    contígua. Lista vazia = já está compacta.
    """

    mudancas = []

    for indice, (numero, path) in enumerate(
        numerados(category_dir),
        start=1,
    ):

        if numero == indice:
            continue

        destino = category_dir / f"item_{indice:03d}.png"

        mudancas.append((path, destino))

    return mudancas


def renumerar(category_dir, aplicar=False, log=print):
    """
    Compacta a categoria. Devolve as mudanças realizadas (ou
    planejadas, quando aplicar=False).
    """

    mudancas = planejar(category_dir)

    if not aplicar:
        return mudancas

    feitas = []

    for origem, destino in mudancas:

        if destino.exists():

            log(
                f"  ! {destino.name} já existe — "
                f"{origem.name} não foi renomeado"
            )

            continue

        origem.rename(destino)

        feitas.append((origem, destino))

    return feitas


def proximo_numero(category_dir):
    """
    Número do próximo template, assumindo a sequência já
    compacta: len + 1.
    """

    return len(numerados(category_dir)) + 1


# =========================================================
# Main
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Compacta a numeração dos templates. Sem "
            "--aplicar, só mostra."
        )
    )

    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="renomeia de verdade",
    )

    parser.add_argument(
        "--categoria",
        help="só esta categoria (padrão: todas)",
    )

    args = parser.parse_args()

    if not TEMPLATES_DIR.exists():

        print(f"Pasta não encontrada: {TEMPLATES_DIR}")

        return 1

    pastas = sorted(
        d for d in TEMPLATES_DIR.iterdir() if d.is_dir()
    )

    if args.categoria:

        pastas = [
            d for d in pastas if d.name == args.categoria
        ]

        if not pastas:

            print(f"Categoria '{args.categoria}' não existe.")

            return 1

    total = 0

    for pasta in pastas:

        mudancas = renumerar(pasta, aplicar=args.aplicar)

        if not mudancas:
            continue

        total += len(mudancas)

        verbo = "renomeado" if args.aplicar else "renomearia"

        print(f"\n{pasta.name}:")

        for origem, destino in mudancas:

            print(
                f"  {verbo}: {origem.name} -> {destino.name}"
            )

    print()

    if not total:

        print("Tudo já está compacto.")

        return 0

    if args.aplicar:

        print(f"{total} arquivo(s) renomeado(s).")

    else:

        print(
            f"{total} arquivo(s) mudariam. "
            f"Rode com --aplicar."
        )

    return 0


if __name__ == "__main__":

    sys.exit(main())
