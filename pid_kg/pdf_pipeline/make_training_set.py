"""Build a Roboflow training set for an equipment model from P&ID drawings (PDF/PNG/JPG).

Each drawing is cut into the same overlapping tiles the cloud route sends (roboflow_cloud.TILE / OVERLAP), so
the model is trained at the scale it will see. Equipment found from tags (equipment.py) is written as DRAFT
labels: check and correct them in Roboflow's annotation editor before training.

    python -m pdf_pipeline.make_training_set                      (drawings in training/drawings)
    python -m pdf_pipeline.make_training_set --valid sample_101.pdf
    python -m pdf_pipeline.make_training_set --skip C01V01-SAG.EX02.pdf:R001   (leave out a wrong draft label)

Output: training/dataset/{train,valid}/ with tiles + _annotations.coco.json, training/dataset.zip for upload,
and training/preview/ with the draft labels drawn on each drawing.
"""
import argparse
import glob
import json
import os
import re
import shutil

import cv2

from . import page_input
from .equipment import EQUIPMENT_TAG, find_equipment
from .roboflow_cloud import OVERLAP, TILE

# Equipment class from the tag's letter code; adapt to the customer's tag convention. Unknown codes -> 'equipment'.
TAG_CLASSES = [(r"^(HX|E|H|W)", "heat-exchanger"), (r"^(K|CP)", "compressor"), (r"^P", "pump"), (r"^T", "tank"),
               (r"^(V|D)", "vessel"), (r"^C", "column"), (r"^R", "reactor")]
CLASSES = ["pump", "tank", "vessel", "heat-exchanger", "column", "compressor", "reactor", "equipment"]
MIN_VISIBLE = 0.5   # a box cut by the tile edge is kept if at least this share of it is inside the tile


def class_of(tag):
    for pattern, name in TAG_CLASSES:
        if re.match(pattern, tag):
            return name
    return "equipment"


def tiles(h, w, tile=TILE, overlap=OVERLAP):
    step = int(tile * (1 - overlap))
    for y in range(0, max(h - tile, 0) + step, step):
        for x in range(0, max(w - tile, 0) + step, step):
            yield min(x, max(w - tile, 0)), min(y, max(h - tile, 0))


def build(drawings, out_dir, valid_names, force_ocr=False, skip=()):
    """skip: 'drawing.pdf:TAG' entries for draft labels that are wrong (e.g. valves tagged like equipment);
    draw those by hand in Roboflow instead."""
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    preview_dir = os.path.join(os.path.dirname(out_dir), "preview")
    os.makedirs(preview_dir, exist_ok=True)
    coco = {s: {"images": [], "annotations": [],
                "categories": [{"id": i + 1, "name": c, "supercategory": "equipment"} for i, c in enumerate(CLASSES)]}
            for s in ("train", "valid")}
    summary = []
    for path in drawings:
        name = os.path.splitext(os.path.basename(path))[0]
        split = "valid" if os.path.basename(path) in valid_names else "train"
        img, page, zoom = page_input.load_page(path)
        texts, src = page_input.read_text(img, page, zoom, force_ocr=force_ocr)
        found = find_equipment(img, texts, EQUIPMENT_TAG)
        labels = [(class_of(e["tag"]), e["outline"], e["tag"]) for e in found
                  if f"{os.path.basename(path)}:{e['tag']}" not in skip]
        preview = img.copy()
        for cls, (x0, y0, x1, y1), tag in labels:
            cv2.rectangle(preview, (int(x0), int(y0)), (int(x1), int(y1)), (0, 140, 255), 4)
            cv2.putText(preview, f"{cls} {tag}", (int(x0), max(int(y0) - 8, 20)), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                        (0, 140, 255), 3)
        cv2.imwrite(os.path.join(preview_dir, f"{name}.jpg"), preview)
        h, w = img.shape[:2]
        n_tiles = 0
        for x0, y0 in tiles(h, w):
            crop = img[y0:y0 + TILE, x0:x0 + TILE]
            th, tw = crop.shape[:2]
            file_name = f"{name}_x{x0}_y{y0}.jpg"
            os.makedirs(os.path.join(out_dir, split), exist_ok=True)
            cv2.imwrite(os.path.join(out_dir, split, file_name), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            data = coco[split]
            image_id = len(data["images"]) + 1
            data["images"].append({"id": image_id, "file_name": file_name, "width": tw, "height": th})
            for cls, (a, b, c, d), _ in labels:
                ca, cb, cc, cd = max(a, x0), max(b, y0), min(c, x0 + tw), min(d, y0 + th)
                if cc <= ca or cd <= cb or (cc - ca) * (cd - cb) < MIN_VISIBLE * (c - a) * (d - b):
                    continue
                data["annotations"].append({
                    "id": len(data["annotations"]) + 1, "image_id": image_id, "category_id": CLASSES.index(cls) + 1,
                    "bbox": [round(ca - x0, 1), round(cb - y0, 1), round(cc - ca, 1), round(cd - cb, 1)],
                    "area": round((cc - ca) * (cd - cb), 1), "iscrowd": 0})
            n_tiles += 1
        summary.append((os.path.basename(path), split, src, n_tiles, sorted(f"{t}={c}" for c, _, t in labels)))
    for split, data in coco.items():
        if data["images"]:
            with open(os.path.join(out_dir, split, "_annotations.coco.json"), "w", encoding="utf-8") as f:
                json.dump(data, f)
    zip_path = shutil.make_archive(out_dir, "zip", out_dir)
    return summary, coco, zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drawings", default="training/drawings")
    ap.add_argument("--out", default="training/dataset")
    ap.add_argument("--valid", nargs="*", help="file names to put in the validation split "
                                               "(default: every 5th drawing, at least one)")
    ap.add_argument("--ocr", action="store_true", help="read tags with OCR instead of the PDF text layer")
    ap.add_argument("--skip", nargs="*", default=[], help="wrong draft labels to leave out, as drawing.pdf:TAG")
    args = ap.parse_args()
    drawings = sorted(p for p in glob.glob(os.path.join(args.drawings, "*"))
                      if p.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")))
    if not drawings:
        raise SystemExit(f"No drawings in {args.drawings}")
    valid = set(args.valid) if args.valid else {os.path.basename(p) for p in drawings[::5]}
    summary, coco, zip_path = build(drawings, args.out, valid, args.ocr, set(args.skip))
    for name, split, src, n, labels in summary:
        print(f"  {name:28s} {split:5s} text from {src:15s} {n:3d} tiles  draft labels: {', '.join(labels) or '-'}")
    for split, data in coco.items():
        print(f"{split}: {len(data['images'])} tiles, {len(data['annotations'])} boxes")
    print(f"zip for upload: {zip_path}   previews: {os.path.join(os.path.dirname(args.out), 'preview')}")


if __name__ == "__main__":
    main()
