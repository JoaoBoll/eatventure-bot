#!/usr/bin/env python3
"""Treino manual ou por lote, com replay, validação e publicação versionada."""

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from copy import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for candidato in (str(ROOT), str(ROOT / "src"), str(ROOT / "IA")):
    if candidato not in sys.path:
        sys.path.insert(0, candidato)
from core.cli import ArgumentParser, configure_locale
from core.i18n import t

DEFAULT_DATASET = ROOT / "dataset"
DEFAULT_MODEL = ROOT / "IA" / "model.joblib"

# renovate/fly: sem elas o bot fica preso na tela de reforma (RENOVATE_RULES
# em core/state_machine.py). `fly` tem poucas caixas e a maior forma do
# dataset (414x141 vs 95x94 da mediana), então some fácil num --limit ou
# --outcome apertado sem que a acurácia agregada avise.



def _parse_resolution(value):
    if value == "legacy" or re.fullmatch(r"[1-9]\d*x[1-9]\d*", value):
        return value
    raise argparse.ArgumentTypeError(
        t("messages.invalid_resolution")
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
            t("messages.missing_training_dependencies", value_1=sys.executable, value_2=' '.join(faltando))
        )


def progress(feito, total, fase):

    if total <= 0:
        return

    if not sys.stdout.isatty():
        if feito == total or feito % 1000 == 0:
            print(t("messages.training_progress", fase=fase, feito=feito, total=total), flush=True)
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
    label_source="all",
    fase=t("messages.features_phase"),
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

        caixas = box_labels(registro)
        if label_source == "confirmed":
            # A coleta nasce do matcher de templates. Só o alvo que sumiu após
            # o clique tem um sinal adicional de que aquela caixa era útil;
            # as demais detecções ainda podem ser falsos positivos do matcher.
            caixas = [
                (caixa, categoria, agiu)
                for caixa, categoria, agiu in caixas
                if registro.get("outcome") == "changed" and agiu
            ]
        for caixa, categoria, _agiu in caixas:
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
    label_source="all",
    replay_rows=12000,
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
        label_source,
    )

    por_shard = defaultdict(list)
    disponiveis = defaultdict(dict)
    for registro in metadata_records if metadata_records is not None else registros:
        disponiveis[registro.get("_shard") or "."][record_id(registro)] = compact_record(registro)
    for registro in registros:
        por_shard[registro.get("_shard") or "."].append(registro)

    cache_kind_root = Path(cache_root) / "category"
    bounded = usar_cache and label_source == "confirmed"
    if usar_cache and not bounded and cache_kind_root.exists() and not registros:
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
        if bounded:
            from feature_cache import BoundedReplay

            cache = BoundedReplay(cache_root, shard, assinatura, max_rows=replay_rows)
            old_X, old_y, old_ids = cache.load()
            metadata = cache.metadata()
            incoming = {record_id(r): compact_record(r) for r in lote}
            # Reprocessar um frame selecionado após uma falha não duplica suas linhas.
            keep = ~np.isin(old_ids, list(incoming))
            reused = set(old_ids[keep].tolist())
            if reused:
                partes.append((old_X[keep], old_y[keep],
                               np.asarray([f"{shard}/{i}" for i in old_ids[keep]])))
                info["records"].update({f"{shard}/{i}": metadata[i] for i in reused})
            info["reaproveitadas"] += len(reused)
            X, y, owners, counts = extract_category_rows(
                lote, negativos_por_frame, tamanhos, label_source=label_source,
            )
            used = set(owners.tolist())
            cache.append(X, y, owners, records=incoming)
            info["caches"][shard] = cache
            info["extraidas"] += len(used)
            info["ilegiveis"] += counts["ilegiveis"]
            info["recortes_vazios"] += counts["recortes_vazios"]
            info["records"].update({f"{shard}/{i}": incoming[i] for i in used})
            if used:
                partes.append((X, y, np.asarray([f"{shard}/{i}" for i in owners])))
            continue
        cache = FeatureCache(cache_root, "category", shard, assinatura) if usar_cache else None
        conhecidos = set()
        metadados = {record_id(r): compact_record(r) for r in lote}
        if cache is not None:
            if not cache.valido():
                raise RuntimeError(
                    t("messages.cache_incompatible_warning", value_1=cache.dir)
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
                label_source=label_source,
                fase=t("messages.shard_features_phase", shard=shard, count=len(novos)),
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
        raise SystemExit(t("messages.training_dataset_empty"))

    X = np.concatenate([p[0] for p in partes])
    y = np.concatenate([p[1] for p in partes])
    donos = np.concatenate([p[2] for p in partes])

    if len(X) == 0:
        raise SystemExit(
            t("messages.valid_crops_missing")
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
            progress(indice, total, t("messages.reading_images"))

    if not linhas:
        raise SystemExit(t("messages.readable_images_missing"))

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


def validation_metrics(clf, X_teste, y_teste):
    from sklearn.metrics import accuracy_score, classification_report, f1_score

    previsto = clf.predict(X_teste)
    metricas = classification_report(
        y_teste, previsto, output_dict=True, zero_division=0,
    )
    return {
        "accuracy": float(accuracy_score(y_teste, previsto)),
        "macro_f1": float(f1_score(y_teste, previsto, average="macro", zero_division=0)),
        "class_metrics": metricas,
    }


def promotion_decision(metricas, args):
    if getattr(args, "allow_unsafe_promotion", False):
        return True, [t("messages.forced_promotion")]

    min_macro_f1 = getattr(args, "min_macro_f1", 0.55)
    min_critical_recall = getattr(args, "min_critical_recall", 0.40)
    critical_categories = getattr(args, "critical_categories", ["renovate", "fly"])
    reasons = []
    if metricas["macro_f1"] < min_macro_f1:
        reasons.append(
            t("messages.low_macro_f1", score=metricas['macro_f1'], minimum=min_macro_f1)
        )
    classes = metricas["class_metrics"]
    critical_present = [
        category for category in critical_categories if category in classes
    ]
    low_recall = [
        category for category in critical_present
        if classes[category].get("recall", 0.0) < min_critical_recall
    ]
    if low_recall:
        reasons.append(
            t("messages.low_critical_recall", categories=", ".join(low_recall))
        )
    return not reasons, reasons


def _train_one_resolution(args):
    from dataset.training_files import snapshot_records, training_lock
    from dataset.layout import MIN_FRESH_TARGETS, automatic_minimum, confirmed_training_sample, record_resolution
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
            confirmed = args.kind == "category" and args.label_source == "confirmed"
            if confirmed:
                registros = [r for r in registros if confirmed_training_sample(r)]
            if args.resolution is not None:
                registros = [
                    record for record in registros
                    if record_resolution(record) == args.resolution
                ]
            registros = list({record_key(r): r for r in registros}.values())
            if automatico:
                treinados = (trained_sample_ids(raiz, args.cache_root, confirmed=True) if confirmed
                             else trained_sample_ids(raiz, args.cache_root))
                registros = [r for r in registros if record_key(r) not in treinados]
                minimo = getattr(args, "min_samples", 10000)
                if confirmed:
                    minimo = automatic_minimum(raiz, args.resolution, minimo)
                    if sum(r.get("label_evidence") == "target_changed" for r in registros) < MIN_FRESH_TARGETS:
                        print(t("messages.wait_fresh_holdout"))
                        return 0
                if len(registros) < minimo:
                    print(t("messages.training_waiting_samples", value_1=len(registros), minimo=minimo))
                    return 0
                batch_size = getattr(args, "batch_size", None)
                if batch_size is not None:
                    if confirmed:
                        registros.sort(key=lambda r: not bool(r.get("label_evidence")))
                    registros = registros[:batch_size]
            if args.limit:
                registros = registros[:args.limit]
            return _train_snapshot(args, raiz, registros, snapshot, aviso)
    except BlockingIOError:
        print(t("messages.training_already_active"))
        return 0 if automatico else 1
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        return _failed_training(args, error)


def train(args):
    if args.resolution is not None or args.combine_resolutions or args.model_out is not None:
        return _train_one_resolution(args)

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
    print(t("messages.training_failed", error=error))
    print(t("messages.training_report_path", value_1=execucao.path))
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
    from dataset.training_files import cleanup_records
    from dataset_io import (
        continuous_split, capture_time,
        ceiling_state_categories_on_holdout,
        split_groups,
        state_baseline_on_holdout,
    )
    from training_run import (
        activate_model, compact_record, prune_generations, publish_model,
        record_id, record_key, summarize,
    )

    automatico = getattr(args, "automatic", False)
    label_source = getattr(args, "label_source", "all")
    continuous = args.kind == "category" and label_source == "confirmed"
    compativel_limpeza = args.kind == "category" and not args.no_cache
    pedido_limpeza = getattr(args, "cleanup", None)
    if pedido_limpeza is True and not compativel_limpeza:
        raise ValueError(t("messages.cleanup_requirements"))
    if automatico and (not compativel_limpeza or pedido_limpeza is False):
        raise ValueError(t("messages.automatic_training_requirements"))
    if automatico and args.limit is not None:
        raise ValueError(t("messages.automatic_batch_limit_required"))
    limpar = compativel_limpeza if pedido_limpeza is None else pedido_limpeza
    batch_size = getattr(args, "batch_size", None)
    if (
        getattr(args, "min_samples", 10000) <= 0
        or (batch_size is not None and batch_size <= 0)
    ):
        raise ValueError(t("messages.invalid_training_batch"))
    if args.kind == "action" and Path(args.model_out).resolve() == DEFAULT_MODEL.resolve():
        raise ValueError(t("messages.action_model_output_required"))

    selecionadas = {record_key(r) for r in registros}
    execucao.data["dataset_root"] = str(raiz)
    execucao.data["input_warnings"] = aviso
    execucao.data["current_batch"] = {
        **summarize(registros),
        "available_frames": len(snapshot),
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
            label_source=label_source,
            replay_rows=getattr(args, "replay_rows", 12000),
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
        raise ValueError(t("messages.insufficient_usable_samples", value_1=len(efetivas)))

    todos_registros = [metadados[k] for k in sorted(efetivas)]
    incumbent = None
    prior_id = None
    if continuous:
        from model_updates import resolve_publication
        import joblib
        publication = resolve_publication(args.model_out)
        prior_id = publication.run_id
        cutoff = 0
        if prior_id or publication.model_path.exists():
            incumbent = joblib.load(publication.model_path)
            cutoff = getattr(incumbent, "training_cutoff_", publication.model_path.stat().st_mtime)
        treino, teste, divisao = continuous_split(todos_registros, atuais, cutoff, args.test_ratio, args.seed)
    else:
        treino, teste, divisao = split_groups(
            todos_registros, train_ratio=1.0 - args.test_ratio,
            mode=args.split, seed=args.seed,
        )
    ids_treino = {record_key(r) for r in treino}
    ids_teste = {record_key(r) for r in teste}
    mascara_teste = np.isin(donos, list(ids_teste))
    mascara_treino = np.isin(donos, list(ids_treino))
    X_treino, y_treino = X[mascara_treino], y[mascara_treino]
    X_teste, y_teste = X[mascara_teste], y[mascara_teste]
    if continuous and (len(ids_teste) < 2 or not any(y_teste != "background")
                       or len(set(y_treino.tolist())) < 2):
        execucao.data["reason"] = str(t("messages.wait_fresh_holdout"))
        execucao.finish("waiting")
        print(execucao.data["reason"])
        return 0
    if not len(X_treino) or not len(X_teste):
        raise ValueError(t("messages.empty_training_split"))
    if len(set(y_treino.tolist())) < 2:
        raise ValueError(t("messages.insufficient_categories"))

    print(t("messages.evaluation_summary", value_1=len(ids_treino), value_2=len(X_treino), value_3=len(ids_teste), value_4=len(X_teste)))
    avaliador = build_classifier(args.trees, args.seed, args.jobs)
    avaliador.fit(X_treino, y_treino)
    metricas = validation_metrics(avaliador, X_teste, y_teste)
    referencias = {}
    if args.kind == "action":
        baseline_train = [
            {**record, "boxes": [{"category": category} for category in record["categories"]]}
            for record in treino
        ]
        baseline_test = [
            {**record, "boxes": [{"category": category} for category in record["categories"]]}
            for record in teste
        ]
        referencias = {
            "só o estado da máquina": state_baseline_on_holdout(baseline_train, baseline_test),
            "estado + categorias (teto)": ceiling_state_categories_on_holdout(baseline_train, baseline_test),
        }
    print(
        t("messages.validation_summary", value_1=metricas['accuracy'], value_2=metricas['macro_f1'])
    )
    execucao.data["evaluation"] = {
        "accuracy": metricas["accuracy"],
        "macro_f1": metricas["macro_f1"],
        "row_unit": "box_crop" if args.kind == "category" else "full_frame",
        "split": {**divisao, "test_ratio": args.test_ratio},
        "training": {**summarize(treino), "rows": int(len(X_treino))},
        "validation": {**summarize(teste), "rows": int(len(X_teste))},
        "class_metrics": metricas["class_metrics"],
        "baselines": {name: float(value) for name, value in referencias.items()},
        "scope": "fresh_frames" if continuous else "held_out_groups_before_final_refit",
    }
    execucao.save()

    print(t("messages.final_fit_summary", value_1=len(efetivas), value_2=len(X)))
    if continuous:
        modelo = avaliador
        modelo.training_cutoff_ = max(capture_time(r) for r in todos_registros)
    else:
        modelo = build_classifier(args.trees, args.seed, args.jobs)
        modelo.fit(X, y)
    fitted_ids = ids_treino if continuous else efetivas
    classes = dict(sorted(Counter(str(label) for label in (y_treino if continuous else y)).items()))
    data_summary = summarize(treino if continuous else todos_registros)
    execucao.data["final_fit"] = {
        "resolutions": data_summary["resolutions"],
        "categories": data_summary["categories"],
        "images_used": len(fitted_ids),
        "new_images": len(fitted_ids & atuais),
        "reused_images": len(fitted_ids & replay),
        "category_count": (
            len(classes) - int("background" in classes)
            if args.kind == "category" else len(classes)
        ),
        "rows": int(len(X_treino) if continuous else len(X)),
        "row_unit": "box_crop" if args.kind == "category" else "full_frame",
        "class_count": len(classes),
        "classes": classes,
        "includes_validation": not continuous,
    }

    from features import HSV_BINS, PATCH_MARGIN, PATCH_SIZE, SCREEN_HEIGHT, SCREEN_WIDTH
    meta = {
        "run_id": execucao.id,
        "publication_target": Path(args.model_out).name,
        "previous_run": prior_id,
        "kind": args.kind,
        "labels": [str(label) for label in modelo.classes_],
        "counts": execucao.data["final_fit"]["classes"],
        "accuracy": metricas["accuracy"],
        "macro_f1": metricas["macro_f1"],
        "accuracy_scope": "fresh_frames" if continuous else "validation_before_final_refit",
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
    promoted, promotion_reasons = promotion_decision(metricas, args)
    comparison = None
    if incumbent is not None:
        comparison = validation_metrics(incumbent, X_teste, y_teste)
        lost_classes = set(incumbent.classes_) - set(modelo.classes_)
        def recall(metrics, category):
            return metrics["class_metrics"].get(category, {}).get("recall", 0.0)
        if (lost_classes or metricas["macro_f1"] < comparison["macro_f1"]
            or any(recall(metricas, c) < recall(comparison, c) for c in args.critical_categories)):
            promoted = False
            promotion_reasons.append(t("messages.incumbent_regression"))
    locator = None
    if continuous and promoted:
        from region_locator import attach_locator
        locator = attach_locator(
            modelo, [r for r in registros if record_key(r) in ids_treino],
            [r for r in registros if record_key(r) in ids_teste], incumbent=incumbent,
        )
        if getattr(incumbent, "region_locator_", None) is not None and getattr(modelo, "region_locator_", None) is None:
            promoted = False
            promotion_reasons.append(t("messages.locator_regression"))
    execucao.data["artifacts"] = publish_model(execucao, modelo, meta, args.model_out)
    execucao.data["status"] = "publishing"
    execucao.save()
    execucao.data["promotion"] = {
        "accepted": promoted,
        "min_macro_f1": getattr(args, "min_macro_f1", 0.55),
        "min_critical_recall": getattr(args, "min_critical_recall", 0.40),
        "reasons": promotion_reasons,
        "incumbent": comparison,
        "incumbent_id": prior_id,
        "locator": locator,
    }
    if promoted:
        activate_model(execucao)
    else:
        print(t("messages.model_candidate_saved") + "; ".join(promotion_reasons))
    execucao.data["model_activated"] = promoted
    execucao.finish("success")

    try:
        if limpar and (promoted or (continuous and automatico)):
            fonte_limpeza = snapshot if not continuous and not args.outcome and not args.limit else registros
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
            ids = [metadata["id"] for metadata in metadados.values() if metadata["_shard"] == shard]
            if continuous:
                cache.mark_trained(ids, live_ids={record_id(r) for r in snapshot if r.get("_shard") == shard})
            else:
                cache.mark_trained(ids)
        if continuous:
            prune_generations(args.model_out, prior_id, execucao.id, args.model_generations)
    except Exception as error:
        execucao.data["cleanup"] = {
            **execucao.data["cleanup"], "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
        print(t("messages.published_model_cleanup_failed", error=error))
    execucao.finish("success")
    final_fit = execucao.data["training"]
    print(
        t("messages.training_completed", value_1=final_fit['images_used'], value_2=final_fit['category_count'], value_3=final_fit['class_count'], value_4=execucao.data['validation']['accuracy'])
    )
    print(t("messages.published_model_path", value_1=execucao.data['model']['model']))
    print(t("messages.training_summary_path", value_1=execucao.path))
    return 0

def parse_args(argv=None):
    configure_locale(argv)

    parser = ArgumentParser(
        description=(
            t("messages.training_description")
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--kind",
        choices=("category", "action"),
        default="category",
        help=t("messages.training_kind_help"),
    )

    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_DATASET),
        help=t("messages.training_dataset_help"),
    )

    parser.add_argument(
        "--split",
        choices=("phash", "session"),
        default="session",
        help=(
            t("messages.split_help")
        ),
    )

    parser.add_argument(
        "--cache-root",
        default=None,
        help=t("messages.cache_root_help"),
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=t("messages.no_cache_help"),
    )

    parser.add_argument(
        "--automatic", action="store_true",
        help=t("messages.automatic_help"),
    )
    parser.add_argument(
        "--resolution",
        type=_parse_resolution,
        default=None,
        help=t("messages.resolution_help"),
    )
    parser.add_argument(
        "--combine-resolutions",
        action="store_true",
        help=t("messages.combine_resolutions_help"),
    )
    parser.add_argument(
        "--min-samples", type=int, default=10000,
        help=t("messages.min_samples_help"),
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help=t("messages.batch_size_help"),
    )
    limpeza = parser.add_mutually_exclusive_group()
    limpeza.add_argument(
        "--cleanup", dest="cleanup", action="store_true", default=None,
        help=t("messages.cleanup_help"),
    )
    limpeza.add_argument(
        "--keep-data", dest="cleanup", action="store_false", default=None,
        help=t("messages.keep_data_help"),
    )

    parser.add_argument(
        "--model-out",
        default=None,
        help=t("messages.model_output_help"),
    )

    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.2,
        help=t("messages.test_ratio_help"),
    )

    parser.add_argument(
        "--outcome",
        nargs="*",
        default=None,
        metavar="RESULTADO",
        help=t("messages.outcome_filter_help"),
    )

    parser.add_argument(
        "--negatives",
        type=int,
        default=2,
        help=t("messages.negatives_help"),
    )
    parser.add_argument(
        "--label-source",
        choices=("confirmed", "all"),
        default="all",
        help=(
            t("messages.label_source_help")
        ),
    )

    parser.add_argument(
        "--trees",
        type=int,
        default=200,
        help=t("messages.tree_count_help"),
    )

    parser.add_argument(
        "--jobs",
        type=int,
        default=-1,
        help=t("messages.job_count_help"),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=t("messages.training_limit_help"),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help=t("messages.seed_help"),
    )
    parser.add_argument(
        "--min-macro-f1",
        type=float,
        default=0.55,
        help=t("messages.min_macro_f1_help"),
    )
    parser.add_argument(
        "--min-critical-recall",
        type=float,
        default=0.40,
        help=t("messages.min_critical_recall_help"),
    )
    parser.add_argument(
        "--critical-categories",
        nargs="*",
        default=["renovate", "fly"],
        metavar="CATEGORIA",
        help=t("messages.critical_categories_help"),
    )
    parser.add_argument(
        "--allow-unsafe-promotion",
        action="store_true",
        help=t("messages.unsafe_promotion_help"),
    )

    parser.add_argument("--replay-rows", type=int, default=12000, help=t("messages.replay_rows_help"))
    parser.add_argument("--model-generations", type=int, default=3, help=t("messages.model_generations_help"))
    args = parser.parse_args(argv)
    if not 20 <= args.replay_rows <= 1000000 or not 3 <= args.model_generations <= 100:
        parser.error(t("messages.invalid_retention_settings"))
    if args.resolution is not None and args.combine_resolutions:
        parser.error(t("messages.incompatible_resolution_options"))
    if not 0 <= args.min_macro_f1 <= 1 or not 0 <= args.min_critical_recall <= 1:
        parser.error(t("messages.invalid_promotion_thresholds"))
    return args


def main(argv=None):

    args = parse_args(argv)

    return train(args)


if __name__ == "__main__":

    raise SystemExit(main())
