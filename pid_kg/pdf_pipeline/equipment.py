"""Equipment from tags: the free symbol model has no pump/tank/exchanger classes, so we find
equipment the way a person does: read an equipment tag (e.g. P4711), then take the drawn outline
that contains it or sits right next to it.

The tag pattern is the main thing to adapt per customer (their tag convention), see EQUIPMENT_TAG.
"""
import os
import re

import cv2
import numpy as np

# Default: 1-3 letters, optional dash, 2-5 digits, optional letter suffix: P4711, T-4750, HX101A.
# Instrument tags (PT4712.01), nozzle labels (N1) and line numbers do not match.
EQUIPMENT_TAG = r"^[A-Z]{1,3}-?\d{2,5}[A-Z]?$"
# Letter codes that look like tags but are not equipment (pipe sizes, pressure ratings, schedules)
MAX_SIZE = 30    # largest equipment outline, in letter heights (a tank drawn with small text measured 25.1;
                 # 40 let OCR runs grab whole pipe loops as equipment)
MAX_TEXTS = 6    # an outline holding more texts than this is a table or title block
NOT_EQUIPMENT = {"DN", "PN", "NPS", "SCH", "NB", "CL"}


def _rects_from_drawing(gray):
    """Bounding boxes of closed outlines (inner contours), e.g. a pump circle or a vessel body."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    binary = cv2.dilate(binary, np.ones((3, 3), np.uint8), iterations=1)   # close tiny gaps in outlines
    contours, _ = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(c) for c in contours]


def _gap(a, b):
    """Distance between two boxes [x0,y0,x1,y1] (0 if they overlap)."""
    dx = max(b[0] - a[2], a[0] - b[2], 0)
    dy = max(b[1] - a[3], a[1] - b[3], 0)
    return (dx * dx + dy * dy) ** 0.5


def _debug(tag, tb, msg):
    if os.environ.get("PID_DEBUG"):
        print(f"  equipment {tag} at {[round(v) for v in tb]}: {msg}")


def _inside(r, box, pad=2):
    """True if rectangle r lies (almost) entirely within a text box."""
    return r[0] >= box[0] - pad and r[1] >= box[1] - pad and r[2] <= box[2] + pad and r[3] <= box[3] + pad


def _in_table_cell(tb, text_h, rects):
    """True if the text sits in a table cell: a tight rectangle around it with a neighbouring cell of the same
    height right beside it. Equipment data tables repeat the tags; a tag printed there is not the equipment."""
    tol = 0.3 * text_h
    cells = [r for r in rects if r[0] <= tb[0] + 2 and r[1] <= tb[1] + 2 and r[2] >= tb[2] - 2 and r[3] >= tb[3] - 2
             and r[3] - r[1] <= 3 * text_h]
    for c in cells:
        for r in rects:
            if r is not c and r[2] - r[0] > text_h and abs(r[1] - c[1]) <= tol and abs(r[3] - c[3]) <= tol \
                    and min(abs(r[0] - c[2]), abs(c[0] - r[2])) <= tol:
                return True
    return False


def find_equipment(img_bgr, text_lines, tag_pattern=EQUIPMENT_TAG, existing_symbols=()):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    rects = [[x, y, x + rw, y + rh] for x, y, rw, rh in _rects_from_drawing(gray)]
    centers = [((t["box"][0] + t["box"][2]) / 2, (t["box"][1] + t["box"][3]) / 2) for t in text_lines]
    pattern = re.compile(tag_pattern)
    best = {}   # tag -> candidate
    for t in text_lines:
        tag = t["text"].replace(" ", "")
        if not pattern.match(tag) or re.match(r"[A-Z]+", tag).group(0) in NOT_EQUIPMENT:
            continue
        tb = t["box"]
        text_h = t.get("glyph_h") or max(min(tb[3] - tb[1], tb[2] - tb[0]), 1)   # letter height (also vertical text)
        if _in_table_cell(tb, text_h, rects):
            _debug(tag, tb, "inside a table cell, skipped")
            continue
        def text_count(r):
            return sum(1 for cx, cy in centers if r[0] <= cx <= r[2] and r[1] <= cy <= r[3])

        # outline pieces near the tag (a symbol is often cut into pieces by lines drawn inside it)
        pieces = []
        for r in rects:
            rw, rh = r[2] - r[0], r[3] - r[1]
            if min(rw, rh) < 0.4 * text_h or rw * rh > 0.15 * w * h or max(rw, rh) > MAX_SIZE * text_h:
                continue   # too small (letters) or too big (frame, whole pipe network)
            if text_count(r) > MAX_TEXTS:
                continue   # contains many texts: a table or title block, not equipment
            if any(_inside(r, t2["box"]) for t2 in text_lines):
                continue   # the outline of a letter or digit, not a drawn shape
            tag_inside = r[0] <= (tb[0] + tb[2]) / 2 <= r[2] and r[1] <= (tb[1] + tb[3]) / 2 <= r[3]
            if rw * rh > (10 * text_h) ** 2 and not tag_inside:
                continue   # a big region next to the tag is usually a loop of pipes, not the equipment
            pieces.append(r)
        near = sorted((_gap(tb, r), r) for r in pieces if _gap(tb, r) <= 4 * text_h)
        if not near:
            _debug(tag, tb, "no outline piece within reach")
            continue
        best_here = None
        for g0, core in near[:4]:   # try the few nearest pieces as starting points
            box = list(core)
            # a symbol made of pieces stays compact; a single big piece (e.g. a vessel body) may be larger
            limit = max(10 * text_h, core[2] - core[0], core[3] - core[1])
            grown = True
            while grown:   # join pieces that touch the outline found so far
                grown = False
                for r in pieces:
                    if _gap(r, box) > 0.25 * text_h:
                        continue
                    merged = [min(box[0], r[0]), min(box[1], r[1]), max(box[2], r[2]), max(box[3], r[3])]
                    if merged != box and max(merged[2] - merged[0], merged[3] - merged[1]) <= limit:
                        box, grown = merged, True
            if min(box[2] - box[0], box[3] - box[1]) < 1.6 * text_h or text_count(box) > MAX_TEXTS:
                _debug(tag, tb, f"outline {[round(v) for v in box]} too small or holds a table")
                continue
            rank = (box[2] - box[0]) * (box[3] - box[1]) / (1 + g0)
            if best_here is None or rank > best_here[0]:
                best_here = (rank, box, g0)
        if best_here is None:
            continue
        _, outline, g0 = best_here
        box = [min(outline[0], tb[0]), min(outline[1], tb[1]), max(outline[2], tb[2]), max(outline[3], tb[3])]  # include the tag
        size = (box[2] - box[0]) * (box[3] - box[1])
        score = size / (1 + g0)
        if tag not in best or score > best[tag]["score_rank"]:
            best[tag] = {"label": "Equipment/From tag", "box": [float(v) for v in box], "tag": tag,
                         "outline": [float(v) for v in outline],   # the drawn symbol only, without its tag
                         "score": 0.5, "score_rank": score, "source": "tag+outline"}
    out = []
    for item in best.values():
        item.pop("score_rank")
        out.append(item)
    return out
