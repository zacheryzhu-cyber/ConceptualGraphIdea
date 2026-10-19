"""Measure the PDF pipeline on a P&ID whose right answer is known.

It draws the DEXPI sample (C01V04-VER.EX01.xml) as a PDF, extracts it with the PDF pipeline, and
compares which equipment is connected to which against the exact DEXPI data.

    python -m pdf_pipeline.test_on_sample --weights models/rfdetr_pid.pth
    python -m pdf_pipeline.test_on_sample --weights models/rfdetr_pid.pth --ocr   (simulate a scanned PDF)
"""
import argparse
import os
from collections import defaultdict, deque

import pymupdf

import plant_loader as pl

FLOW_RELS = {"PIPE", "DIRECT_PIPING_CONNECTION"}


def make_sample_pdf(xml="C01V04-VER.EX01.xml", out="test_pdf/sample_101.pdf"):
    from pydexpi.loaders import ProteusSerializer
    from pydexpi.loaders.svg_loader import DrawDiagram
    os.makedirs(os.path.dirname(out), exist_ok=True)
    model = ProteusSerializer().load(os.path.dirname(os.path.abspath(xml)), os.path.basename(xml))
    svg_path = out.replace(".pdf", ".svg")
    DrawDiagram(model.diagram, padding=5.0, pretty=False).save_svg("sample", svg_path)
    # The sample contains a few invisible shapes scaled to exactly zero; PyMuPDF then silently drops
    # the rest of the drawing. A near-zero scale keeps them invisible and the conversion complete.
    with open(svg_path, encoding="utf-8") as f:
        text = f.read().replace("scale(0.00,0.00)", "scale(0.001,0.001)")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(text)
    svg = pymupdf.open(svg_path)
    pdf = pymupdf.open("pdf", svg.convert_to_pdf())
    pdf.save(out)
    return out


def equipment_links(assets, edges, flow_only=True):
    """Pairs of equipment connected through non-equipment items (valves, fittings, instruments)."""
    eq = {a["uid"]: a["name"] for a in assets if a["category"] == "Equipment"}
    adj = defaultdict(set)
    for e in edges:
        if flow_only and e["rel"] not in FLOW_RELS:
            continue
        adj[e["src"]].add(e["dst"])
        adj[e["dst"]].add(e["src"])
    pairs = set()
    for start in eq:
        seen, queue = {start}, deque([start])
        while queue:
            n = queue.popleft()
            for m in adj[n]:
                if m in seen:
                    continue
                seen.add(m)
                if m in eq:
                    pairs.add(frozenset((eq[start], eq[m])))
                else:
                    queue.append(m)
    return pairs, set(eq.values())


def score(truth_pairs, truth_eq, found_pairs, found_eq):
    tp = truth_pairs & found_pairs
    eq_hit = truth_eq & found_eq
    print(f"  Equipment found:   {len(eq_hit)}/{len(truth_eq)}  {sorted(eq_hit)}"
          + (f"   missed {sorted(truth_eq - found_eq)}" if truth_eq - found_eq else "")
          + (f"   extra {sorted(found_eq - truth_eq)}" if found_eq - truth_eq else ""))
    print(f"  Equipment links:   {len(tp)}/{len(truth_pairs)} found"
          f"   (recall {len(tp) / max(len(truth_pairs), 1):.0%},"
          f" precision {len(tp) / max(len(found_pairs), 1):.0%})")
    fmt = lambda s: sorted("–".join(sorted(p)) for p in s)
    print(f"    correct: {fmt(tp)}")
    if truth_pairs - found_pairs:
        print(f"    missed:  {fmt(truth_pairs - found_pairs)}")
    if found_pairs - truth_pairs:
        print(f"    wrong:   {fmt(found_pairs - truth_pairs)}")
    return len(tp), len(truth_pairs), len(found_pairs)


def main():
    from pdf_pipeline.extract import extract_sheet
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="models/rfdetr_pid.pth")
    ap.add_argument("--ocr", action="store_true", help="ignore the PDF text layer (simulates a scan)")
    ap.add_argument("--xml", default="C01V04-VER.EX01.xml")
    ap.add_argument("--roboflow-model", help="also run this Roboflow-hosted model (uploads the drawing); "
                                             "API key from the ROBOFLOW_API_KEY environment variable")
    args = ap.parse_args()
    roboflow = ({"model_id": args.roboflow_model, "api_key": os.environ.get("ROBOFLOW_API_KEY")}
                if args.roboflow_model else None)

    pdf = make_sample_pdf(args.xml)
    truth_assets, truth_edges, _ = pl.parse_sheet(args.xml, "truth")
    truth_pairs, truth_eq = equipment_links(truth_assets, truth_edges)
    print(f"Ground truth from DEXPI: {len(truth_eq)} equipment, {len(truth_pairs)} equipment-to-equipment links")

    r = extract_sheet(pdf, "sample_101", args.weights, out_dir="test_pdf/output", force_ocr=args.ocr,
                      roboflow=roboflow)
    print(f"Extracted with text from {r['text_source']}: {r['symbols_found']} symbols, "
          f"{len(r['assets'])} assets, {len(r['edges'])} connections"
          + (f" (Roboflow {r['roboflow_model']}: {r['roboflow_symbols']} symbols)" if roboflow else ""))
    for a in r["assets"]:
        if a["category"] == "Equipment":
            print(f"    equipment {a['name']:10s} type {a['type']}")
    found_pairs, found_eq = equipment_links(r["assets"], r["edges"])
    score(truth_pairs, truth_eq, found_pairs, found_eq)
    print(f"  Overview picture: {r['overview_image']}")


if __name__ == "__main__":
    main()
