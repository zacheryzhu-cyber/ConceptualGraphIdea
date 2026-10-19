"""PDF / image P&ID -> sheet data for the plant knowledge graph (assets + connections).

Pipeline, all local:
  1. page_input   PDF page or image -> high-resolution picture
  2. symbols      ready-trained RF-DETR: valves, instruments, fittings, flow arrows
  3. page_input   tags/text: PDF text layer (exact) or RapidOCR (offline OCR)
  4. equipment    equipment from equipment tags + the outline drawn around/next to them
  5. azure_adapter  Microsoft Azure sample's line detection + graph construction -> connections
Everything produced here is marked source='pdf' and must be reviewed before it goes into the graph.

Optional: --roboflow-model also runs a Roboflow-hosted model, which adds equipment classes (pumps, tanks, ...)
but uploads the drawing to Roboflow (see roboflow_cloud.py).

Usage:  python -m pdf_pipeline.extract drawing.pdf --sheet 301 --weights models/rfdetr_pid.pth
        python -m pdf_pipeline.extract drawing.pdf --sheet 301 --roboflow-model p-id-symbols/1
"""
import argparse
import json
import os
import re

import cv2

from . import page_input
from .equipment import EQUIPMENT_TAG, find_equipment

INSTRUMENT_TAG = r"^[A-Z]{1,5}[- ]?\d{2,5}(\.\d{1,3})?[A-Z]?$"


def _category(label):
    if label.startswith("Equipment/"):
        return "Equipment"
    if label.startswith("Instrument/Valve/"):
        return "Piping"          # valves are piping components, as in the DEXPI route
    if label.startswith("Instrument/"):
        return "Instrumentation"
    return "Piping"


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1]) or 1
    return ix * iy / area


def _same_spot(a, b):
    return max(_overlap(a, b), _overlap(b, a)) > 0.5


