"""
The vector PDF that COMOS exports for a P&ID sheet as an auditor of the pipe links in the DEXPI file of that sheet.

DEXPI stays the backbone. The PDF only checks pipe connectivity: which pairs of plant items the drawn pipe lines
join, compared with which pairs the DEXPI pipe segments join. Generic: everything is derived from the two files
(no tags, IDs, sheet names, line widths or coordinates in here).

  1. coordinate_map / tag_match   DEXPI mm -> PDF points, and a sanity check that the PDF is this sheet: tags in the
                                  PDF text layer must sit where the DEXPI file puts their objects
  2. objects                      equipment, piping components and off-page connectors as PDF boxes
  3. line_segments / calibrate    the stroke width(s) the pipes are drawn in, found from the DEXPI pipe routes
  4. drawn_connections            pairs of objects the drawn pipe runs join (Shapely + NetworkX)
  5. dexpi_connections            pairs of objects the DEXPI pipe segments join (dexpi_links.segment_chain)
  6. check                        all of the above: confirmed, drawn_only, dexpi_only

    python -m crosscheck.pdf_vs_dexpi sheet.pdf sheet-dexpi.xml [overlay.png]
"""
import argparse
import itertools
import json
import logging
import sys
from collections import Counter

import networkx as nx
import pymupdf as fitz
from defusedxml import ElementTree as ET
from shapely.geometry import LineString, Point, box as shape_box
from shapely.strtree import STRtree

from dexpi_links import full_label, segment_chain
from plant_loader import read_xml_text

log = logging.getLogger(__name__)

OBJECT_TAGS = ("Equipment", "PipingComponent", "PipeOffPageConnector")
NOZZLE_TAGS = ("Nozzle", "Nozzles")       # COMOS writes <Nozzles>, which the XSD doesn't have
TAG_TOL_MM = 15.0       # a tag word this close to its object's outline counts as found where DEXPI puts it
TAG_MIN_SCORE = 0.5     # below this share of tags found in place, the PDF is another sheet / revision / scale
ROUTE_TOL_PT = 1.5      # a drawn segment end this close to a DEXPI pipe route point matches it
WIDTH_KEEP = 0.25       # pipe widths kept: every width scoring at least this share of the best one
JOIN_TOL_PT = 1.5       # segment ends this close to another segment join it (end to end, or a T-junction)
BOX_MARGIN_PT = 2.0     # a pipe run ending this close to an object's box connects it
TOUCH_PT = 1.0          # two object boxes this close are drawn against each other


class PdfMismatch(ValueError):
    """The PDF is not a drawing of this DEXPI sheet. No results are produced."""

    def __init__(self, tags):
        self.tags = tags
        super().__init__(f"PDF does not match this DEXPI sheet (different sheet, revision or scale): "
                         f"{tags['matched']} of {tags['total']} tags ({tags['score']:.0%}) found where the DEXPI "
                         f"file puts them, at least {TAG_MIN_SCORE:.0%} needed")


# --------------------------------------------------------------------------- 1. coordinates and sanity check

class Mapping:
    """DEXPI drawing coordinates (mm, y up) -> PDF page coordinates (points, y down)."""

    def __init__(self, page, root):
        drawing_max = root.find("Drawing/Extent/Max")
        if drawing_max is None or not float(drawing_max.get("X") or 0):
            raise ValueError("the DEXPI file has no Drawing/Extent/Max, so its drawing can't be placed on the PDF")
        self.scale = page.rect.width / float(drawing_max.get("X"))     # points per mm
        self.height = page.rect.height

    def point(self, x, y):
        return float(x) * self.scale, self.height - float(y) * self.scale

    def box(self, el):
        """The element's Extent as a PDF box (x0, y0, x1, y1), or None."""
        lo, hi = el.find("Extent/Min"), el.find("Extent/Max")
        if lo is None or hi is None:
            return None
        x0, y1 = self.point(lo.get("X"), lo.get("Y"))
        x1, y0 = self.point(hi.get("X"), hi.get("Y"))
        return x0, y0, x1, y1


