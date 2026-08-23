#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_ROOT = ROOT / "dataset"
DEFAULT_MODEL_PATH = ROOT / "IA" / "model.joblib"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from src.core.config import DATASET_ACTIONS
except Exception:  # pragma: no cover
    DATASET_ACTIONS = set()


def ensure_dependencies() -> None:
    modules = (
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("sklearn", "scikit-learn"),
        ("joblib", "joblib"),
    )

    missing = []
    for module_name, pip_name in modules:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    print("[train_ai] Instalando dependências de treino...", flush=True)
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                *missing,
            ]
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Falha ao instalar dependências de treino. Execute: "
            f"{sys.executable} -m pip install {' '.join(missing)}"
        ) from exc

    for module_name, _ in modules:
        try:
            __import__(module_name)
        except ImportError as exc:
            raise RuntimeError(f"Módulo obrigatório ainda não está disponível: {module_name}") from exc


def resolve_dataset_root(dataset_root: str | None) -> Path:
    if dataset_root:
        return Path(dataset_root).expanduser().resolve()
    return DEFAULT_DATASET_ROOT.resolve()


def normalize_label_value(value: object) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None
    return text.lower()


def detect_label(sample: dict, label_mode: str = "action") -> str | None:
    action = normalize_label_value(sample.get("action"))
    if label_mode == "action":
        if action in (None, ""):
            return "negative"

        normalized_actions = {normalize_label_value(value) for value in DATASET_ACTIONS if value is not None}
        if action in normalized_actions:
            return action

        target_category = normalize_label_value(sample.get("target_category"))
        if target_category not in (None, ""):
            return target_category

        for key in ("label", "class", "category"):
            value = normalize_label_value(sample.get(key))
            if value not in (None, ""):
                return value
        return None

    if label_mode == "category":
        target_category = normalize_label_value(sample.get("target_category"))
        if target_category not in (None, ""):
            return target_category
        if action in (None, ""):
            return "negative"
        for key in ("label", "class", "category"):
            value = normalize_label_value(sample.get(key))
            if value not in (None, ""):
                return value
        return action if action not in (None, "") else None

    for key in ("label", "class", "category", "target_category", "action"):
        value = normalize_label_value(sample.get(key))
        if value not in (None, ""):
            return value

    boxes = sample.get("boxes") or []
    if isinstance(boxes, list):
        for box in boxes:
            if isinstance(box, dict):
                category = normalize_label_value(box.get("category"))
                if category not in (None, ""):
                    return category
    return None


def parse_json_objects(raw_line: str) -> list[dict]:
    line = raw_line.strip()
    if not line:
        return []

    candidates = [line]
    if line.count("{") > 1:
        pieces = re.split(r'(?<=\})\s*(?=\{)|(?<=\])\s*(?=\{)', line)
        candidates = [piece.strip() for piece in pieces if piece and piece.strip()]

    parsed: list[dict] = []
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            parsed.append(obj)
    return parsed


def load_samples(dataset_root: Path, label_mode: str = "action") -> list[dict]:
    samples_file = dataset_root / "samples.jsonl"
    if not samples_file.exists():
        raise FileNotFoundError(
            f"Arquivo de treino não encontrado: {samples_file}. "
            f"Crie o dataset em {dataset_root} com imagens em {dataset_root / 'images'} e o índice em samples.jsonl."
        )

    samples: list[dict] = []
    with samples_file.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue

            parsed_items = parse_json_objects(line)
            if not parsed_items:
                print(f"[train_ai] Ignorando linha inválida em {samples_file}:{line_number}", flush=True)
                continue

            for sample in parsed_items:
                image_ref = sample.get("image")
                if not image_ref:
                    continue

                image_path = (dataset_root / image_ref).resolve()
                if not image_path.exists():
                    # compatibilidade com caminhos que venham como "images/..." ou caminhos absolutos
                    alt = (dataset_root.parent / image_ref).resolve()
                    if alt.exists():
                        image_path = alt
                    else:
                        continue

                label = detect_label(sample, label_mode=label_mode)
                if label is None:
                    continue

                samples.append({"image": image_path, "label": label})

    if not samples:
        raise ValueError(
            f"Nenhuma amostra válida foi encontrada em {samples_file}. "
            "Confira o formato do JSONL e os caminhos das imagens."
        )

    return samples


def render_progress_bar(percent: float, width: int = 30) -> str:
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    bar = "#" * filled + "." * (width - filled)
    return f"[{bar}]"


def emit_training_progress(current: int, total: int, phase: str, file_name: str | None = None, inline: bool = False) -> None:
    if total <= 0:
        return
    percent = min(100.0, max(0.0, (current / total) * 100.0))
    bar = render_progress_bar(percent)
    label = file_name or "arquivo"
    prefix = f"[train_ai] progresso {current}/{total} | {percent:.0f}% | {bar} | {phase}"
    if file_name:
        suffix = f" | {label}"
    else:
        suffix = ""

    if inline:
        print(f"\r{prefix}{suffix}", end="", flush=True)
    else:
        print(f"{prefix}{suffix}", flush=True)


def load_image_features(image_path: Path) -> list[float]:
    import cv2
    import numpy as np

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Não foi possível ler a imagem: {image_path}")

    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    image = image.astype(np.float32) / 255.0
    return image.reshape(-1).tolist()


