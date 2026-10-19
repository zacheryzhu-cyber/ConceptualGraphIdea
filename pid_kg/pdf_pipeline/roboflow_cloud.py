"""Optional: find symbols, including equipment, with a model hosted on Roboflow (cloud).

Unlike the rest of the pipeline this SENDS THE DRAWING to Roboflow, tile by tile. The app only calls it when
the "Roboflow cloud" box is ticked. Any public Universe model works with your own API key, e.g. 'p-id-symbols/1'
(https://universe.roboflow.com/pid-connect/p-id-symbols). Class names differ per model, so they are mapped to the
Azure-style labels by keyword: valves, instruments, equipment, everything else is a piping fitting.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request

import numpy as np

import cv2

ROBOFLOW_URL = os.environ.get("PID_ROBOFLOW_URL", "https://serverless.roboflow.com")
DEFAULT_MODEL = "p-id-symbols/1"
TILE = 1600      # pixels per tile sent; the model resizes each tile to its own input size
OVERLAP = 0.3    # tiles overlap so equipment up to ~0.3 * TILE wide is whole in at least one tile

# Checked in this order: "Motor operated valve" is a valve, "Flow indicator" an instrument
VALVE_WORDS = ("valve", "regulator", "steam trap")
INSTRUMENT_WORDS = ("transmitter", "indicator", "controller", "gauge", "gage", "element", "instrument", "sensor",
                    "switch", "recorder", "analy", "meter", "alarm", "dcs", "plc")
EQUIPMENT_WORDS = ("pump", "compressor", "blower", "fan", "tank", "vessel", "drum", "column", "tower", "reactor",
                   "exchanger", "cooler", "heater", "condens", "reboiler", "boiler", "furnace", "filter", "separator",
                   "agitator", "mixer", "ejector", "turbine", "motor", "centrifuge", "silo", "hopper", "accumulator",
                   "absorber", "evaporator", "dryer", "drier", "crusher", "conveyor", "chiller", "equipment")


def to_label(class_name):
    """Roboflow class name -> Azure-style label, or None for junk classes ('0', '`', '- Resize to 640x640')."""
    name = re.sub(r"\s+", " ", str(class_name).replace("/", "-")).strip()
    low = name.lower()
    if not re.search(r"[a-z]{2}", low) or low.startswith("-") or len(name) > 60:
        return None
    if any(w in low for w in VALVE_WORDS):
        return "Instrument/Valve/" + name
    if any(w in low for w in INSTRUMENT_WORDS):
        return "Instrument/Indicator/" + name
    if any(w in low for w in EQUIPMENT_WORDS):
        return "Equipment/" + name
    return "Piping/Fittings/" + name


def _infer(tile_bgr, model_id, api_key):
    ok, buf = cv2.imencode(".jpg", tile_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
    req = urllib.request.Request(f"{ROBOFLOW_URL}/{model_id.strip('/')}?api_key={api_key}",
                                 data=base64.b64encode(buf.tobytes()), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    # errors are re-raised without the URL, so the API key never shows up in messages or logs
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(f"Roboflow model {model_id}: HTTP {exc.code} {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach Roboflow: {exc.reason}") from None


def _contained(a, b):
    """Share of box a that lies inside box b."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / (((a[2] - a[0]) * (a[3] - a[1])) or 1)


def detect_symbols_cloud(img_bgr, model_id, api_key, threshold=0.4, tile=TILE, overlap=OVERLAP):
    """Run a Roboflow-hosted model over overlapping tiles. Returns symbols in the same format as symbols.py."""
    import supervision as sv

    if not api_key:
        raise ValueError("Enter a Roboflow API key")
    h, w = img_bgr.shape[:2]
    step = int(tile * (1 - overlap))
    labels, parts = [], []
    for y in range(0, max(h - tile, 0) + step, step):
        for x in range(0, max(w - tile, 0) + step, step):
            y0, x0 = min(y, max(h - tile, 0)), min(x, max(w - tile, 0))
            res = _infer(img_bgr[y0:y0 + tile, x0:x0 + tile], model_id, api_key)
            boxes, scores, ids = [], [], []
            for p in res.get("predictions", []):
                label = to_label(p.get("class", ""))
                if label is None or p.get("confidence", 0) < threshold:
                    continue
                if label not in labels:
                    labels.append(label)
                cx, cy, bw, bh = p["x"] + x0, p["y"] + y0, p["width"], p["height"]
                boxes.append([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2])
                scores.append(p["confidence"])
                ids.append(labels.index(label))
            if boxes:
                parts.append(sv.Detections(xyxy=np.array(boxes, dtype=float), confidence=np.array(scores, dtype=float),
                                           class_id=np.array(ids, dtype=int)))
    if not parts:
        return []
    merged = sv.Detections.merge(parts).with_nms(threshold=0.5, class_agnostic=False)
    found = [{"label": labels[int(c)], "box": [float(v) for v in b], "score": float(s), "source": "roboflow"}
             for b, c, s in zip(merged.xyxy, merged.class_id, merged.confidence)]
    # an item cut by a tile edge comes back as a partial box inside the whole one: keep the whole one
    found.sort(key=lambda s: -(s["box"][2] - s["box"][0]) * (s["box"][3] - s["box"][1]))
    kept = []
    for s in found:
        if not any(k["label"] == s["label"] and _contained(s["box"], k["box"]) > 0.8 for k in kept):
            kept.append(s)
    return kept
