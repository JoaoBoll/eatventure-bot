#!/usr/bin/env python3
"""Treino manual ou por lote, com replay, validação e publicação versionada."""

import argparse
import hashlib
import json
import re
import sys
import time
from copy import copy
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


def _parse_resolution(value):
    if value == "legacy" or re.fullmatch(r"[1-9]\d*x[1-9]\d*", value):
        return value
    raise argparse.ArgumentTypeError(
        "Informe a resolução como LARGURAxALTURA ou use legacy."
    )


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

    if not sys.stdout.isatty():
        if feito == total or feito % 1000 == 0:
            print(f"{fase}: {feito}/{total}", flush=True)
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
        np.asarray(donos, dtype=str),
        {"ilegiveis": ilegiveis, "recortes_vazios": vazios},
    )


def category_dataset(
    registros,
    negativos_por_frame,
    cache_root,
    usar_cache=True,
    metadata_records=None,
):
    """Junta as features novas e a memória, preservando metadados de cada frame."""

    import numpy as np
    from feature_cache import FeatureCache, signature
    from features import FEATURE_SIZE, HSV_BINS, PATCH_MARGIN, PATCH_SIZE
    from training_run import compact_record, record_id, record_key

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
    disponiveis = defaultdict(dict)
    for registro in metadata_records if metadata_records is not None else registros:
        disponiveis[registro.get("_shard") or "."][record_id(registro)] = compact_record(registro)
    for registro in registros:
        por_shard[registro.get("_shard") or "."].append(registro)

    cache_kind_root = Path(cache_root) / "category"
    if usar_cache and cache_kind_root.exists():
        if any(cache_kind_root.glob("*.npz")):
            por_shard.setdefault(".", [])
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
        "records": {},
        "caches": {},
    }
    planos = []
    for shard, lote in sorted(por_shard.items()):
        cache = FeatureCache(cache_root, "category", shard, assinatura) if usar_cache else None
        conhecidos = set()
        metadados = {record_id(r): compact_record(r) for r in lote}
        if cache is not None:
            if not cache.valido():
                raise RuntimeError(
                    f"Cache incompatível preservado em {cache.dir}. "
                    "Restaure as configurações originais ou escolha outro --cache-root."
                )
            conhecidos = cache.ids()
            metadados.update(cache.ensure_metadata(disponiveis[shard]))
            info["caches"][shard] = cache
            for metadata in metadados.values():
                tamanhos.extend(tuple(size) for size in metadata.get("box_sizes", []))
        planos.append((shard, lote, cache, conhecidos, metadados))

    for shard, lote, cache, conhecidos, metadados in planos:
        novos = [r for r in lote if record_id(r) not in conhecidos]
        if novos:
            X, y, donos, extra = extract_category_rows(
                novos,
                negativos_por_frame,
                tamanhos,
                f"extraindo {shard} ({len(novos)} novas)",
            )

            info["ilegiveis"] += extra["ilegiveis"]
            info["recortes_vazios"] += extra["recortes_vazios"]
            info["extraidas"] += len(set(donos.tolist()))
            if cache is not None:
                cache.append(X, y, donos, records=metadados)
        elif cache is None:
            continue

        if cache is not None:
            X, y, donos = cache.load()
            metadados = cache.ensure_metadata(disponiveis[shard])
            info["reaproveitadas"] += len(conhecidos)
        utilizados = set(str(dono) for dono in donos)
        for dono in utilizados:
            metadata = metadados[dono]
            info["records"][record_key(metadata)] = metadata
        chaves = np.asarray([f"{shard}/{dono}" for dono in donos], dtype=str)
        partes.append((X, y, chaves))

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
    from training_run import record_key

    linhas = []
    rotulos = []
    donos = []
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
                donos.append(record_key(registro))

        if indice % 25 == 0 or indice == total:
            progress(indice, total, "lendo imagens")

    if not linhas:
        raise SystemExit("Nenhuma imagem legível.")

    X = np.asarray(linhas, dtype=np.float32)
    assert X.shape[1] == SCREEN_FEATURE_SIZE
    return X, np.asarray(rotulos), {"ilegiveis": ilegiveis, "ids": donos}


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


