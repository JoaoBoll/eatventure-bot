"""Localizador supervisionado de centros de UI; coordenadas sempre no frame original."""

from collections import Counter, defaultdict
import hashlib

import cv2
import numpy as np
from sklearn.tree import DecisionTreeClassifier


STEP = 8
BACKGROUND = "__background__"
OFFSETS = [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1),
           (-3, 0), (3, 0), (0, -3), (0, 3), (-3, -3), (3, 3),
           (-7, 0), (7, 0), (0, -7), (0, 7)]


def _grid(frame):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (max(1, w // STEP), max(1, h // STEP)), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    pad = cv2.copyMakeBorder(small, 7, 7, 7, 7, cv2.BORDER_REPLICATE)
    return np.concatenate([pad[7+y:7+y+h, 7+x:7+x+w] for x, y in OFFSETS], axis=2)


def _labels(record):
    if record.get("outcome") != "changed":
        return []
    return [b for b in record.get("boxes", ()) if b.get("acted") and b.get("category")
            and b.get("width", 0) > 0 and b.get("height", 0) > 0]


def _sample(records, per_class):
    groups = defaultdict(list)
    for record in records:
        if not record.get("_path"):
            continue
        labels = _labels(record)
        if labels or record.get("action") is None:
            key = labels[0]["category"] if labels else BACKGROUND
            groups[key].append(record)
    for key in sorted(groups):
        yield from sorted(
            groups[key],
            key=lambda r: hashlib.sha256(str(r.get("id") or r.get("_path")).encode()).digest(),
        )[:per_class]


def _fit(records):
    rng = np.random.default_rng(13)
    rows, targets, sizes = [], [], defaultdict(list)
    for record in _sample(records, 36):
        frame = cv2.imread(str(record["_path"]))
        if frame is None:
            continue
        grid = _grid(frame)
        h, w = grid.shape[:2]
        sy, sx = h/frame.shape[0], w/frame.shape[1]
        available = np.ones((h, w), dtype=bool)
        for b in record.get("boxes", ()):
            x, y = b["x"], b["y"]
            available[max(0, int(y*sy)-1):int((y+b["height"])*sy)+2,
                      max(0, int(x*sx)-1):int((x+b["width"])*sx)+2] = False
        for b in _labels(record):
            bw, bh = b["width"], b["height"]
            x, y = int((b["x"]+bw/2)*sx), int((b["y"]+bh/2)*sy)
            rx, ry = min(2, int(bw*sx/6)), min(2, int(bh*sy/6))
            region = grid[max(0, y-ry):min(h, y+ry+1), max(0, x-rx):min(w, x+rx+1)]
            if region.size:
                rows.extend(region.reshape(-1, grid.shape[-1]))
                targets.extend([b["category"]] * (region.shape[0]*region.shape[1]))
                sizes[b["category"]].append((bw, bh))
        ys, xs = np.where(available)
        choice = rng.choice(len(ys), min(len(ys), 128), replace=False)
        rows.extend(grid[ys[choice], xs[choice]])
        targets.extend([BACKGROUND] * len(choice))
    shapes = {k: np.median(v, axis=0).astype(int).tolist() for k, v in sizes.items() if len(v) >= 4}
    if not shapes or BACKGROUND not in targets:
        return None
    tree = DecisionTreeClassifier(max_depth=14, min_samples_leaf=3, random_state=13)
    tree.fit(np.asarray(rows), targets)
    return {"tree": tree, "sizes": shapes}


def locate(locator, frame, allowed=None):
    sizes = locator["sizes"]
    wanted = set(sizes) if allowed is None else set(allowed).intersection(sizes)
    if not wanted:
        return []
    grid = _grid(frame)
    h, w, channels = grid.shape
    scores = locator["tree"].predict_proba(grid.reshape(-1, channels))
    scale_x, scale_y = frame.shape[1]/w, frame.shape[0]/h
    proposals = []
    for col, label in enumerate(locator["tree"].classes_):
        if label not in wanted:
            continue
        bw, bh = sizes[label]
        if bw > frame.shape[1] or bh > frame.shape[0]:
            continue
        heat = cv2.blur(scores[:, col].reshape(h, w).astype("float32"), (3, 3))
        for _ in range(4):
            _, score, _, (x, y) = cv2.minMaxLoc(heat)
            if score < 0.4:
                break
            bx = int(np.clip((x+.5)*scale_x-bw/2, 0, frame.shape[1]-bw))
            by = int(np.clip((y+.5)*scale_y-bh/2, 0, frame.shape[0]-bh))
            proposals.append((str(label), (bx, by, bw, bh)))
            rx, ry = max(1, int(bw/scale_x/2)), max(1, int(bh/scale_y/2))
            heat[max(0, y-ry):y+ry+1, max(0, x-rx):x+rx+1] = 0
    return proposals


def detect_regions(classifier, locator, frame, confidence, allowed=None):
    from IA.features import box_features

    pairs = [(label, box, box_features(frame, box)) for label, box in locate(locator, frame, allowed)]
    pairs = [pair for pair in pairs if pair[2] is not None]
    if not pairs:
        return []
    scores = classifier.predict_proba(np.array([p[2] for p in pairs]))
    results = []
    for (label, (x, y, w, h), _), row in zip(pairs, scores):
        idx = int(np.argmax(row))
        if classifier.classes_[idx] == label and row[idx] >= confidence:
            results.append(dict(category=label, x=x, y=y, width=w, height=h,
                                name=f"modelo:{label}", confidence=float(row[idx]), color_similarity=float(row[idx])))
    return sorted(results, key=lambda d: -d["confidence"])


def _overlap(a, b):
    dx = min(a["x"]+a["width"], b["x"]+b["width"]) - max(a["x"], b["x"])
    dy = min(a["y"]+a["height"], b["y"]+b["height"]) - max(a["y"], b["y"])
    area = max(0, dx)*max(0, dy)
    return area / (a["width"]*a["height"] + b["width"]*b["height"] - area)


def attach_locator(classifier, train_records, validation_records, incumbent=None):
    """Só libera classes que localizam e classificam os alvos em telas reservadas."""
    locator = _fit(train_records)
    old = getattr(incumbent, "region_locator_", None)
    if not locator:
        return {"classes": [], "frames": 0}
    support, hits, guesses = Counter(), Counter(), Counter()
    old_hits, old_guesses = Counter(), Counter()
    frames = 0
    for r in _sample(validation_records, 12):
        frame = cv2.imread(str(r["_path"]))
        if frame is None:
            continue
        frames += 1
        expected = _labels(r)
        support.update(b["category"] for b in expected)
        for model, loc, tp, count in ((classifier, locator, hits, guesses),
                                      (incumbent, old, old_hits, old_guesses)):
            if loc is None:
                continue
            predictions = detect_regions(model, loc, frame, 0.60)
            count.update(p["category"] for p in predictions)
            for b in expected:
                if any(p["category"] == b["category"] and _overlap(p, b) >= 0.4 for p in predictions):
                    tp[b["category"]] += 1
    accepted = [k for k in locator["sizes"] if support[k] >= 4 and
                hits[k] / support[k] >= 0.6 and hits[k] / max(1, guesses[k]) >= 0.75 and
                hits[k] >= old_hits[k] and guesses[k]-hits[k] <= max(1, old_guesses[k]-old_hits[k])]
    if accepted and (not old or set(old["sizes"]).issubset(accepted)):
        locator["sizes"] = {k: locator["sizes"][k] for k in accepted}
        classifier.region_locator_ = locator
    return {"classes": sorted(getattr(classifier, "region_locator_", {}).get("sizes", {})),
            "frames": frames, "support": dict(support), "hits": dict(hits), "predictions": dict(guesses)}
