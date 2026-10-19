"""Step 0 + 2: turn a PDF page or image into a high-resolution picture, and read its text.

Text comes from the PDF's own text layer when there is one (exact, for PDFs exported from CAD);
otherwise from local OCR (RapidOCR, runs offline on the CPU).
"""
import os

import cv2
import numpy as np
import pymupdf

TARGET_LONG_SIDE = 5000   # pixels; P&IDs need high resolution for small symbols and text


def load_page(path, page_number=0):
    """Return (image_bgr, pdf_page_or_None, zoom). zoom converts PDF points to image pixels."""
    if path.lower().endswith(".pdf"):
        doc = pymupdf.open(path)
        page = doc[page_number]
        zoom = TARGET_LONG_SIDE / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        return img, page, zoom
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot read image {path}")
    return img, None, 1.0


def pdf_text_lines(page, zoom):
    """Text lines from the PDF text layer, in image pixels. Empty list for scanned PDFs.
    glyph_h is the height of the letters (the line box also includes line spacing)."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            x0 = min(s["bbox"][0] for s in spans); y0 = min(s["bbox"][1] for s in spans)
            x1 = max(s["bbox"][2] for s in spans); y1 = max(s["bbox"][3] for s in spans)
            text = " ".join(s["text"].strip() for s in spans)
            size = max(s["size"] for s in spans)
            out.append({"text": text, "box": [x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom],
                        "glyph_h": size * 0.7 * zoom})
    return out


_ocr_engine = None


def ocr_text_lines(img):
    """Local OCR with RapidOCR (offline, CPU). Returns text lines with pixel boxes."""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    result, _ = _ocr_engine(img)
    out = []
    for quad, text, score in result or []:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        out.append({"text": text.strip(), "box": [min(xs), min(ys), max(xs), max(ys)], "score": float(score)})
    return out


def read_text(img, page, zoom, force_ocr=False):
    """Prefer the PDF text layer; fall back to OCR. Returns (lines, source)."""
    if page is not None and not force_ocr:
        lines = pdf_text_lines(page, zoom)
        if len(lines) >= 5:
            return lines, "pdf-text-layer"
    return ocr_text_lines(img), "ocr"
