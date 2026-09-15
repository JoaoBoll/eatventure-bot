#!/usr/bin/env python3
"""Simula bot em dados coletados (sem executar ações)."""

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
DEFAULT_OUTPUT = ROOT / "IA" / "simulation_report.json"


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
            "Faltam dependências.\n\n"
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


def simulate_bot(args):
    """
    Roda o bot em cada imagem do dataset, sem executar ações.
    """

    import cv2
    import joblib
    import numpy as np

    from dataset_io import action_label, read_index, box_labels
    from features import FEATURE_SIZE, box_features

    raiz = Path(args.dataset_root).expanduser().resolve()
    modelo_path = Path(args.model).expanduser().resolve()

    print(f"dataset ......... {raiz}")
    print(f"modelo .......... {modelo_path}")

    if not modelo_path.exists():
        raise SystemExit(f"Modelo não encontrado: {modelo_path}")

    registros, aviso = read_index(raiz)
    print(f"amostras ........ {len(registros)}")

    if aviso["invalidas"] or aviso["sem_imagem"]:
        print(
            f"  ignoradas: {aviso['invalidas']} linha(s) "
            f"inválida(s), {aviso['sem_imagem']} sem imagem"
        )

    if args.limit:
        registros = registros[:args.limit]
        print(f"limite .......... {len(registros)} amostras")

    # Carregar modelo
    clf = joblib.load(modelo_path)

    # Simulação
    resultados = {
        "total": 0,
        "corretos": 0,
        "divergencias": [],
        "estado_transicoes": Counter(),
        "acoes_preditas": Counter(),
        "acoes_esperadas": Counter(),
        "por_estado": {},
    }

    total = len(registros)

    for indice, registro in enumerate(registros, start=1):

        imagem = cv2.imread(str(registro["_path"]))
        if imagem is None:
            progress(indice, total, "simulando")
            continue

        acao_esperada = action_label(registro)
        estado = registro.get("state", "?")
        phash = registro.get("phash", "?")

        detections = _detect_frame(imagem, registro, clf)
        acao_predita = _simulate_action(estado, detections, registro)

        resultados["total"] += 1
        resultados["acoes_esperadas"][acao_esperada] += 1
        resultados["acoes_preditas"][acao_predita] += 1

        acertou = (acao_predita == acao_esperada)
        if acertou:
            resultados["corretos"] += 1
        else:
            resultados["divergencias"].append({
                "esperada": acao_esperada,
                "predita": acao_predita,
                "estado": estado,
                "arquivo": str(registro["_path"]),
                "phash": phash,
                "deteccoes": len(detections),
            })

        if estado not in resultados["por_estado"]:
            resultados["por_estado"][estado] = {"total": 0, "corretos": 0}

        stats = resultados["por_estado"][estado]
        stats["total"] += 1
        if acertou:
            stats["corretos"] += 1

        resultados["estado_transicoes"][f"{estado} -> {acao_predita}"] += 1

        if indice % 25 == 0 or indice == total:
            progress(indice, total, "simulando")

    # Limitar divergências se houver muitas
    if len(resultados["divergencias"]) > 100:
        resultados["divergencias"] = resultados["divergencias"][:100]
        resultados["divergencias_truncadas"] = True

    return resultados


def _detect_frame(imagem, registro, clf):
    import cv2
    import numpy as np
    from dataset_io import box_labels
    from features import FEATURE_SIZE, box_features

    detections = []
    for caixa, categoria_real, _ in box_labels(registro):
        vetor = box_features(imagem, caixa)
        if vetor is None:
            continue

        vetor = np.asarray([vetor], dtype=np.float32)
        predicao = clf.predict(vetor)[0]
        confianca = clf.predict_proba(vetor)[0].max()

        detections.append({
            "categoria": str(predicao),
            "categoria_real": str(categoria_real),
            "confianca": float(confianca),
            "caixa": caixa,
        })

    return detections


def _simulate_action(estado, detections, registro):
    """
    Simula: dado este estado e estas detecções, qual ação o bot faria?

    Isso é uma aproximação. O real seria rodar a StateMachine,
    mas podemos deduzir pela frequência e confiança das detecções.
    """

    # Agregar detecções por categoria
    por_categoria = {}

    for det in detections:
        cat = det["categoria"]

        if cat not in por_categoria:
            por_categoria[cat] = {
                "count": 0,
                "confianca_media": 0,
            }

        stats = por_categoria[cat]
        stats["count"] += 1
        stats["confianca_media"] = (
            stats["confianca_media"] * (stats["count"] - 1) / stats["count"]
            + det["confianca"] / stats["count"]
        )

    if not por_categoria:
        return "noop"

    # A categoria mais detectada com mais confiança
    melhor = max(
        por_categoria.items(),
        key=lambda x: x[1]["count"] * x[1]["confianca_media"]
    )

    categoria_melhor, stats_melhor = melhor

    # Mapear categoria para ação
    # (isto é uma simulação; o real usaria a tabela de ações da StateMachine)
    acao_map = {
        "food": "food_click",
        "button": "button_click",
        "renovate": "renovate_click",
        "fly": "fly_click",
        "plane": "plane_click",
        "background": "noop",
    }

    acao_predita = acao_map.get(categoria_melhor, f"{categoria_melhor}_click")

    # Se confiança muito baixa, não fazer nada
    if stats_melhor["confianca_media"] < 0.5:
        acao_predita = "noop"

    return acao_predita


