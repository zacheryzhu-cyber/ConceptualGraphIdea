"""Step 1: find symbols with the ready-trained RF-DETR P&ID model (runs locally, CPU or GPU).

Model: https://huggingface.co/dimtri009/rfdetr-pid-detector  (Apache-2.0 per its model card)
It knows valves, blinds, reducers, flanges, flow arrows and instrument bubbles.
It does NOT know equipment (pumps, tanks, exchangers): those come from equipment.py.

Each symbol gets a label in the Azure sample's naming scheme, because the Azure graph code
decides what is an asset by label prefix: 'Equipment/', 'Instrument/Valve/', 'Instrument/', 'Piping/'.
"""
import os

import cv2
import numpy as np

# RF-DETR class id -> Azure-style label. Names follow the model card (including its spelling).
CLASS_TO_LABEL = {
    1: "Instrument/Valve/Gate valve", 2: "Instrument/Valve/Ball valve", 3: "Instrument/Valve/Globe valve NO",
    4: "Instrument/Valve/Gate valve NO", 5: "Instrument/Valve/Globe valve NO", 6: "Instrument/Valve/Butterfly valve",
    7: "Instrument/Valve/Plug valve", 8: "Instrument/Valve/Check valve", 9: "Instrument/Valve/Diaphragm valve",
    10: "Instrument/Valve/Needle valve", 11: "Instrument/Valve/Half filled gate valve",
    12: "Instrument/Valve/Gate valve NC", 13: "Instrument/Valve/Globe valve NC", 14: "Instrument/Valve/Control valve",
    15: "Instrument/Valve/Rotary valve", 16: "Instrument/Valve/Ball valve NC",
    17: "Piping/Fittings/Paddle blind", 18: "Piping/Fittings/Spectacle blind closed",
    19: "Piping/Fittings/Spectacle blind open", 20: "Piping/Fittings/Reducer",
    21: "Piping/Fittings/Flanged connection", 22: "Piping/Fittings/Rupture disk",
    24: "Piping/Fittings/Mid arrow flow direction", 25: "Instrument/Indicator/Sight glass",
    26: "Instrument/Indicator/Field mounted", 27: "Instrument/Indicator/Field mounted",
    28: "Instrument/Indicator/Panel mounted", 29: "Instrument/Indicator/Auxiliary panel",
    30: "Instrument/Indicator/Box", 31: "Instrument/Indicator/Panel mounted", 32: "Instrument/Indicator/Box",
}
IGNORED_CLASSES = {0, 23}   # 0 = not used, 23 = insulation/tracing (not a component)

_model = None


def load_model(weights_path):
    """Load the downloaded checkpoint. The path must include a folder (e.g. models/x.pth), otherwise
    rfdetr tries to download a file of that name from Roboflow instead of using yours."""
    global _model
    if _model is not None:
        return _model
    from rfdetr import RFDETRBase
    path = os.path.abspath(weights_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"RF-DETR weights not found: {path}")
    try:
        _model = RFDETRBase(pretrain_weights=path)
    except Exception as first_error:
        # Fine-tuned checkpoints often contain training settings that the safe loader refuses.
        # Only do this for a file you downloaded from a source you trust.
        print(f"Safe load failed ({first_error.__class__.__name__}); loading trusted local checkpoint")
        _model = RFDETRBase.from_checkpoint(path, trust_checkpoint=True)
    return _model


def detect_symbols(img_bgr, weights_path, threshold=0.5, tile=1280, overlap=0.2):
    """Run RF-DETR over overlapping tiles (P&IDs are too big to shrink into one 560-px input)."""
    import supervision as sv

    model = load_model(weights_path)
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    step = int(tile * (1 - overlap))
    parts = []
    for y in range(0, max(h - tile, 0) + step, step):
        for x in range(0, max(w - tile, 0) + step, step):
            y0, x0 = min(y, max(h - tile, 0)), min(x, max(w - tile, 0))
            crop = rgb[y0:y0 + tile, x0:x0 + tile]
            det = model.predict(crop, threshold=threshold)
            if len(det):
                det.xyxy = det.xyxy + np.array([x0, y0, x0, y0])
                det.metadata = {}   # rfdetr stores each tile's image here; supervision can't merge differing metadata
                parts.append(det)
    if not parts:
        return []
    merged = sv.Detections.merge(parts).with_nms(threshold=0.5, class_agnostic=False)
    symbols = []
    for (x0, y0, x1, y1), cls, conf in zip(merged.xyxy, merged.class_id, merged.confidence):
        cls = int(cls)
        if cls in IGNORED_CLASSES or cls not in CLASS_TO_LABEL:
            continue
        symbols.append({"label": CLASS_TO_LABEL[cls], "box": [float(x0), float(y0), float(x1), float(y1)],
                        "score": float(conf), "source": "rf-detr"})
    return symbols