def _train_one_resolution(args):
    from dataset.training_files import snapshot_records, training_lock
    from dataset.layout import record_resolution
    from dataset_io import filter_by_outcome
    from training_run import record_key, trained_sample_ids

    if args.model_out is None:
        args.model_out = (
            ROOT / "IA" / "models" / args.resolution / "category.joblib"
            if args.resolution is not None
            else DEFAULT_MODEL
        )
    raiz = Path(args.dataset_root).expanduser().resolve()
    automatico = getattr(args, "automatic", False)
    try:
        with training_lock(raiz):
            snapshot, aviso = snapshot_records(raiz)
            registros = filter_by_outcome(snapshot, args.outcome)
            if args.resolution is not None:
                registros = [
                    record for record in registros
                    if record_resolution(record) == args.resolution
                ]
            registros = list({record_key(r): r for r in registros}.values())
            if automatico:
                treinados = trained_sample_ids(raiz, args.cache_root)
                registros = [r for r in registros if record_key(r) not in treinados]
                minimo = getattr(args, "min_samples", 10000)
                if len(registros) < minimo:
                    print(f"Treino automático aguardando: {len(registros)}/{minimo} amostras novas.")
                    return 0
                registros = registros[:getattr(args, "batch_size", 10000)]
            if args.limit:
                registros = registros[:args.limit]
            return _train_snapshot(args, raiz, registros, snapshot, aviso)
    except BlockingIOError:
        print("Já existe um treino ativo para este dataset.")
        return 0 if automatico else 1
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        return _failed_training(args, error)


def train(args):
    if args.resolution is not None or args.combine_resolutions:
        return _train_one_resolution(args)

    if args.model_out is not None:
        raise ValueError("Use --resolution ou --combine-resolutions junto com --model-out.")

    from dataset.layout import record_resolution
    from dataset.training_files import snapshot_records

    records, _ = snapshot_records(args.dataset_root)
    resolutions = sorted({record_resolution(record) for record in records})
    if not resolutions:
        return 0 if args.automatic else _train_one_resolution(args)

    result = 0
    for resolution in resolutions:
        resolution_args = copy(args)
        resolution_args.resolution = resolution
        resolution_args.model_out = (
            ROOT / "IA" / "models" / resolution / "category.joblib"
        )
        status = _train_one_resolution(resolution_args)
        if status and not result:
            result = status
    return result


def _failed_training(args, error, execucao=None):
    from training_run import TrainingRun

    execucao = execucao or TrainingRun(ROOT / "IA", args)
    execucao.data["error"] = {
        "type": type(error).__name__,
        "message": str(error),
    }
    execucao.finish("interrupted" if isinstance(error, KeyboardInterrupt) else "failed")
    print(f"Treino não concluído: {error}")
    print(f"Relatório: {execucao.path}")
    return 130 if isinstance(error, KeyboardInterrupt) else 1


def _train_snapshot(args, raiz, registros, snapshot, aviso):
    from training_run import TrainingRun

    execucao = TrainingRun(ROOT / "IA", args)
    try:
        execucao.save()
        return _fit_snapshot(args, raiz, registros, snapshot, aviso, execucao)
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        return _failed_training(args, error, execucao)