def coordinate_map(page, root):
    return Mapping(page, root)


def catalogue_of(root):
    """Everything inside <ShapeCatalogue>: symbol templates drawn at the origin, not plant items."""
    return {el for sc in root.iter("ShapeCatalogue") for el in sc.iter()}


def tag_match(page, root, m, catalogue=None):
    """Share of the DEXPI objects with a TagName whose tag is one word, once, in the PDF text layer, and lies within
    TAG_TOL_MM of the object's outline: {score, matched, total, missed: [tag]}."""
    catalogue = catalogue_of(root) if catalogue is None else catalogue
    words = page.get_text("words")
    count = Counter(w[4] for w in words)
    where = {w[4]: shape_box(*w[:4]) for w in words if count[w[4]] == 1}
    total, matched, missed = 0, 0, []
    for el in root.iter():
        tag = el.get("TagName")
        if el in catalogue or not tag or tag not in where:
            continue
        b = m.box(el)
        if b is None:
            continue
        total += 1
        if shape_box(*b).distance(where[tag]) <= TAG_TOL_MM * m.scale:
            matched += 1
        else:
            missed.append(tag)
    return {"score": matched / total if total else 0.0, "matched": matched, "total": total, "missed": missed}


# --------------------------------------------------------------------------- 2. objects

def objects(root, m, catalogue=None, parent=None):
    """XML ID -> {name, cls, fullLabel, tag, box} for every equipment, piping component and off-page connector placed
    on the drawing. Equipment inside other equipment is left out (it is part of its parent); an equipment's box is
    grown to cover its nozzles."""
    catalogue = catalogue_of(root) if catalogue is None else catalogue
    parent = parent or {c: p for p in root.iter() for c in p}
    objs = {}
    for el in root.iter():
        if el in catalogue or el.tag not in OBJECT_TAGS or not el.get("ID"):
            continue
        if el.tag == "Equipment" and parent.get(el) is not None and parent[el].tag == "Equipment":
            continue
        b = m.box(el)
        if b is not None:
            objs[el.get("ID")] = {"name": el.get("TagName") or full_label(el) or el.get("ID"),
                                  "cls": el.get("ComponentClass") or "", "fullLabel": full_label(el) or "",
                                  "tag": el.get("TagName") or "", "box": b}
    for el in root.iter():
        own = parent.get(el)
        if el.tag in NOZZLE_TAGS and own is not None and own.get("ID") in objs:
            b = m.box(el)
            if b is not None:
                o = objs[own.get("ID")]["box"]
                objs[own.get("ID")]["box"] = (min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3]))
    return objs


# --------------------------------------------------------------------------- 3. pipe line style

def line_segments(page):
    """Solid (not dashed) straight segments on the page: [((x0, y0), (x1, y1), width)]."""
    out = []
    for d in page.get_drawings():
        if d.get("dashes") not in (None, "[] 0", ""):
            continue
        width = round(d.get("width") or 0, 2)
        for it in d["items"]:
            if it[0] == "l":
                out.append(((it[1].x, it[1].y), (it[2].x, it[2].y), width))
    return out


def calibrate(segments, root, m):
    """The stroke width(s) pipes are drawn in: per width, how many segment ends lie on a DEXPI pipe route point
    (PipingNetworkSegment coordinates). Every width scoring at least WIDTH_KEEP of the best is kept, so main and minor
    lines drawn in different widths both count. Returns {scores: {width: n}, widths: [kept, best first]}."""
    route = [Point(m.point(c.get("X"), c.get("Y")))
             for seg in root.iter("PipingNetworkSegment") for c in seg.iter("Coordinate")]
    tree = STRtree(route)
    scores = Counter()
    for a, b, width in segments:
        scores[width] += 0
        for x, y in (a, b):
            if len(tree.query(shape_box(x - ROUTE_TOL_PT, y - ROUTE_TOL_PT, x + ROUTE_TOL_PT, y + ROUTE_TOL_PT))):
                scores[width] += 1
    best = max(scores.values(), default=0)
    widths = sorted((w for w, n in scores.items() if best and n >= WIDTH_KEEP * best), key=lambda w: -scores[w])
    for w, n in sorted(scores.items()):
        log.info("pipe width %.2f pt: %d segment ends on DEXPI pipe routes%s", w, n, " (kept)" if w in widths else "")
    return {"scores": dict(sorted(scores.items())), "widths": widths}


