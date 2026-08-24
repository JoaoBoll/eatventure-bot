#!/usr/bin/env python3
"""Viewer/runner do bot controlado por IA.

Modo seguro por padrão:
  - mostra a tela do device e a decisão da IA em tempo real
  - não clica sozinho sem --auto

Uso:
  python IA/bot_ai.py --device SERIAL --auto --confidence-threshold 0.75
  python IA/bot_ai.py --demo --demo-limit 5
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import joblib

ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "src"
MODEL_PATH = ROOT / "IA" / "model.joblib"
DATASET_ROOT = ROOT / "dataset"

# Tamanho da janela de visão da IA. Ajuste aqui para deixar a tela mais
# estreita/larga conforme sua resolução e preferência visual.
AI_WINDOW_WIDTH = 405
AI_WINDOW_HEIGHT = 720

for candidate in (str(ROOT), str(SRC_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Modelo não encontrado: {MODEL_PATH}. Rode primeiro: python IA/train_ai.py"
        )

    model = joblib.load(MODEL_PATH)
    meta_path = MODEL_PATH.with_suffix(".meta.json")
    labels = []

    if hasattr(model, "classes_"):
        labels = [str(item) for item in model.classes_]

    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not labels:
                labels = [str(item) for item in meta.get("labels", [])]
        except Exception:
            pass

    if not labels:
        raise ValueError("Modelo sem labels disponíveis. Treine novamente o modelo.")

    return model, labels


def preprocess_frame(frame):
    image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    image = image.astype(np.float32) / 255.0
    return image.reshape(-1)


def predict(model, labels, frame):
    features = preprocess_frame(frame)
    feature_matrix = np.asarray([features], dtype=np.float32)

    if hasattr(model, "predict_proba"):
        probs = model.predict_proba(feature_matrix)[0]
        idx = int(np.argmax(probs))
        confidence = float(probs[idx])
        prediction = str(labels[idx])
        return prediction, confidence

    prediction = str(model.predict(feature_matrix)[0])
    return prediction, 1.0


def draw_overlay(frame, prediction, confidence, center_target=None, should_click=False):
    out = frame.copy()
    height, width = out.shape[:2]

    label = f"AI: {prediction}"
    subtitle = f"conf: {confidence:.2%}"

    cv2.rectangle(out, (10, 10), (width - 10, 90), (0, 0, 0), -1)
    cv2.putText(out, label, (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(out, subtitle, (24, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    if should_click:
        cv2.putText(out, "CLICK ->", (24, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    if center_target is not None:
        x, y = center_target
        cv2.circle(out, (x, y), 10, (0, 255, 0), 2)
        cv2.line(out, (x - 20, y), (x + 20, y), (0, 255, 0), 2)
        cv2.line(out, (x, y - 20), (x, y + 20), (0, 255, 0), 2)

    return out


def draw_approval_buttons(frame, prediction, confidence):
    out = frame.copy()
    height, width = out.shape[:2]
    btn_h = 52
    btn_w = 180
    gap = 18
    x1 = max(18, width // 2 - btn_w - gap // 2)
    x2 = min(width - 18, width // 2 + gap // 2)
    approve_rect = (x1, height - 72, x1 + btn_w, height - 18)
    reject_rect = (x2, height - 72, x2 + btn_w, height - 18)

    cv2.rectangle(out, approve_rect[:2], approve_rect[2:], (0, 180, 0), -1)
    cv2.rectangle(out, reject_rect[:2], reject_rect[2:], (0, 0, 180), -1)
    cv2.putText(out, "APPROVE", (approve_rect[0] + 30, approve_rect[1] + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(out, "REJECT", (reject_rect[0] + 30, reject_rect[1] + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(out, f"Decision: {prediction} ({confidence:.2%})", (18, height - 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return out, approve_rect, reject_rect

AI_WINDOW_HEIGHT
def iter_demo_images(limit=None):
    if not DATASET_ROOT.exists():
        return []

    records = []
    samples_path = DATASET_ROOT / "samples.jsonl"
    if not samples_path.exists():
        return []

    with samples_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue
            image = sample.get("image")
            if not image:
                continue
            image_path = (DATASET_ROOT / image).resolve()
            if image_path.exists():
                records.append(image_path)
                if limit is not None and len(records) >= limit:
                    break
    return records


def demo_mode(model, labels, limit, headless=False):
    images = iter_demo_images(limit=limit)
    if not images:
        print("[bot_ai] Nenhuma imagem válida encontrada no dataset.")
        return 0

    for image_path in images:
        frame = cv2.imread(str(image_path))
        if frame is None:
            continue

        prediction, confidence = predict(model, labels, frame)
        center = (frame.shape[1] // 2, frame.shape[0] // 2)
        print(f"[bot_ai] {image_path.name}: {prediction} | conf={confidence:.2%}")

        if headless:
            continue

        view = draw_overlay(frame, prediction, confidence, center, should_click=True)
        resized = cv2.resize(view, (AI_WINDOW_WIDTH, AI_WINDOW_HEIGHT), interpolation=cv2.INTER_AREA)
        cv2.imshow("AI bot decision", resized)
        key = cv2.waitKey(0)
        if key in (27, ord('q')):
            cv2.destroyAllWindows()
            return 0

    if not headless:
        cv2.destroyAllWindows()
    return 0


def execute_action_for_prediction(actions, prediction, center, confidence):
    if prediction in {"swipe_up", "swipe_down"}:
        actions.swipe(prediction.replace("swipe_", ""))
        print(f"[bot_ai] swipe {prediction} executado")
        return

    detection = {
        "x": max(0, center[0] - 50),
        "y": max(0, center[1] - 50),
        "width": 100,
        "height": 100,
    }
    actions.execute(prediction, detection)
    print(f"[bot_ai] ação {prediction} executada (conf={confidence:.2%})")


def screen_mode(model, labels, device_id, auto_click, confidence_threshold, headless=False, approval_mode=False):
    from actions.manager import ActionManager
    from capture.screen import ScreenCapture
    from core.config import REFERENCE_WIDTH, REFERENCE_HEIGHT

    actions = ActionManager(device_id)
    capture = ScreenCapture(device_id)

    actions.start()
    capture.start()

    last_version = 0
    decision_state = {"result": None}
    window_name = "AI bot decision"

    def on_mouse(event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if not approval_mode:
            return
        if decision_state.get("approve_rect") and decision_state["approve_rect"][0] <= x <= decision_state["approve_rect"][2] and decision_state["approve_rect"][1] <= y <= decision_state["approve_rect"][3]:
            decision_state["result"] = "approve"
            return
        if decision_state.get("reject_rect") and decision_state["reject_rect"][0] <= x <= decision_state["reject_rect"][2] and decision_state["reject_rect"][1] <= y <= decision_state["reject_rect"][3]:
            decision_state["result"] = "reject"

    try:
        while True:
            frame, version, _timestamp = capture.get_frame(since_version=last_version, timeout=0.2)
            if not capture.is_running():
                print("[bot_ai] Stream encerrado.")
                break
            if frame is None:
                continue

            last_version = version
            actions.set_frame_size(REFERENCE_WIDTH, REFERENCE_HEIGHT)

            prediction, confidence = predict(model, labels, frame)
            center = (frame.shape[1] // 2, frame.shape[0] // 2)
            should_click = bool(auto_click and confidence >= confidence_threshold)

            if not headless:
                if approval_mode and should_click:
                    view, approve_rect, reject_rect = draw_approval_buttons(draw_overlay(frame, prediction, confidence, center, should_click), prediction, confidence)
                    decision_state["approve_rect"] = approve_rect
                    decision_state["reject_rect"] = reject_rect
                    cv2.imshow(window_name, cv2.resize(view, (AI_WINDOW_WIDTH, AI_WINDOW_HEIGHT), interpolation=cv2.INTER_AREA))
                    cv2.setMouseCallback(window_name, on_mouse)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord('q')):
                        break
                    if key in (ord('a'), ord('A')):
                        decision_state["result"] = "approve"
                    if key in (ord('r'), ord('R')):
                        decision_state["result"] = "reject"
                    if decision_state["result"] in {"approve", "reject"}:
                        if decision_state["result"] == "approve":
                            execute_action_for_prediction(actions, prediction, center, confidence)
                        decision_state["result"] = None
                        continue
                else:
                    view = draw_overlay(frame, prediction, confidence, center, should_click)
                    resized = cv2.resize(view, (AI_WINDOW_WIDTH, AI_WINDOW_HEIGHT), interpolation=cv2.INTER_AREA)
                    cv2.imshow(window_name, resized)
                    key = cv2.waitKey(1) & 0xFF
                    if key == 27:
                        break
                
            if (approval_mode and should_click):
                print(f"[bot_ai] AI -> {prediction} | conf={confidence:.2%} | auto={should_click}")
            

            if auto_click and confidence >= confidence_threshold and not approval_mode:
                execute_action_for_prediction(actions, prediction, center, confidence)

    except KeyboardInterrupt:
        print("[bot_ai] Interrompido pelo usuário.")
    finally:
        if not headless:
            cv2.destroyAllWindows()
        actions.stop()
        capture.stop()

    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Bot controlado por IA com visão em janela OpenCV.")
    parser.add_argument("--device", type=str, default=None, help="Serial do device adb. Sem isso tenta autodetectar.")
    parser.add_argument("--auto", action="store_true", help="Executa a ação prevista automaticamente quando a confiança passar do threshold.")
    parser.add_argument("--approval-mode", action="store_true", help="Mostra botões de APPROVE/REJECT e aguarda confirmação antes de executar a ação da IA.")
    parser.add_argument("--confidence-threshold", type=float, default=0.70, help="Threshold mínimo para auto-click; default 0.70.")
    parser.add_argument("--demo", action="store_true", help="Mostra decisões sobre imagens do dataset em vez do stream ao vivo.")
    parser.add_argument("--demo-limit", type=int, default=10, help="Máximo de imagens do dataset para o modo demo.")
    parser.add_argument("--headless", action="store_true", help="Sem janela OpenCV; imprime as decisões em console.")
    parser.add_argument("--model", type=str, default=str(MODEL_PATH), help="Caminho do modelo treinado.")
    return parser.parse_args()


def main():
    args = parse_args()
    model_path = Path(args.model).expanduser().resolve()
    if model_path.exists():
        model = joblib.load(model_path)
    else:
        model = joblib.load(MODEL_PATH)

    labels = []
    if hasattr(model, "classes_"):
        labels = [str(item) for item in model.classes_]
    meta_path = model_path.with_suffix(".meta.json")
    if not labels and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            labels = [str(item) for item in meta.get("labels", [])]
        except Exception:
            pass
    if not labels:
        raise ValueError("Modelo sem labels disponíveis. Treine novamente o modelo.")

    if args.demo:
        return demo_mode(model, labels, args.demo_limit, headless=args.headless)
    return screen_mode(
        model,
        labels,
        args.device,
        args.auto,
        args.confidence_threshold,
        headless=args.headless,
        approval_mode=args.approval_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