def extract_sheet(path, sheet, weights=None, out_dir="pdf_output", page_number=0, force_ocr=False,
                  equipment_tag=EQUIPMENT_TAG, symbols_override=None, drawing_area=None, roboflow=None):
    """roboflow: optional {"model_id": ..., "api_key": ...} to also run a Roboflow-hosted model.
    That uploads the drawing to Roboflow; without it everything stays on this computer."""
    from .azure_adapter import build_graph   # imported late: it sets up the vendored Azure code

    work = os.path.join(out_dir, str(sheet))
    os.makedirs(work, exist_ok=True)
    img, page, zoom = page_input.load_page(path, page_number)
    texts, text_source = page_input.read_text(img, page, zoom, force_ocr=force_ocr)

    if symbols_override is not None:
        symbols = list(symbols_override)
    elif weights:
        from .symbols import detect_symbols
        symbols = detect_symbols(img, weights)
    elif roboflow:
        symbols = []
    else:
        raise ValueError("Give the RF-DETR weights file (models/...pth)")

    cloud = []
    if roboflow:
        from .roboflow_cloud import detect_symbols_cloud
        cloud = detect_symbols_cloud(img, roboflow["model_id"], roboflow["api_key"])
    cloud_equipment = [c for c in cloud if c["label"].startswith("Equipment/")]
    # the local model is the one measured on valves and instruments: a cloud symbol fills in only where it found nothing
    symbols += [c for c in cloud if c not in cloud_equipment and not any(_same_spot(c["box"], s["box"]) for s in symbols)]

    if not any(s["label"].startswith("Equipment/") for s in symbols):
        equipment = find_equipment(img, texts, equipment_tag, symbols)
        for e in equipment:   # a cloud equipment box on the same spot gives the tagged equipment its type
            same = [c for c in cloud_equipment if _same_spot(c["box"], e["box"])]
            if same:
                e["label"] = max(same, key=lambda c: c["score"])["label"]
        equipment += [c for c in cloud_equipment if not any(_same_spot(c["box"], e["box"]) for e in equipment)]
        # a valve/instrument box sitting inside an equipment outline is usually a mis-detection
        symbols = [s for s in symbols if not any(_overlap(s["box"], e["box"]) > 0.8 for e in equipment)]
        symbols = equipment + symbols

    assets_raw, connections, n_lines = build_graph(img, symbols, texts, work, drawing_area)

    # ---- convert to the plant loader's format
    eq_re, inst_re = re.compile(equipment_tag), re.compile(INSTRUMENT_TAG)
    by_id = {}
    for i, s in enumerate(symbols):
        by_id[i] = {"label": s["label"], "box": s["box"], "text": s.get("tag")}
    for a in assets_raw:
        by_id[a["id"]]["text"] = a["text"] or by_id[a["id"]]["text"]
    for c in connections:
        if by_id.get(c["to"]) is not None and not by_id[c["to"]]["text"]:
            by_id[c["to"]]["text"] = c["to_text"]

    used = {a["id"] for a in assets_raw} | {c["to"] for c in connections}
    used |= {i for i, s in enumerate(symbols) if s["label"].startswith("Equipment/")}
    assets, uid_of = [], {}
    for i in sorted(used):
        s = by_id[i]
        cat = _category(s["label"])
        text = (s["text"] or "").strip()
        compact = text.replace(" ", "")
        tagged = bool(compact) and bool((cat == "Equipment" and eq_re.match(compact)) or
                                        (cat == "Instrumentation" and inst_re.match(compact)))
        uid = f"TAG:{compact}" if tagged else f"{sheet}::pdf-{i}"
        uid_of[i] = uid
        assets.append({
            "uid": uid, "name": compact if tagged else (text or f"{s['label'].split('/')[-1]} #{i}"),
            "type": s["label"].split("/")[-1].replace(" ", ""), "category": cat, "tagged": tagged,
            "source": "pdf", "symbolLabel": s["label"], "box": [round(v) for v in s["box"]],
        })

    edges, seen = [], {}
    for c in connections:
        a, b, flow = c["from"], c["to"], c["flow"]
        if a not in uid_of or b not in uid_of or a == b:
            continue
        src, dst = (b, a) if flow == "upstream" else (a, b)
        key = frozenset((src, dst))
        if key in seen:
            if seen[key]["direction"] == "unknown" and flow != "unknown":
                seen[key].update(src=uid_of[src], dst=uid_of[dst], direction="known")
            continue
        e = {"src": uid_of[src], "dst": uid_of[dst], "rel": "PIPE",
             "direction": "unknown" if flow == "unknown" else "known",
             "props": {"dexpiType": "Line (from PDF)", "source": "pdf"}}
        seen[key] = e
        edges.append(e)

    issues = [f"from PDF ({text_source}); all items need review"]
    untagged_eq = [a for a in assets if a["category"] == "Equipment" and not a["tagged"]]
    if untagged_eq:
        issues.append(f"{len(untagged_eq)} equipment without a readable tag")

    result = {"sheet": str(sheet), "file": os.path.basename(path), "text_source": text_source,
              "symbols_found": len(symbols), "line_segments": n_lines,
              "roboflow_model": roboflow["model_id"] if roboflow else None, "roboflow_symbols": len(cloud),
              "assets": assets, "edges": edges, "issues": issues}
    with open(os.path.join(work, "extracted.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    _draw_overview(img, assets, edges, os.path.join(work, "overview.png"))
    _draw_symbols(img, symbols, os.path.join(work, "symbols.png"))
    result["overview_image"] = os.path.join(work, "overview.png")
    return result


def _draw_overview(img, assets, edges, path):
    """Picture for the review step: boxes for what was found, red lines for detected connections."""
    out = img.copy()
    colours = {"Equipment": (121, 78, 31), "Instrumentation": (43, 130, 217), "Piping": (120, 120, 120)}
    centre = {}
    for a in assets:
        x0, y0, x1, y1 = [int(v) for v in a["box"]]
        centre[a["uid"]] = ((x0 + x1) // 2, (y0 + y1) // 2)
        cv2.rectangle(out, (x0, y0), (x1, y1), colours[a["category"]], 4)
        cv2.putText(out, a["name"][:20], (x0, max(y0 - 8, 20)), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                    colours[a["category"]], 3)
    for e in edges:
        if e["src"] in centre and e["dst"] in centre:
            cv2.arrowedLine(out, centre[e["src"]], centre[e["dst"]], (40, 40, 220), 3, tipLength=0.02)
    cv2.imwrite(path, out)


def _draw_symbols(img, symbols, path):
    """Every symbol as found (before graph building), labelled with its class: use this to check that
    the model's classes are right, e.g. that a ball valve is called a ball valve."""
    out = img.copy()
    for s in symbols:
        x0, y0, x1, y1 = [int(v) for v in s["box"]]
        colour = (121, 78, 31) if s["label"].startswith("Equipment/") else (40, 140, 40)
        if s.get("source") == "roboflow":
            colour = (0, 140, 255)   # orange: found by the Roboflow cloud model
        cv2.rectangle(out, (x0, y0), (x1, y1), colour, 3)
        text = s.get("tag") or s["label"].split("/")[-1]
        if s.get("score") is not None and s.get("source") in ("rf-detr", "roboflow"):
            text += f" {s['score']:.2f}"
        cv2.putText(out, text, (x0, max(y0 - 6, 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
    cv2.imwrite(path, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--weights", default="models/rfdetr_pid.pth")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--ocr", action="store_true", help="ignore the PDF text layer and use OCR")
    ap.add_argument("--out", default="pdf_output")
    ap.add_argument("--roboflow-model", help="also run this Roboflow-hosted model (uploads the drawing); "
                                             "API key from the ROBOFLOW_API_KEY environment variable")
    args = ap.parse_args()
    roboflow = ({"model_id": args.roboflow_model, "api_key": os.environ.get("ROBOFLOW_API_KEY")}
                if args.roboflow_model else None)
    r = extract_sheet(args.path, args.sheet, args.weights, args.out, args.page, args.ocr, roboflow=roboflow)
    print(f"sheet {r['sheet']}: {len(r['assets'])} assets, {len(r['edges'])} connections "
          f"(text from {r['text_source']}) -> {args.out}/{r['sheet']}/")


if __name__ == "__main__":
    main()