def _fit_snapshot(args, raiz, registros, snapshot, aviso, execucao):
    check_dependencies()

    import numpy as np
    from sklearn.metrics import classification_report

    from dataset.training_files import cleanup_records
    from dataset_io import ceiling_state_categories, split_groups, state_baseline
    from training_run import (
        activate_model, compact_record, publish_model,
        record_id, record_key, summarize,
    )

    automatico = getattr(args, "automatic", False)
    compativel_limpeza = args.kind == "category" and not args.no_cache
    pedido_limpeza = getattr(args, "cleanup", None)
    if pedido_limpeza is True and not compativel_limpeza:
        raise ValueError("--cleanup exige --kind category e cache habilitado.")
    if automatico and (not compativel_limpeza or pedido_limpeza is False):
        raise ValueError("O treino automático exige categorias, cache e limpeza habilitados.")
    if automatico and args.limit is not None:
        raise ValueError("Use --batch-size para limitar lotes automáticos; --limit é apenas manual.")
    limpar = compativel_limpeza if pedido_limpeza is None else pedido_limpeza
    if getattr(args, "min_samples", 10000) <= 0 or getattr(args, "batch_size", 10000) <= 0:
        raise ValueError("--min-samples e --batch-size devem ser positivos.")
    if args.kind == "action" and Path(args.model_out).resolve() == DEFAULT_MODEL.resolve():
        raise ValueError("--kind action exige --model-out alternativo para preservar o modelo do bot.")

    selecionadas = {record_key(r) for r in registros}
    execucao.data["dataset_root"] = str(raiz)
    execucao.data["input_warnings"] = aviso
    execucao.data["current_batch"] = {
        **summarize(registros),
        "available_frames": len(snapshot),
        "sample_keys": sorted(selecionadas),
    }
    execucao.data["cleanup"] = {
        "requested": bool(limpar),
        "status": "pending" if limpar else "disabled",
    }
    execucao.save()

    cache_root = Path(args.cache_root).expanduser().resolve() if args.cache_root else raiz / "cache"
    if args.kind == "category":
        X, y, donos, extra = category_dataset(
            registros, args.negatives, cache_root,
            usar_cache=not args.no_cache, metadata_records=snapshot,
        )
        metadados = extra["records"]
        caches = extra["caches"]
    else:
        X, y, extra = build_action_dataset(registros)
        donos = np.asarray(extra["ids"], dtype=str)
        utilizados = set(donos.tolist())
        metadados = {
            record_key(r): compact_record(r)
            for r in registros if record_key(r) in utilizados
        }
        caches = {}

    efetivas = set(donos.tolist())
    atuais = efetivas & selecionadas
    replay = efetivas - selecionadas
    execucao.data["current_batch"].update({
        "used_frames": len(atuais),
        "unused_frames": len(selecionadas - atuais),
        "used_rows": int(np.isin(donos, list(atuais)).sum()),
        "used_summary": summarize(metadados[k] for k in sorted(atuais)),
    })
    execucao.data["replay"] = {
        **summarize(metadados[k] for k in sorted(replay)),
        "rows": int(np.isin(donos, list(replay)).sum()),
    }
    execucao.data["extraction"] = {
        key: extra.get(key, 0)
        for key in ("ilegiveis", "recortes_vazios", "extraidas", "reaproveitadas")
    }
    execucao.save()
    if len(efetivas) < 20:
        raise ValueError(f"Amostras efetivamente utilizáveis insuficientes: {len(efetivas)} (mínimo 20).")

    todos_registros = [metadados[k] for k in sorted(efetivas)]
    treino, teste, divisao = split_groups(
        todos_registros, train_ratio=1.0 - args.test_ratio,
        mode=args.split, seed=args.seed,
    )
    ids_treino = {record_key(r) for r in treino}
    ids_teste = {record_key(r) for r in teste}
    mascara_teste = np.isin(donos, list(ids_teste))
    X_treino, y_treino = X[~mascara_teste], y[~mascara_teste]
    X_teste, y_teste = X[mascara_teste], y[mascara_teste]
    if not len(X_treino) or not len(X_teste):
        raise ValueError("O split por grupo precisa de conjuntos de treino e validação não vazios.")
    if len(set(y_treino.tolist())) < 2:
        raise ValueError("O conjunto de treino contém menos de duas categorias.")

    baseline_records = [
        {**r, "boxes": [{"category": category} for category in r["categories"]]}
        for r in todos_registros
    ]
    referencias = {
        "só o estado da máquina": state_baseline(baseline_records),
        "estado + categorias (teto)": ceiling_state_categories(baseline_records),
    }
    print(f"Avaliação: {len(ids_treino)} frames/{len(X_treino)} recortes no treino, "
          f"{len(ids_teste)} frames/{len(X_teste)} recortes na validação.")
    avaliador = build_classifier(args.trees, args.seed, args.jobs)
    avaliador.fit(X_treino, y_treino)
    acuracia = report(avaliador, X_teste, y_teste, y_treino, referencias, args.kind)
    execucao.data["evaluation"] = {
        "accuracy": float(acuracia),
        "row_unit": "box_crop" if args.kind == "category" else "full_frame",
        "split": {**divisao, "test_ratio": args.test_ratio},
        "training": {**summarize(treino), "rows": int(len(X_treino))},
        "validation": {**summarize(teste), "rows": int(len(X_teste))},
        "class_metrics": classification_report(
            y_teste, avaliador.predict(X_teste), output_dict=True, zero_division=0,
        ),
        "baselines": {name: float(value) for name, value in referencias.items()},
        "scope": "held_out_groups_before_final_refit",
    }
    execucao.save()

    print(f"Ajuste final: {len(efetivas)} frames/{len(X)} recortes, incluindo validação e replay.")
    modelo = build_classifier(args.trees, args.seed, args.jobs)
    modelo.fit(X, y)
    execucao.data["final_fit"] = {
        **summarize(todos_registros),
        "rows": int(len(X)),
        "row_unit": "box_crop" if args.kind == "category" else "full_frame",
        "current_batch_frames": len(atuais),
        "replay_frames": len(replay),
        "classes": dict(sorted(Counter(str(label) for label in y).items())),
        "includes_validation": True,
    }

    from features import HSV_BINS, PATCH_MARGIN, PATCH_SIZE, SCREEN_HEIGHT, SCREEN_WIDTH
    meta = {
        "run_id": execucao.id,
        "kind": args.kind,
        "labels": [str(label) for label in modelo.classes_],
        "counts": execucao.data["final_fit"]["classes"],
        "accuracy": float(acuracia),
        "accuracy_scope": "validation_before_final_refit",
        "baselines": execucao.data["evaluation"]["baselines"],
        "split": execucao.data["evaluation"]["split"],
        "outcome_filter": args.outcome,
        "features": {
            "patch_size": PATCH_SIZE, "patch_margin": PATCH_MARGIN,
            "hsv_bins": HSV_BINS, "screen_width": SCREEN_WIDTH,
            "screen_height": SCREEN_HEIGHT, "n_features": int(X.shape[1]),
        },
        "trees": args.trees, "seed": args.seed,
        "final_fit": execucao.data["final_fit"],
    }
    execucao.data["artifacts"] = publish_model(
        execucao, modelo, meta, args.model_out,
    )
    execucao.data["status"] = "publishing"
    execucao.save()
    activate_model(execucao)
    execucao.data["model_activated"] = True
    execucao.finish("success")

    try:
        if limpar:
            fonte_limpeza = snapshot if not args.outcome and not args.limit else registros
            consumiveis = [r for r in fonte_limpeza if record_key(r) in efetivas]
            consumidos = {record_id(r) for r in consumiveis}
            resultado = cleanup_records(raiz, consumiveis, consumidos)
            execucao.data["cleanup"] = {
                **resultado, "requested": True,
                "eligible_snapshot_frames": len(consumiveis),
                "replay_raw_frames": sum(record_key(r) in replay for r in consumiveis),
            }
            execucao.data["cleanup"].setdefault(
                "status", "partial" if resultado.get("errors") else "completed"
            )
        for shard, cache in caches.items():
            cache.mark_trained(
                metadata["id"] for metadata in metadados.values()
                if metadata["_shard"] == shard
            )
    except Exception as error:
        execucao.data["cleanup"] = {
            **execucao.data["cleanup"], "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
        print(f"Modelo publicado; limpeza/registro do lote não concluído: {error}")
    execucao.finish("success")
    print(f"Modelo: {execucao.data['artifacts']['model']}")
    print(f"Relatório: {execucao.path}")
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
            "Treina modelos independentes por resolução; "
            "use --combine-resolutions para juntá-las."
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
        "--automatic", action="store_true",
        help="treina apenas quando há amostras novas suficientes, em lotes",
    )
    parser.add_argument(
        "--resolution",
        type=_parse_resolution,
        default=None,
        help="treina somente os registros desta resolução (ex.: 1080x2400)",
    )
    parser.add_argument(
        "--combine-resolutions",
        action="store_true",
        help="opta por treinar registros de todas as resoluções em um único modelo",
    )
    parser.add_argument(
        "--min-samples", type=int, default=10000,
        help="mínimo de frames novos para iniciar treino automático (padrão: 10000)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=10000,
        help="máximo de frames novos por lote automático (padrão: 10000)",
    )
    limpeza = parser.add_mutually_exclusive_group()
    limpeza.add_argument(
        "--cleanup", dest="cleanup", action="store_true", default=None,
        help="limpa os índices/imagens consumidos após sucesso (padrão com categorias e cache)",
    )
    limpeza.add_argument(
        "--keep-data", dest="cleanup", action="store_false", default=None,
        help="preserva índices e imagens após o treino manual",
    )

    parser.add_argument(
        "--model-out",
        default=None,
        help="nome de referência do modelo; publica versão em models/ e ponteiro .current.json",
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

    args = parser.parse_args(argv)
    if args.resolution is not None and args.combine_resolutions:
        parser.error("--resolution e --combine-resolutions são incompatíveis.")
    if args.model_out is not None and args.resolution is None and not args.combine_resolutions:
        parser.error("--model-out exige --resolution ou --combine-resolutions.")
    return args


def main(argv=None):

    args = parse_args(argv)

    return train(args)


if __name__ == "__main__":

    raise SystemExit(main())