def choose_classifier_backend(device: str = "auto"):
    requested = (device or "auto").lower()

    if requested in {"cuda", "gpu"}:
        try:
            from cuml.ensemble import RandomForestClassifier
            return RandomForestClassifier, "cuda"
        except Exception:
            print("[train_ai] CUDA solicitado, mas cuml não está disponível. Usando CPU.", flush=True)

    if requested == "auto":
        try:
            import torch
            if torch.cuda.is_available():
                try:
                    from cuml.ensemble import RandomForestClassifier
                    return RandomForestClassifier, "cuda"
                except Exception:
                    pass
        except Exception:
            pass

    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier, "cpu"


def train_model(
    dataset_root: Path,
    model_path: Path,
    label_mode: str = "action",
    train_ratio: float = 0.8,
    device: str = "auto",
) -> tuple[str, float, list[str], dict[str, int]]:
    if not 0.1 <= train_ratio <= 0.9:
        raise ValueError("train_ratio deve ficar entre 0.1 e 0.9 para garantir dados de treino e teste.")

    samples = load_samples(dataset_root, label_mode=label_mode)
    labels = sorted({sample["label"] for sample in samples})
    if len(labels) < 2:
        raise ValueError(
            "O dataset precisa ter pelo menos duas classes para treinar. "
            "Adicione imagens e labels diferentes em samples.jsonl."
        )

    counts = Counter(sample["label"] for sample in samples)

    import numpy as np
    from sklearn.model_selection import train_test_split

    X = []
    y = []
    total_samples = len(samples)
    for index, sample in enumerate(samples, start=1):
        X.append(load_image_features(sample["image"]))
        y.append(sample["label"])
        if index == total_samples or index % max(1, total_samples // 20) == 0:
            emit_training_progress(index, total_samples, "carregando imagens", file_name=sample["image"].name, inline=True)

    print(flush=True)
    X_array = np.asarray(X, dtype=np.float32)
    y_array = np.asarray(y)

    emit_training_progress(70, 100, "dividindo treino/teste")
    test_size = 1.0 - train_ratio
    X_train, X_test, y_train, y_test = train_test_split(
        X_array,
        y_array,
        train_size=train_ratio,
        test_size=test_size,
        random_state=42,
        stratify=y_array,
    )

    classifier_cls, backend = choose_classifier_backend(device)
    emit_training_progress(75, 100, "treinando classificador")
    clf = classifier_cls(
        n_estimators=300,
        random_state=42,
        class_weight="balanced",
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)
    emit_training_progress(95, 100, "avaliando modelo")
    accuracy = clf.score(X_test, y_test)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    import joblib

    emit_training_progress(98, 100, "salvando modelo")
    joblib.dump(clf, model_path)
    metadata_path = model_path.with_suffix(".meta.json")
    metadata_path.write_text(
        json.dumps({
            "label_mode": label_mode,
            "labels": labels,
            "counts": dict(sorted(counts.items())),
            "train_ratio": train_ratio,
            "backend": backend,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    emit_training_progress(100, 100, "concluído")
    return f"{accuracy:.2%}", accuracy, labels, dict(sorted(counts.items()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Treina uma IA a partir de imagens e samples.jsonl.")
    parser.add_argument("--dataset-root", type=str, default=None, help="Caminho para a pasta do dataset. Padrão: dataset/")
    parser.add_argument("--model-out", type=str, default=str(DEFAULT_MODEL_PATH), help="Onde salvar o modelo treinado.")
    parser.add_argument(
        "--label-mode",
        choices=("action", "category", "auto"),
        default="action",
        help="Como rotular cada imagem: 'action' usa a ação do bot, 'category' usa a categoria alvo, 'auto' usa action quando houver e categoria como fallback.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Porcentagem de dados usada para treino. Ex.: 0.8 = 80%% treino / 20%% teste.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Backend do treino: 'auto' tenta CUDA quando disponível e cuml existe; cai para CPU em seguida.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dependencies()

    dataset_root = resolve_dataset_root(args.dataset_root)
    model_path = Path(args.model_out).expanduser().resolve()
    label_mode = "action" if args.label_mode == "action" else ("category" if args.label_mode == "category" else "action")

    print(f"[train_ai] Dataset: {dataset_root}")
    print(f"[train_ai] Modelo: {model_path}")
    print(f"[train_ai] Modo de label: {label_mode}")
    print(f"[train_ai] Treino: {args.train_ratio:.0%} / teste: {(1 - args.train_ratio):.0%}")
    print(f"[train_ai] Backend: {args.device}")
    print("[train_ai] Iniciando treinamento...", flush=True)

    accuracy_str, _, labels, counts = train_model(
        dataset_root,
        model_path,
        label_mode=label_mode,
        train_ratio=args.train_ratio,
        device=args.device,
    )

    print("[train_ai] Treinamento concluído.")
    print(f"[train_ai] Classes detectadas: {', '.join(labels)}")
    print(f"[train_ai] Distribuição: {counts}")
    print(f"[train_ai] Precisão estimada: {accuracy_str}")
    print(f"[train_ai] Modelo salvo em: {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
