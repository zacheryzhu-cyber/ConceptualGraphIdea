"""Step 3: run the Microsoft Azure sample's own line detection + graph construction code, locally.

The vendored code in ./azure_pid is unchanged except one mechanical edit: `from pydantic import`
became `from pydantic.v1 import`, so it can share an environment with pyDEXPI (pydantic 2).
No Azure services are called: we hand it our own symbols (RF-DETR + equipment-from-tags) and
our own text (PDF text layer or local OCR), in place of its Azure ML endpoint and Azure OCR.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The Azure code reads these settings at import time and refuses empty values. We never call those
# services, so placeholders are fine.
for _var in ("BLOB_STORAGE_ACCOUNT_URL", "BLOB_STORAGE_CONTAINER_NAME", "FORM_RECOGNIZER_ENDPOINT",
             "SYMBOL_DETECTION_API", "SYMBOL_DETECTION_API_BEARER_TOKEN", "GRAPH_DB_CONNECTION_STRING"):
    os.environ.setdefault(_var, "not-used-locally")
sys.path.insert(0, os.path.join(HERE, "azure_pid"))

# The Azure text-detection package imports its Azure OCR client when loaded. We replace OCR with
# RapidOCR / the PDF text layer, so register a stand-in module instead of installing the Azure SDK.
import types  # noqa: E402

_stub = types.ModuleType("app.services.text_detection.utils.ocr_client")
_stub.ocr_client = None
sys.modules["app.services.text_detection.utils.ocr_client"] = _stub

import cv2  # noqa: E402

from app.config import config  # noqa: E402
from app.models.image_details import ImageDetails  # noqa: E402
from app.models.symbol_detection.label import Label  # noqa: E402
from app.models.symbol_detection.symbol_detection_inference_response import SymbolDetectionInferenceResponse  # noqa: E402
from app.models.text_detection.text_recognized import TextRecognized  # noqa: E402
from app.models.graph_construction.graph_construction_request import GraphConstructionInferenceRequest  # noqa: E402
from app.services.text_detection.symbol_to_text_correlation_service import correlate_symbols_with_text  # noqa: E402
from app.services.line_detection.line_detection_service import detect_lines  # noqa: E402
from app.services.graph_construction.graph_construction_service import construct_graph  # noqa: E402


# Distances at a 5000-px-wide image; scaled automatically for other sizes. Raise them if equipment
# that is clearly piped together comes out unconnected; lower them if unrelated items get linked.
SYMBOL_GAP_PX = int(os.environ.get("PID_SYMBOL_GAP_PX", 40))        # symbol touching symbol (nozzle -> vessel)
LINE_TO_SYMBOL_PX = int(os.environ.get("PID_LINE_TO_SYMBOL_PX", 15))  # line end touching a symbol


def _norm(box, w, h):
    x0, y0, x1, y1 = box
    return dict(topX=max(x0 / w, 0.0), topY=max(y0 / h, 0.0), bottomX=min(x1 / w, 1.0), bottomY=min(y1 / h, 1.0))


def build_graph(img_bgr, symbols, text_lines, debug_dir, drawing_area=None):
    """symbols: [{label, box(px), tag?}], text_lines: [{text, box(px)}].
    drawing_area: optional [x0,y0,x1,y1] px to ignore title block / tables.
    Returns (assets, connections) where connections are Azure's asset-to-asset links."""
    os.makedirs(debug_dir, exist_ok=True)
    h, w = img_bgr.shape[:2]
    # The Azure defaults are fixed pixel distances tuned for Microsoft's own drawings. Scale them to
    # this image (tested on the DEXPI sample at 5000 px wide: 0 -> 4 of 5 equipment links found).
    scale = max(w, h) / 5000
    config.graph_symbol_to_symbol_distance_threshold_pixels = int(SYMBOL_GAP_PX * scale)
    config.graph_distance_threshold_for_symbols_pixels = int(LINE_TO_SYMBOL_PX * scale)
    ok, png = cv2.imencode(".png", img_bgr)
    image_bytes = png.tobytes()
    details = ImageDetails(format="png", width=w, height=h)
    inclusive = None
    if drawing_area:
        from app.models.bounding_box import BoundingBox
        inclusive = BoundingBox(**_norm(drawing_area, w, h))

    labels = [Label(id=i, label=s["label"], score=s.get("score"), **_norm(s["box"], w, h))
              for i, s in enumerate(symbols)]
    texts = [TextRecognized(text=t["text"], **_norm(t["box"], w, h)) for t in text_lines if t["text"]]
    symbol_response = SymbolDetectionInferenceResponse(image_url="local.png", image_details=details,
                                                       bounding_box_inclusive=inclusive, label=labels)
    prefixes = tuple(sorted(p.lower() for p in config.symbol_label_prefixes_with_text))
    associated = correlate_symbols_with_text(texts, symbol_response,
                                             config.text_detection_area_intersection_ratio_threshold,
                                             config.text_detection_distance_threshold, prefixes)
    # Equipment found from its tag already knows its tag: keep it rather than a nearby text.
    for a, s in zip(associated, symbols):
        if s.get("tag"):
            a.text_associated = s["tag"]

    request = GraphConstructionInferenceRequest(
        image_url="local.png", image_details=details, bounding_box_inclusive=inclusive,
        all_text_list=texts, text_and_symbols_associated_list=associated)

    lines = detect_lines(
        "local", image_bytes, request,
        config.enable_thinning_preprocessing_line_detection,
        config.line_detection_hough_threshold,
        None if not config.detect_dotted_lines else config.line_detection_hough_max_line_gap,
        config.line_detection_hough_min_line_length,
        config.line_detection_hough_rho,
        config.line_detection_hough_theta,
        inclusive, h, w,
        os.path.join(debug_dir, "lines_preprocessed.png"),
        os.path.join(debug_dir, "lines_before_thinning.png"),
        os.path.join(debug_dir, "lines_detected.png"))

    # construct_graph uses a process pool. On Windows each worker first re-runs the parent's main script;
    # under Streamlit that is app.py, which fails outside the app and kills the workers ("process pool was
    # terminated abruptly"). Hide the main script while the workers start; they only need the Azure modules.
    main_module = sys.modules["__main__"]
    sys.modules["__main__"] = types.ModuleType("__main__")
    try:
        connected, _arrows = construct_graph(
            "local", image_bytes, request, lines,
            os.path.join(debug_dir, "graph.png"),
            os.path.join(debug_dir, "graph_connections.png"),
            os.path.join(debug_dir, "graph_lines_and_symbols.png"),
            config.symbol_label_prefixes_to_include_in_graph_image_output)
    finally:
        sys.modules["__main__"] = main_module

    assets = []
    connections = []
    for item in connected:
        d = item.dict() if hasattr(item, "dict") else item
        assets.append({"id": d["id"], "label": d["label"], "text": d["text_associated"],
                       "box": _px(d["bounding_box"], w, h)})
        for c in d.get("connections", []):
            fd = c["flow_direction"]
            connections.append({"from": d["id"], "to": c["id"], "to_label": c["label"],
                                "to_text": c["text_associated"],
                                "flow": fd.value if hasattr(fd, "value") else str(fd)})
    return assets, connections, len(lines.line_segments)


def _px(bb, w, h):
    return [bb["topX"] * w, bb["topY"] * h, bb["bottomX"] * w, bb["bottomY"] * h]