def report_simulation(resultados, args):
    """
    Exibe relatório da simulação.
    """

    total = resultados["total"]
    corretos = resultados["corretos"]
    divergencias = len(resultados["divergencias"])

    taxa = (corretos / total * 100) if total > 0 else 0

    print()
    print("=" * 62)
    print("  SIMULAÇÃO DO BOT")
    print("=" * 62)
    print()
    print(f"  Total de frames ............. {total:7d}")
    print(f"  Ações corretas ............. {corretos:7d} ({taxa:6.2f}%)")
    print(f"  Divergências ............... {divergencias:7d}")
    print()

    # Por estado
    print("-" * 62)
    print("  POR ESTADO")
    print("-" * 62)
    print()

    for estado in sorted(resultados["por_estado"].keys()):
        stats = resultados["por_estado"][estado]

        taxa_estado = (stats["corretos"] / stats["total"] * 100) if stats["total"] > 0 else 0

        print(
            f"  {estado:<16} {stats['total']:6d} | "
            f"acertos {taxa_estado:6.2f}%"
        )

    print()

    # Ações mais preditas vs esperadas
    print("-" * 62)
    print("  AÇÕES: ESPERADAS vs PREDITAS")
    print("-" * 62)
    print()

    todas_acoes = set(resultados["acoes_esperadas"].keys()) | set(resultados["acoes_preditas"].keys())

    for acao in sorted(todas_acoes):
        esp = resultados["acoes_esperadas"].get(acao, 0)
        pred = resultados["acoes_preditas"].get(acao, 0)

        print(f"  {acao:<20} esperada {esp:6d} | predita {pred:6d}")

    print()

    # Maiores divergências
    if resultados["divergencias"]:

        print("-" * 62)
        print("  MAIORES DIVERGÊNCIAS")
        print("-" * 62)
        print()

        # Agregar: qual estado + ação esperada gera erros?
        confusoes = Counter()

        for div in resultados["divergencias"]:
            chave = (div["estado"], div["esperada"], div["predita"])
            confusoes[chave] += 1

        for (estado, esp, pred), quantas in confusoes.most_common(10):
            print(
                f"  {estado:<12} | esperava {esp:<16} "
                f"| fez {pred:<16} ({quantas:4d}x)"
            )

        print()
        print("  Exemplos de divergência:")

        for div in resultados["divergencias"][:5]:
            print(
                f"    {div['estado']:<12} esperava {div['esperada']:<16} "
                f"fez {div['predita']:<16} "
                f"({div['deteccoes']} detecções)"
            )

        print()

    # Transições mais frequentes
    print("-" * 62)
    print("  TRANSIÇÕES MAIS FREQUENTES")
    print("-" * 62)
    print()

    for transicao, quantas in resultados["estado_transicoes"].most_common(15):
        print(f"  {transicao:<30} {quantas:6d}x")

    print()


def save_simulation(resultados, output_path):
    """
    Salva relatório em JSON.
    """

    dados = {
        "total": resultados["total"],
        "corretos": resultados["corretos"],
        "taxa_acerto": (resultados["corretos"] / resultados["total"]) if resultados["total"] > 0 else 0,
        "divergencias": resultados["divergencias"][:50],  # Limitar
        "por_estado": {
            k: {
                "total": v["total"],
                "corretos": v["corretos"],
                "taxa": v["corretos"] / v["total"] if v["total"] > 0 else 0,
            }
            for k, v in resultados["por_estado"].items()
        },
        "acoes_esperadas": dict(resultados["acoes_esperadas"]),
        "acoes_preditas": dict(resultados["acoes_preditas"]),
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Relatório salvo em: {output_path}")


def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description="Simula o bot em dados coletados (sem executar ações).",
    )

    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_DATASET),
        help="pasta do dataset",
    )

    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL),
        help="caminho do modelo treinado (model.joblib)",
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="arquivo JSON com relatório",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="simula só as N primeiras amostras",
    )

    parser.add_argument(
        "--show-mismatches",
        action="store_true",
        help="mostra só os casos onde diverge",
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)

    check_dependencies()

    print()
    print("=" * 62)
    print("  SIMULADOR DO BOT")
    print("=" * 62)
    print()

    resultados = simulate_bot(args)

    report_simulation(resultados, args)

    save_simulation(resultados, args.output)

    print()
    print("✓ Simulação concluída.")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
