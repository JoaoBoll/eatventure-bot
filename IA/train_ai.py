#!/usr/bin/env python3
"""
Treino do modelo de visão, a partir de dataset/samples.jsonl.

    python IA/train_ai.py                       # modelo de categoria
    python IA/train_ai.py --kind action         # comparação
    python IA/train_ai.py --outcome changed     # só ações que funcionaram
    python IA/train_ai.py --split session       # teste mais duro
    python IA/train_ai.py --limit 5000          # ensaio rápido

==============================================================
POR QUE A FIDELIDADE ESTAVA ABAIXO DE 30%
==============================================================

Não era falta de dado: são 29.297 amostras e 65.730 caixas.
Eram três coisas, medidas no dataset:

1. O ALVO DESAPARECIA.
   A versão anterior reduzia o frame inteiro de 1080x2400 para
   32x32. O alvo mediano tem 95x94 px, então virava 2.8 x 1.3
   px. Não existe classificador que resolva isso.

   Aumentar a resolução da tela não resolve: mesmo a 256x256 o
   alvo teria 22 x 10 px. O que resolve é recortar o OBJETO e
   usar 32x32 NELE.

2. O SPLIT ERA INVÁLIDO.
   56% das amostras têm `phash` repetido — o bot age ~1x/s numa
   tela quase estática. Com `train_test_split` aleatório, o
   mesmo quadro caía em treino e teste, e a acurácia deixava de
   significar qualquer coisa.

3. NÃO HAVIA REFERÊNCIA.
   "30%" sozinho não informa. As referências deste dataset:

       chutar a classe majoritária ....... 16.7%
       só olhar o estado da máquina ...... 46.2%
       estado + categorias na tela ....... 96.0%   <- teto

   O modelo anterior estava ABAIXO de olhar só o estado, ou
   seja, atrás de uma regra de uma linha. Sem imprimir essas
   linhas, ninguém percebe.

==============================================================
A ARQUITETURA QUE ESTE ARQUIVO TREINA
==============================================================

O teto de 96% para "estado + categorias" é a chave: se saber o
que está na tela resolve 96% da escolha da ação, então o que
precisa ser APRENDIDO é detectar categoria — e a ação sai da
tabela de prioridade que já existe em core/state_machine.py e
já é testada.

    --kind category   (padrão, recomendado)
        recorte de caixa -> categoria
        65.730 exemplos, objeto preenchendo o quadro
        substitui os 184 templates recortados à mão, e
        generaliza para prato que nunca foi visto

    --kind action     (comparação)
        tela inteira -> ação
        mantido para medir a diferença, não para usar

Este arquivo NÃO tem testes: eles ficam em tests/test_ia.py.
E NÃO instala dependência sozinho — instalar pacote como efeito
colateral de treinar é surpresa ruim.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for candidato in (str(ROOT), str(ROOT / "src"), str(ROOT / "IA")):
    if candidato not in sys.path:
        sys.path.insert(0, candidato)

DEFAULT_DATASET = ROOT / "dataset"
DEFAULT_MODEL = ROOT / "IA" / "model.joblib"

# Categorias que a máquina de estados precisa para SAIR de um
# estado. Hoje são as duas regras de RENOVATE_RULES
# (core/state_machine.py): sem `renovate` ou `fly` o bot fica
# preso na tela de reforma.
#
# Elas entram aqui, e não numa lista genérica de "classes
# raras", porque o custo de perdê-las não é acurácia: é o bot
# travar. `fly` tem POUCAS caixas e a maior forma do dataset
# (414x141 contra 95x94 da mediana), então é a primeira a sumir
# num --limit, num --outcome apertado ou num split de sessão
# infeliz — e nada nos números avisa.
CATEGORIAS_CRITICAS = ("renovate", "fly")


# =========================================================
# DEPENDÊNCIAS
# =========================================================

def check_dependencies():
    """
    Confere e ORIENTA. Não instala.

    A versão anterior rodava `pip install` de dentro do treino.
    Instalar pacote como efeito colateral é surpresa ruim: muda
    o ambiente de quem só queria treinar, e num venv errado
    quebra outra coisa.
    """

    faltando = []

    for modulo, pacote in (
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("sklearn", "scikit-learn"),
        ("joblib", "joblib"),
    ):

        try:
            __import__(modulo)

        except ImportError:
            faltando.append(pacote)

    if faltando:

        raise SystemExit(
            "Faltam dependências de treino.\n\n"
            f"  {sys.executable} -m pip install "
            f"{' '.join(faltando)}\n"
        )


# =========================================================
# PROGRESSO
# =========================================================

def progress(feito, total, fase):

    if total <= 0:
        return

    fracao = feito / total

    largura = 28

    cheio = int(round(fracao * largura))

    barra = "#" * cheio + "." * (largura - cheio)

    sys.stdout.write(
        f"\r\033[2K  [{barra}] {fracao * 100:5.1f}%  {fase}"
    )

    sys.stdout.flush()

    if feito >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


# =========================================================
# MONTAGEM DAS FEATURES
# =========================================================

def build_category_dataset(registros, negativos_por_frame, seed=42):
    """
    Um exemplo por CAIXA, mais negativos sorteados.

    Devolve (X, y, info).
    """

    import cv2
    import numpy as np

    from dataset_io import box_labels, sample_negative_boxes
    from features import FEATURE_SIZE, box_features

    # Tamanhos reais observados, para os negativos não serem
    # distinguíveis só pela dimensão.
    tamanhos = [
        (c["width"], c["height"])
        for r in registros
        for c in (r.get("boxes") or [])
        if c.get("width") and c.get("height")
    ]

    linhas = []
    rotulos = []

    ilegiveis = 0
    vazios = 0

    total = len(registros)

    for indice, registro in enumerate(registros, start=1):

        imagem = cv2.imread(str(registro["_path"]))

        if imagem is None:

            ilegiveis += 1

            progress(indice, total, "lendo imagens")

            continue

        for caixa, categoria, _agiu in box_labels(registro):

            vetor = box_features(imagem, caixa)

            if vetor is None:

                vazios += 1

                continue

            linhas.append(vetor)
            rotulos.append(categoria)

        if negativos_por_frame > 0:

            for caixa in sample_negative_boxes(
                registro,
                negativos_por_frame,
                tamanhos,
                seed=seed + indice,
            ):

                vetor = box_features(imagem, caixa)

                if vetor is None:

                    vazios += 1

                    continue

                linhas.append(vetor)
                rotulos.append("background")

        if indice % 25 == 0 or indice == total:
            progress(indice, total, "lendo imagens")

    if not linhas:

        raise SystemExit(
            "Nenhum recorte válido foi extraído. As imagens "
            "existem mas estão ilegíveis, ou as caixas do "
            "índice têm largura/altura zero."
        )

    X = np.asarray(linhas, dtype=np.float32)

    assert X.shape[1] == FEATURE_SIZE, (X.shape[1], FEATURE_SIZE)

    return X, np.asarray(rotulos), {
        "ilegiveis": ilegiveis,
        "recortes_vazios": vazios,
    }


def build_action_dataset(registros):
    """
    Um exemplo por FRAME, tela inteira -> ação.

    Existe para comparação. O alvo continua com poucos pixels
    aqui — é o ponto do diagnóstico, não uma alternativa.
    """

    import cv2
    import numpy as np

    from dataset_io import action_label
    from features import SCREEN_FEATURE_SIZE, screen_features

    linhas = []
    rotulos = []

    ilegiveis = 0

    total = len(registros)

    for indice, registro in enumerate(registros, start=1):

        imagem = cv2.imread(str(registro["_path"]))

        if imagem is None:

            ilegiveis += 1

        else:

            vetor = screen_features(imagem)

            if vetor is not None:

                linhas.append(vetor)
                rotulos.append(action_label(registro))

        if indice % 25 == 0 or indice == total:
            progress(indice, total, "lendo imagens")

    if not linhas:
        raise SystemExit("Nenhuma imagem legível.")

    X = np.asarray(linhas, dtype=np.float32)

    assert X.shape[1] == SCREEN_FEATURE_SIZE

    return X, np.asarray(rotulos), {"ilegiveis": ilegiveis}


# =========================================================
# CLASSIFICADOR
# =========================================================

def build_classifier(trees, seed, jobs):

    from sklearn.ensemble import RandomForestClassifier

    return RandomForestClassifier(
        n_estimators=trees,
        random_state=seed,
        n_jobs=jobs,

        # Sem isto as classes raras somem: `plane` tem 52
        # caixas contra 17.642 de `food`. Um modelo que ignora
        # `plane` acerta 99.9% e é inútil justamente onde
        # importa.
        class_weight="balanced_subsample",

        # Folha com 1 amostra decora ruído de compressão JPG.
        min_samples_leaf=2,
    )


# =========================================================
# RELATÓRIO
# =========================================================

def report(clf, X_teste, y_teste, y_treino, referencias, kind="category"):
    """
    Imprime o que permite julgar o modelo, não só um número.
    """

    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
    )

    from dataset_io import majority_baseline

    previsto = clf.predict(X_teste)

    acuracia = accuracy_score(y_teste, previsto)

    base, classe_base = majority_baseline(list(y_teste))

    print()
    print("=" * 62)
    print("  RESULTADO")
    print("=" * 62)
    print()
    print(f"  acurácia do modelo ............. {acuracia:7.2%}")
    print(f"  chutar a maior classe ('{classe_base}') "
          f"{'':>{max(0, 8 - len(str(classe_base)))}}{base:7.2%}")

    for nome, valor in referencias.items():
        print(f"  {nome:<30} {valor:7.2%}")

    print()

    if acuracia <= base:

        print("  !! O modelo NÃO bate o chute na classe "
              "majoritária.")
        print("     Não é ajuste fino: a formulação ou as "
              "features estão erradas.")
        print()

    print("-" * 62)
    print("  POR CLASSE")
    print("-" * 62)
    print()

    print(
        classification_report(
            y_teste,
            previsto,
            zero_division=0,
            digits=3,
        )
    )

    # A média macro é o número que expõe classe rara ignorada:
    # a acurácia crua fica alta mesmo errando tudo em `plane`.
    print("-" * 62)
    print("  CONFUSÃO (linha = verdade, coluna = previsto)")
    print("-" * 62)
    print()

    classes = sorted(set(y_teste) | set(previsto))

    matriz = confusion_matrix(y_teste, previsto, labels=classes)

    largura = max(len(c) for c in classes) + 1

    cabecalho = " " * largura + "".join(
        f"{c[:6]:>7}" for c in classes
    )

    print(cabecalho)

    for nome, linha in zip(classes, matriz):

        print(
            f"{nome:<{largura}}"
            + "".join(f"{v:>7d}" for v in linha)
        )

    print()

    # Onde o modelo mais erra, em pares. É o que diz se o
    # problema é uma confusão específica (dá para resolver) ou
    # ruído geral (não dá).
    erros = Counter()

    for verdade, palpite in zip(y_teste, previsto):

        if verdade != palpite:
            erros[(str(verdade), str(palpite))] += 1

    # Só faz sentido no modelo de categoria: em --kind action os
    # rótulos são ações ("renovate_click"), não categorias.
    if kind == "category":
        _report_criticas(y_teste, y_treino, previsto)

    if erros:

        print("  maiores confusões:")

        for (verdade, palpite), quantas in erros.most_common(8):

            print(
                f"    {verdade:<16} previsto como "
                f"{palpite:<16} {quantas:5d}"
            )

        print()

    return acuracia


def _report_criticas(y_teste, y_treino, previsto):
    """
    As críticas, sempre e nominalmente.

    No classification_report elas passam batido no meio de 17
    linhas, e no agregado um recall de 0% em `fly` custa ~0.1%
    de acurácia — invisível no número, fatal no bot.
    """

    print("-" * 62)
    print("  CATEGORIAS QUE FECHAM RENOVATE")
    print("-" * 62)
    print()

    treino_contagem = Counter(str(v) for v in y_treino)
    teste_contagem = Counter(str(v) for v in y_teste)

    for categoria in CATEGORIAS_CRITICAS:

        no_treino = treino_contagem.get(categoria, 0)
        no_teste = teste_contagem.get(categoria, 0)

        acertos = sum(
            1
            for verdade, palpite in zip(y_teste, previsto)
            if str(verdade) == categoria and str(palpite) == categoria
        )

        recall = (acertos / no_teste) if no_teste else None

        linha = (
            f"  {categoria:<12} treino {no_treino:6d} | "
            f"teste {no_teste:6d} | recall "
            + (f"{recall:7.2%}" if recall is not None else "     n/d")
        )

        if not no_treino:
            linha += "   <- NÃO TREINADA"

        elif recall is not None and recall < 0.5:
            linha += "   <- perde mais da metade"

        print(linha)

    print()


# =========================================================
# TREINO
# =========================================================

def train(args):

    from dataset_io import (
        action_label,
        ceiling_state_categories,
        filter_by_outcome,
        read_index,
        split_groups,
        state_baseline,
    )

    raiz = Path(args.dataset_root).expanduser().resolve()

    print(f"dataset ......... {raiz}")

    registros, aviso = read_index(raiz)

    print(f"amostras ........ {len(registros)}")

    if aviso["invalidas"] or aviso["sem_imagem"]:

        print(
            f"  ignoradas: {aviso['invalidas']} linha(s) "
            f"inválida(s), {aviso['sem_imagem']} sem imagem"
        )

    if args.outcome:

        antes = len(registros)

        registros = filter_by_outcome(registros, args.outcome)

        print(
            f"filtro outcome .. {args.outcome} "
            f"({antes} -> {len(registros)})"
        )

    if args.limit:

        registros = registros[: args.limit]

        print(f"limite .......... {len(registros)} amostras")

    if len(registros) < 20:

        raise SystemExit(
            f"Amostras insuficientes ({len(registros)}). "
            "Rode o bot mais tempo com RECORD_DATASET ligado."
        )

    # -----------------------------------------------------
    # Referências, antes de treinar
    # -----------------------------------------------------

    referencias = {
        "só o estado da máquina": state_baseline(registros),
        "estado + categorias (teto)":
            ceiling_state_categories(registros),
    }

    print()
    print("referências deste dataset:")

    for nome, valor in referencias.items():
        print(f"  {nome:<30} {valor:7.2%}")

    # -----------------------------------------------------
    # Split por grupo
    # -----------------------------------------------------

    treino, teste, info = split_groups(
        registros,
        train_ratio=args.train_ratio,
        mode=args.split,
        seed=args.seed,
    )

    print()
    print(
        f"split por {info['mode']}: "
        f"{info['grupos_treino']} grupos de treino / "
        f"{info['grupos_teste']} de teste"
    )
    print(
        f"  {info['amostras_treino']} amostras de treino, "
        f"{info['amostras_teste']} de teste"
    )
    print(
        "  nenhum grupo aparece nos dois lados — é o que faz a "
        "acurácia significar algo"
    )
    print()

    if not teste:

        raise SystemExit(
            "Conjunto de teste vazio. Baixe o --train-ratio."
        )

    # -----------------------------------------------------
    # Features
    # -----------------------------------------------------

    inicio = time.monotonic()

    if args.kind == "category":

        print(f"montando recortes de treino ({len(treino)} frames)")

        X_treino, y_treino, extra = build_category_dataset(
            treino,
            args.negatives,
            seed=args.seed,
        )

        print(f"montando recortes de teste ({len(teste)} frames)")

        X_teste, y_teste, _ = build_category_dataset(
            teste,
            args.negatives,
            seed=args.seed + 9999,
        )

    else:

        print(f"montando telas de treino ({len(treino)} frames)")

        X_treino, y_treino, extra = build_action_dataset(treino)

        print(f"montando telas de teste ({len(teste)} frames)")

        X_teste, y_teste, _ = build_action_dataset(teste)

    if extra.get("ilegiveis"):

        print(f"  {extra['ilegiveis']} imagem(ns) ilegível(is)")

    if extra.get("recortes_vazios"):

        print(f"  {extra['recortes_vazios']} recorte(s) vazio(s)")

    print(
        f"  treino {X_treino.shape[0]} x {X_treino.shape[1]} | "
        f"teste {X_teste.shape[0]} x {X_teste.shape[1]} "
        f"({time.monotonic() - inicio:.0f}s)"
    )

    contagem = Counter(y_treino.tolist())

    print()
    print("classes no treino:")

    for classe, quantas in contagem.most_common():
        print(f"  {classe:<16} {quantas:7d}")

    if len(contagem) < 2:

        raise SystemExit(
            "Só uma classe no treino. Nada a aprender."
        )

    faltantes = set(y_teste.tolist()) - set(contagem)

    if faltantes:

        print()
        print(
            f"  aviso: {sorted(faltantes)} aparece(m) só no "
            "teste — o modelo não pode acertar essas"
        )

    # -----------------------------------------------------
    # Treino
    # -----------------------------------------------------

    print()
    print(f"treinando ({args.trees} árvores)...")

    inicio = time.monotonic()

    clf = build_classifier(args.trees, args.seed, args.jobs)

    clf.fit(X_treino, y_treino)

    print(f"  {time.monotonic() - inicio:.0f}s")

    acuracia = report(
        clf, X_teste, y_teste, y_treino, referencias, args.kind
    )

    # -----------------------------------------------------
    # Gravação
    # -----------------------------------------------------

    import joblib

    from features import (
        HSV_BINS,
        PATCH_MARGIN,
        PATCH_SIZE,
        SCREEN_HEIGHT,
        SCREEN_WIDTH,
    )

    destino = Path(args.model_out).expanduser().resolve()

    destino.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(clf, destino)

    # O metadado registra COMO as features foram feitas. Sem
    # isso, mudar PATCH_SIZE e usar um modelo antigo dá entrada
    # com tamanho diferente — e o bot_ai confere isto no load.
    meta = {
        "kind": args.kind,
        "labels": [str(c) for c in clf.classes_],
        "counts": dict(sorted(contagem.items())),
        "accuracy": float(acuracia),
        "baselines": {
            nome: float(valor)
            for nome, valor in referencias.items()
        },
        "split": info,
        "outcome_filter": args.outcome,
        "features": {
            "patch_size": PATCH_SIZE,
            "patch_margin": PATCH_MARGIN,
            "hsv_bins": HSV_BINS,
            "screen_width": SCREEN_WIDTH,
            "screen_height": SCREEN_HEIGHT,
            "n_features": int(X_treino.shape[1]),
        },
        "trees": args.trees,
        "seed": args.seed,
    }

    destino.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"modelo .......... {destino}")
    print(f"metadado ........ {destino.with_suffix('.meta.json')}")
    print()

    if args.kind == "action":

        print(
            "Lembrete: `--kind action` existe para comparação.\n"
            "O alvo mediano tem 95x94 px e a tela inteira é\n"
            "reduzida, então ele não alcança o teto de 96%. O\n"
            "modelo para usar no bot é o `category`."
        )

    return 0


# =========================================================
# CLI
# =========================================================

def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description=(
            "Treina o modelo de visão a partir de "
            "dataset/samples.jsonl."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--kind",
        choices=("category", "action"),
        default="category",
        help=(
            "category (padrão): recorte de caixa -> categoria, "
            "e a ação sai da tabela de prioridade que já existe. "
            "action: tela inteira -> ação, mantido só para "
            "comparação."
        ),
    )

    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_DATASET),
        help="pasta do dataset (padrão: dataset/)",
    )

    parser.add_argument(
        "--model-out",
        default=str(DEFAULT_MODEL),
        help="onde gravar o modelo",
    )

    parser.add_argument(
        "--split",
        choices=("phash", "session"),
        default="phash",
        help=(
            "agrupamento do split. phash (padrão): quadros "
            "iguais não cruzam treino/teste. session: sessão "
            "inteira de um lado, o teste mais honesto."
        ),
    )

    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="fração de GRUPOS para treino (padrão: 0.8)",
    )

    parser.add_argument(
        "--outcome",
        nargs="*",
        default=None,
        metavar="RESULTADO",
        help=(
            "só amostras com esses resultados: changed, "
            "unchanged, unknown, negative. Treinar em "
            "'changed negative' deixa de fora as ações do "
            "professor que não funcionaram."
        ),
    )

    parser.add_argument(
        "--negatives",
        type=int,
        default=2,
        help=(
            "recortes de fundo sorteados por frame, no modo "
            "category (padrão: 2). Sem eles todo pedaço de "
            "cenário viraria detecção. 0 desliga."
        ),
    )

    parser.add_argument(
        "--trees",
        type=int,
        default=200,
        help="árvores da floresta (padrão: 200)",
    )

    parser.add_argument(
        "--jobs",
        type=int,
        default=-1,
        help="núcleos (padrão: -1, todos)",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="usa só as N primeiras amostras (ensaio rápido)",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="semente (padrão: 42)",
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)

    check_dependencies()

    return train(args)


if __name__ == "__main__":

    raise SystemExit(main())