# --------------------------------------------------------------------------- 4. drawn connectivity

def _pad(b, d):
    return shape_box(b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def drawn_connections(pipes, objs):
    """Pairs of object IDs the drawing joins, and the number of drawn pipe runs.

    pipes: [((x0, y0), (x1, y1))] in the pipe width(s). Segments join where an end touches another segment (end to
    end, or a T-junction within JOIN_TOL_PT), unless that point is inside an object (lines crossing a symbol are not
    a junction). Each joined run connects every object whose box (BOX_MARGIN_PT around) one of its ends reaches.
    Objects drawn against each other (boxes within TOUCH_PT, e.g. a reducer on a pump) are joined too, unless one has
    no ComponentClass: untyped items such as COMOS motors are drawn on top of their equipment (composition)."""
    ids = list(objs)
    boxes = [shape_box(*objs[i]["box"]) for i in ids]
    obj_tree = STRtree(boxes)
    lines = [LineString([a, b]) for a, b in pipes]
    line_tree = STRtree(lines)

    runs = nx.Graph()
    runs.add_nodes_from(range(len(pipes)))
    for i, ends in enumerate(pipes):
        for q in ends:
            p = Point(q)
            if len(obj_tree.query(p, predicate="intersects")):
                continue
            runs.add_edges_from((i, int(j)) for j in line_tree.query(p, predicate="dwithin", distance=JOIN_TOL_PT)
                                if j != i)
    pairs = set()
    components = list(nx.connected_components(runs))
    for run in components:
        touched = set()
        for i in run:
            for x, y in pipes[i]:
                touched.update(ids[k] for k in obj_tree.query(_pad((x, y, x, y), BOX_MARGIN_PT),
                                                              predicate="intersects"))
        pairs.update(frozenset(p) for p in itertools.combinations(sorted(touched), 2))
    for k, a in enumerate(ids):
        if not objs[a]["cls"]:
            continue
        for j in obj_tree.query(_pad(objs[a]["box"], TOUCH_PT), predicate="intersects"):
            b = ids[j]
            if b != a and objs[b]["cls"]:
                pairs.add(frozenset((a, b)))
    return pairs, len(components)


# --------------------------------------------------------------------------- 5. DEXPI connectivity

def dexpi_connections(root, objs, parent=None):
    """Pairs of object IDs the DEXPI pipe segments join: FromID -> inline items -> ToID per PipingNetworkSegment
    (dexpi_links rule 5), any element mapped to the object that holds it (a nozzle to its equipment)."""
    parent = parent or {c: p for p in root.iter() for c in p}
    catalogue = catalogue_of(root)
    by_id = {el.get("ID"): el for el in root.iter() if el.get("ID") and el not in catalogue}

    def owner(xml_id):
        el = by_id.get(xml_id) if xml_id else None
        while el is not None and el.get("ID") not in objs:
            el = parent.get(el)
        return el.get("ID") if el is not None else None

    pairs = set()
    for seg in root.iter("PipingNetworkSegment"):
        if seg in catalogue:
            continue
        chain = segment_chain(seg, owner)
        pairs.update(frozenset(p) for p in zip(chain, chain[1:]))
    return pairs


# --------------------------------------------------------------------------- 6. the whole check

def load(pdf_path, dexpi_path, page_number=0):
    doc = fitz.open(pdf_path)
    return doc, doc[page_number], ET.fromstring(read_xml_text(dexpi_path))


def check(pdf_path, dexpi_path, page_number=0):
    """Cross-check one sheet. Raises PdfMismatch when the PDF is not this sheet. Returns
    {tag_match, calibration, objects, segments, runs, confirmed, drawn_only, dexpi_only}; the three lists hold sorted
    (XML ID, XML ID) pairs, objects describes each ID."""
    doc, page, root = load(pdf_path, dexpi_path, page_number)
    try:
        m = coordinate_map(page, root)
        catalogue = catalogue_of(root)
        tags = tag_match(page, root, m, catalogue)
        log.info("tags found in place: %d of %d (%.0f%%)", tags["matched"], tags["total"], 100 * tags["score"])
        if tags["score"] < TAG_MIN_SCORE:
            raise PdfMismatch(tags)
        parent = {c: p for p in root.iter() for c in p}
        objs = objects(root, m, catalogue, parent)
        segments = line_segments(page)
        cal = calibrate(segments, root, m)
        pipes = [(a, b) for a, b, w in segments if w in cal["widths"]]
        drawn, runs = drawn_connections(pipes, objs)
        logic = dexpi_connections(root, objs, parent)
    finally:
        doc.close()
    as_list = lambda pairs: sorted(tuple(sorted(p)) for p in pairs)
    return {"tag_match": tags, "calibration": cal, "objects": objs, "segments": len(pipes), "runs": runs,
            "confirmed": as_list(drawn & logic), "drawn_only": as_list(drawn - logic),
            "dexpi_only": as_list(logic - drawn)}


def overlay_png(pdf_path, result, page_number=0, dpi=110):
    """The PDF page with the result drawn on it: confirmed links green, drawn-only links (candidates) red, DEXPI-only
    links orange, and the boxes of the objects they join. PNG bytes."""
    objs = result["objects"]
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        centre = lambda o: fitz.Point((objs[o]["box"][0] + objs[o]["box"][2]) / 2,
                                      (objs[o]["box"][1] + objs[o]["box"][3]) / 2)
        layers = [(result["confirmed"], (0, .6, 0), 3), (result["dexpi_only"], (1, .5, 0), 4),
                  (result["drawn_only"], (.9, 0, 0), 6)]
        for pairs, colour, width in layers:
            for a, b in pairs:
                page.draw_line(centre(a), centre(b), color=colour, width=width)
                for o in (a, b):
                    page.draw_rect(fitz.Rect(objs[o]["box"]), color=colour, width=1.5)
        return page.get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def describe(result, pair):
    return " — ".join(f"{result['objects'][x]['name']} [{result['objects'][x]['cls']}, "
                      f"{result['objects'][x]['fullLabel']}]" for x in pair)


def main():
    ap = argparse.ArgumentParser(description="Cross-check DEXPI pipe links against the vector PDF of the same sheet.")
    ap.add_argument("pdf")
    ap.add_argument("dexpi")
    ap.add_argument("overlay", nargs="?", help="write the overlay picture (PNG) here")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        res = check(args.pdf, args.dexpi, args.page - 1)
    except PdfMismatch as exc:
        print(exc)
        return 2
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "objects"}, indent=2, default=str))
    else:
        print(f"pipe width(s): {', '.join(f'{w:g}' for w in res['calibration']['widths'])} pt, {res['segments']} "
              f"segments in {res['runs']} drawn runs; {len(res['objects'])} DEXPI objects on the drawing")
        print(f"confirmed: {len(res['confirmed'])}   drawn only: {len(res['drawn_only'])}   "
              f"DEXPI only: {len(res['dexpi_only'])}")
        for title, key in (("DRAWN BUT MISSING IN DEXPI", "drawn_only"), ("IN DEXPI BUT NOT DRAWN", "dexpi_only")):
            print(f"\n{title}:")
            for p in res[key]:
                print("  ", describe(res, p))
    if args.overlay:
        with open(args.overlay, "wb") as fh:
            fh.write(overlay_png(args.pdf, res, args.page - 1))
        print("overlay ->", args.overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
