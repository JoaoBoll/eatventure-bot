#!/usr/bin/env python3
"""
Treino do modelo de visão, a partir de dataset/samples.jsonl.

    python IA/train_ai.py                       # modelo de categoria
    python IA/train_ai.py --kind action         # comparação
    python IA/train_ai.py --outcome changed     # só ações que funcionaram
    python IA/train_ai.py --test-ratio 0.3      # teste com 30% aleatório
    python IA/train_ai.py --limit 5000          # ensaio rápido

`--kind category` (padrão) treina recorte-de-caixa -> categoria: o alvo
mediano (95x94 px) desaparece se a tela inteira for reduzida a 32x32, então
o que funciona é recortar o objeto antes. A ação final vem da tabela de
prioridade de core/state_machine.py, não do modelo. `--kind action` (tela
inteira -> ação) fica só para comparação, não alcança o teto de acurácia.

O split de teste é aleatório por índice, não por sessão/tempo — 56% das
amostras têm `phash` repetido (bot roda ~1x/s em tela quase estática), então
comparar com os baselines de `state_baseline`/`ceiling_state_categories` é
o que garante que a acurácia significa algo.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for candidato in (str(ROOT), str(ROOT / "src"), str(ROOT / "IA")):
    if candidato not in sys.path:
        sys.path.insert(0, candidato)

DEFAULT_DATASET = ROOT / "dataset"
DEFAULT_MODEL = ROOT / "IA" / "model.joblib"

# renovate/fly: sem elas o bot fica preso na tela de reforma (RENOVATE_RULES
# em core/state_machine.py). `fly` tem poucas caixas e a maior forma do
# dataset (414x141 vs 95x94 da mediana), então some fácil num --limit ou
# --outcome apertado sem que a acurácia agregada avise.
CATEGORIAS_CRITICAS = ("renovate", "fly")


def check_dependencies():
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


def _seed_negativos(sample_id):
    """Seed derivada do id da amostra, não da posição na lista: o negativo
    daquela amostra sai igual em qualquer execução, que é o que permite
    guardar ele no cache."""

    digest = hashlib.sha1(str(sample_id).encode("utf-8")).hexdigest()

    return int(digest[:8], 16)


def box_sizes(registros):
    """Tamanhos reais das caixas, para negativo não ser distinguível do alvo por dimensão."""

    return [
        (c["width"], c["height"])
        for r in registros
        for c in (r.get("boxes") or [])
        if c.get("width") and c.get("height")
    ]


def extract_category_rows(
    registros,
    negativos_por_frame,
    tamanhos,
    fase="extraindo features",
):
    """(X, y, donos, info) — donos[i] é o id da amostra que gerou a linha i.

    É o único ponto que decodifica imagem; com o cache formado ele só roda
    para amostra nova."""

    import cv2
    import numpy as np
    from dataset_io import box_labels, sample_negative_boxes
    from features import FEATURE_SIZE, box_features

    linhas = []
    rotulos = []
    donos = []

    ilegiveis = 0
    vazios = 0
    total = len(registros)

    for indice, registro in enumerate(registros, start=1):

        dono = str(
            registro.get("id") or registro["_path"].stem
        )

        imagem = cv2.imread(str(registro["_path"]))

        if imagem is None:
            ilegiveis += 1
            progress(indice, total, fase)
            continue

        for caixa, categoria, _agiu in box_labels(registro):
            vetor = box_features(imagem, caixa)
            if vetor is None:
                vazios += 1
                continue
            linhas.append(vetor)
            rotulos.append(categoria)
            donos.append(dono)

        if negativos_por_frame > 0:
            for caixa in sample_negative_boxes(
                registro,
                negativos_por_frame,
                tamanhos,
                seed=_seed_negativos(dono),
            ):
                vetor = box_features(imagem, caixa)
                if vetor is None:
                    vazios += 1
                    continue
                linhas.append(vetor)
                rotulos.append("background")
                donos.append(dono)

        if indice % 25 == 0 or indice == total:
            progress(indice, total, fase)

    X = (
        np.asarray(linhas, dtype=np.float32)
        if linhas
        else np.empty((0, FEATURE_SIZE), dtype=np.float32)
    )

    assert X.shape[1] == FEATURE_SIZE, (X.shape[1], FEATURE_SIZE)

    return (
        X,
        np.asarray(rotulos, dtype="<U32"),
        np.asarray(donos, dtype="<U40"),
        {"ilegiveis": ilegiveis, "recortes_vazios": vazios},
    )


def category_dataset(
    registros,
    negativos_por_frame,
    cache_root,
    usar_cache=True,
):
    """(X, y, donos, info) de todas as resoluções, usando o cache por resolução.

    Cada resolução tem cache próprio: só as amostras que ainda não estão lá
    são extraídas, e o resultado vira um chunk novo. O cache é um superconjunto
    (guarda o que já foi visto), então no fim as linhas são filtradas para as
    amostras realmente pedidas nesta execução."""

    import numpy as np
    from feature_cache import FeatureCache, signature
    from features import FEATURE_SIZE, HSV_BINS, PATCH_MARGIN, PATCH_SIZE

    tamanhos = box_sizes(registros)

    assinatura = signature(
        "category",
        negativos_por_frame,
        FEATURE_SIZE,
        PATCH_SIZE,
        PATCH_MARGIN,
        HSV_BINS,
    )

    por_shard = defaultdict(list)

    for registro in registros:
        por_shard[registro.get("_shard") or "."].append(registro)

    # Shards que só existem no cache (amostras brutas já apagadas do disco)
    # continuam entrando no treino — é isso que faz o conhecimento acumular
    # em vez de se perder quando o dataset bruto é limpo.
    cache_kind_root = Path(cache_root) / "category"

    if usar_cache and cache_kind_root.exists():
        for pasta in cache_kind_root.iterdir():
            if pasta.is_dir():
                por_shard.setdefault(pasta.name, [])

    partes = []

    info = {
        "ilegiveis": 0,
        "recortes_vazios": 0,
        "extraidas": 0,
        "reaproveitadas": 0,
        "invalidados": [],
    }

    for shard, lote in sorted(por_shard.items()):

        if not usar_cache:

            X, y, donos, extra = extract_category_rows(
                lote,
                negativos_por_frame,
                tamanhos,
                f"extraindo {shard}",
            )

            info["ilegiveis"] += extra["ilegiveis"]
            info["recortes_vazios"] += extra["recortes_vazios"]
            info["extraidas"] += len(lote)

            partes.append((X, y, donos))

            continue

        cache = FeatureCache(cache_root, "category", shard, assinatura)

        # Assinatura diferente = vetor guardado não é mais comparável.
        if not cache.valido():
            cache.descartar()
            info["invalidados"].append(shard)

        conhecidos = cache.ids()

        novos = [
            r
            for r in lote
            if str(r.get("id") or r["_path"].stem) not in conhecidos
        ]

        if novos:

            X, y, donos, extra = extract_category_rows(
                novos,
                negativos_por_frame,
                tamanhos,
                f"extraindo {shard} ({len(novos)} novas)",
            )

            info["ilegiveis"] += extra["ilegiveis"]
            info["recortes_vazios"] += extra["recortes_vazios"]
            info["extraidas"] += len(novos)

            cache.append(X, y, donos)

        # Sem filtro por id: o cache é a memória acumulada, não só o que o
        # dataset bruto ainda tem no disco agora.
        X, y, donos = cache.load()

        info["reaproveitadas"] += len(donos) - len(novos)

        partes.append((X, y, donos))

    if not partes:
        raise SystemExit("Nenhuma amostra para montar o dataset.")

    X = np.concatenate([p[0] for p in partes])
    y = np.concatenate([p[1] for p in partes])
    donos = np.concatenate([p[2] for p in partes])

    if len(X) == 0:
        raise SystemExit(
            "Nenhum recorte válido. Imagens ilegíveis ou caixas com dimensão zero."
        )

    return X, y, donos, info


def build_action_dataset(registros):
    # comparação: tela inteira -> ação (não recomendado, alvo fica pequeno demais)
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


def build_classifier(trees, seed, jobs):
    from sklearn.ensemble import RandomForestClassifier
    # balanced_subsample: classes raras não são ignoradas
    # min_samples_leaf=2: evita overfitting em ruído JPG
    return RandomForestClassifier(
        n_estimators=trees,
        random_state=seed,
        n_jobs=jobs,
        class_weight="balanced_subsample",
        min_samples_leaf=2,
    )


def report(clf, X_teste, y_teste, y_treino, referencias, kind="category"):
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
    )

    from dataset_io import majority_baseline
    from collections import Counter

    previsto = clf.predict(X_teste)

    acuracia = accuracy_score(y_teste, previsto)

    base, classe_base = majority_baseline(list(y_teste))

    treino_dist = Counter(str(v) for v in y_treino)
    teste_dist = Counter(str(v) for v in y_teste)

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
        print("  !! Modelo não bate o chute na classe majoritária.")
        print("     Formulação ou features erradas.")
        print()

    print("-" * 62)
    print("  POR CLASSE")
    print("-" * 62)
    print()

    print(classification_report(y_teste, previsto, zero_division=0, digits=3))

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

    # pares de erro: diz se é confusão específica (resolvível) ou ruído geral
    erros = Counter()

    for verdade, palpite in zip(y_teste, previsto):

        if verdade != palpite:
            erros[(str(verdade), str(palpite))] += 1

    # só faz sentido em category: em action os rótulos já são ações
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

    _check_distribution_divergence(treino_dist, teste_dist)

    return acuracia


def _check_distribution_divergence(treino_dist, teste_dist):
    print("-" * 62)
    print("  VERIFICAÇÃO: DISTRIBUIÇÃO TREINO vs TESTE")
    print("-" * 62)
    print()

    todas_classes = set(treino_dist.keys()) | set(teste_dist.keys())

    desvios = []

    for classe in sorted(todas_classes):
        treino_pct = (treino_dist.get(classe, 0) / sum(treino_dist.values())) * 100
        teste_pct = (teste_dist.get(classe, 0) / sum(teste_dist.values())) * 100

        desvio = abs(treino_pct - teste_pct)
        desvios.append((desvio, classe, treino_pct, teste_pct))

    desvios.sort(reverse=True)

    problemas = []

    for desvio, classe, treino_pct, teste_pct in desvios[:5]:

        if desvio > 10:
            problemas.append(
                f"  {classe:<16} treino {treino_pct:5.1f}% | "
                f"teste {teste_pct:5.1f}%  (desvio {desvio:.1f}%)"
            )

    if problemas:
        print("  maiores divergências:")
        for p in problemas:
            print(p)
        print()
        print("  ⚠ Split desigual: o modelo foi treinado numa "
              "distribuição diferente da que vai testar.")
    else:
        print("  ✓ distribuições balanceadas entre treino e teste")

    print()


def _report_criticas(y_teste, y_treino, previsto):
    # recall 0% em `fly` custa ~0.1% de acurácia agregada — invisível no
    # número, fatal no bot; por isso reportado nominalmente aqui
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


def train(args):

    import random

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

    print(
        "resolucoes ...... "
        + ", ".join(
            f"{nome} ({quantas})"
            for nome, quantas in sorted(aviso["raizes"].items())
        )
    )

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
            "Rode o bot mais tempo com DATASET_SAVE ligado."
        )

    referencias = {
        "só o estado da máquina": state_baseline(registros),
        "estado + categorias (teto)":
            ceiling_state_categories(registros),
    }

    print()
    print("referências deste dataset:")

    for nome, valor in referencias.items():
        print(f"  {nome:<30} {valor:7.2%}")

    random.seed(args.seed)

    # Split por GRUPO, não por amostra: 56% das amostras repetem phash, e
    # dividir por índice deixa o mesmo quadro dos dois lados — a acurácia sai
    # inflada por decorar, não por generalizar.
    treino, teste, info = split_groups(
        registros,
        train_ratio=1.0 - args.test_ratio,
        mode=args.split,
        seed=args.seed,
    )

    info["test_ratio"] = args.test_ratio

    print()
    print(f"split por {args.split} (nenhum grupo nos dois lados):")
    print(f"  {info['grupos_treino']} grupos / {len(treino)} amostras de treino")
    print(f"  {info['grupos_teste']} grupos / {len(teste)} amostras de teste")
    print()

    if not teste:

        raise SystemExit(
            "Conjunto de teste vazio."
        )

    inicio = time.monotonic()

    if args.kind == "category":

        import numpy as np

        cache_root = (
            Path(args.cache_root).expanduser().resolve()
            if args.cache_root
            else raiz / "cache"
        )

        print(
            f"features ........ {'cache em ' + str(cache_root) if not args.no_cache else 'sem cache'}"
        )

        X, y, donos, extra = category_dataset(
            registros,
            args.negatives,
            cache_root,
            usar_cache=not args.no_cache,
        )

        if extra["invalidados"]:

            print(
                f"  cache invalidado (features mudaram): "
                f"{', '.join(extra['invalidados'])}"
            )

        print(
            f"  {extra['extraidas']} amostra(s) extraída(s), "
            f"{extra['reaproveitadas']} reaproveitada(s) do cache"
        )

        ids_teste = {
            str(r.get("id") or r["_path"].stem)
            for r in teste
        }

        mascara_teste = np.isin(donos, list(ids_teste))

        X_treino, y_treino = X[~mascara_teste], y[~mascara_teste]
        X_teste, y_teste = X[mascara_teste], y[mascara_teste]

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

    if args.kind == "category":
        background_count = sum(1 for y in y_treino if str(y) == "background")
        total_treino = len(y_treino)
        background_pct = (background_count / total_treino * 100) if total_treino > 0 else 0

        if args.negatives == 0:
            print()
            print("  ⚠ AVISO: --negatives=0")
            print("    Sem recortes de fundo, o modelo pode detectar qualquer")
            print("    pixel que não for do alvo. Recomendado: --negatives=2+")
        elif background_pct < 15:
            print()
            print(f"  ⚠ AVISO: background apenas {background_pct:.1f}% do treino")
            print("    Muito poucos negativos. Aumentar --negatives?")
        else:
            print()
            print(f"  ✓ background {background_pct:.1f}% do treino (saudável)")

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

    print()
    print("verificação de categorias críticas:")

    for categoria in CATEGORIAS_CRITICAS:
        qtd = contagem.get(categoria, 0)
        pct = (qtd / sum(contagem.values())) * 100 if sum(contagem.values()) > 0 else 0

        if qtd == 0:
            print(f"  ⚠ {categoria:<14} 0 exemplos!      <- coleta urgente")
        elif qtd < 50:
            print(f"  ⚠ {categoria:<14} {qtd:5d} ({pct:5.2f}%)  <- coleta recomendada")
        else:
            print(f"  ✓ {categoria:<14} {qtd:5d} ({pct:5.2f}%)")

    print()
    print(f"treinando ({args.trees} árvores)...")

    inicio = time.monotonic()

    clf = build_classifier(args.trees, args.seed, args.jobs)

    clf.fit(X_treino, y_treino)

    print(f"  {time.monotonic() - inicio:.0f}s")

    acuracia = report(
        clf, X_teste, y_teste, y_treino, referencias, args.kind
    )

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

    # registra como as features foram geradas: bot_ai confere isso no load
    # para não usar um modelo antigo com PATCH_SIZE diferente
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

    _recommendations(acuracia, referencias, contagem, background_pct)

    return 0


def _recommendations(acuracia, referencias, contagem, background_pct):
    print("=" * 62)
    print("  RECOMENDAÇÕES PARA A PRÓXIMA COLETA")
    print("=" * 62)
    print()

    teto = referencias.get("estado + categorias (teto)", 0.96)
    espaco = teto - acuracia

    if acuracia >= teto * 0.95:
        print("  ✓ Modelo alcança o teto teórico. Está bom.")
    elif espaco > 0.10:
        print(f"  ℹ Ainda há {espaco*100:.1f}% de margem até o teto.")
        print()
        print("  Prioridades:")

        total = sum(contagem.values())
        raras = [
            (c, q, q/total*100)
            for c, q in contagem.items()
            if c != "background" and q/total*100 < 5
        ]

        if raras:
            print()
            print("  1. Categorias raras (<5%):")
            for cat, qtd, pct in sorted(raras, key=lambda x: x[2]):
                print(f"     - {cat:<14} {qtd:6d} ({pct:5.2f}%)")
            print("     Coletar mais dessas, especialmente as com <1%")

        if background_pct < 20:
            print()
            print("  2. Aumentar negativos (background):")
            print(f"     Hoje {background_pct:.1f}% | Recomendado >20%")
            print("     Retreine com --negatives=3 ou --negatives=4")

    print()


def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description=(
            "Treina o modelo de visão juntando todas as "
            "resoluções em dataset/data/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--kind",
        choices=("category", "action"),
        default="category",
        help="category: recorte -> categoria | action: tela -> ação (comparação)",
    )

    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_DATASET),
        help="pasta do dataset (padrão: dataset/) — todas as resoluções dentro dela",
    )

    parser.add_argument(
        "--split",
        choices=("phash", "session"),
        default="phash",
        help=(
            "agrupamento do split: phash (quadros iguais no mesmo lado) "
            "ou session (partida inteira num lado)"
        ),
    )

    parser.add_argument(
        "--cache-root",
        default=None,
        help="pasta do cache de features (padrão: <dataset>/cache)",
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="extrai tudo do zero, sem ler nem gravar o cache",
    )

    parser.add_argument(
        "--model-out",
        default=str(DEFAULT_MODEL),
        help="onde gravar o modelo",
    )

    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.2,
        help="fração do dataset para teste (padrão: 0.2)",
    )

    parser.add_argument(
        "--outcome",
        nargs="*",
        default=None,
        metavar="RESULTADO",
        help="filtrar amostras: changed, unchanged, unknown, negative",
    )

    parser.add_argument(
        "--negatives",
        type=int,
        default=2,
        help="recortes de fundo por frame (padrão: 2)",
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
