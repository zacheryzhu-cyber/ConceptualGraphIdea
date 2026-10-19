"""
Apply a sheet's PDF cross-check (pdf_vs_dexpi.check) to the sheet's complete graph in memory (plant_model.py),
during loading, before the conceptual level is written to Neo4j.

    confirmed    the sheet's PIPE between the two assets gets pdfCheck = 'confirmed'
    drawn_only   a PIPE the loader already made (e.g. proteus_topology step 2b: items drawn against each other) gets
                 pdfCheck = 'confirmed' and pdfNote; otherwise a new PIPE {source: 'drawing', status: 'candidate',
                 sheet, pdfFile, pdfCheck: 'drawn_only'}
    dexpi_only   the sheet's PIPE between the two assets gets pdfCheck = 'not_drawn'

Candidates stay out of Neo4j until approved. Review decisions are kept in sources/decisions.json (plant_store.py),
keyed by plant_model.pair_key, so a rejected pair is not proposed again and an approved one comes back approved after
a reload or a rebuild. run() returns the summary that goes into the snapshot and onto the Sheet node.
"""
import json
import os
import re
from datetime import datetime

import plant_model as pm
from crosscheck import pdf_vs_dexpi as pv

SHEET_PROPS = ("pdfStatus", "pdfMessage", "pdfFile", "pdfPath", "pdfDexpiPath", "pdfTagScore", "pdfTagsMatched",
               "pdfTagsTotal", "pdfWidths", "pdfCalibration", "pdfConfirmed", "pdfDrawnOnly", "pdfNotDrawn",
               "pdfAlreadyLinked", "pdfRejectedSkipped", "pdfUnmapped", "pdfDrawnOnlyPairs", "pdfNotDrawnPairs",
               "pdfChecked")
NOTE_ALREADY_LINKED = "drawn; not on a DEXPI pipe segment, already linked by the loader"
pair_key = pm.pair_key


def uid_map(G, sheet, objects):
    """XML ID -> asset uid for the checked objects: by full label (plant-wide identity), else the sheet's own node for
    that ID, else the sheet's asset made from that ID (proteusId) when nothing says it is another item."""
    by_full = {n["fullTag"]: u for u, n in G.nodes(data=True) if n.get("fullTag")}
    by_pid = {}
    for u, n in G.nodes(data=True):
        if n.get("proteusId"):
            by_pid.setdefault(n["proteusId"], []).append((u, n.get("fullTag")))
    out = {}
    for xid, o in objects.items():
        uid = by_full.get(o["fullLabel"]) or (f"{sheet}::{xid}" if f"{sheet}::{xid}" in G else None)
        if uid is None:
            same = [u for u, full in by_pid.get(xid, []) if not full or full == o["fullLabel"]]
            uid = same[0] if len(same) == 1 else None
        if uid:
            out[xid] = uid
    return out


def _mark(G, a, b, check, note=None):
    """Mark the sheet's DEXPI PIPE(s) between a and b; how many were marked."""
    n = 0
    for _, _, _, d in pm.edges_between(G, a, b, "PIPE"):
        if d.get("source") != "drawing":
            d["pdfCheck"] = check
            if note:
                d["pdfNote"] = note
            n += 1
    return n


def _direction(G, a, b):
    """(src, dst) for a new pipe between a and b: the drawing has no direction, so follow the pipes already there
    (an item that only feeds pipes starts a run, one that only receives them ends one); else a -> b."""
    def flow(x):
        out = sum(d["type"] == "PIPE" and d.get("status") != "candidate" for _, _, d in G.out_edges(x, data=True))
        inn = sum(d["type"] == "PIPE" and d.get("status") != "candidate" for _, _, d in G.in_edges(x, data=True))
        return out, inn
    (ao, ai), (bo, bi) = flow(a), flow(b)
    if (ao and not ai) or (bi and not bo):
        return b, a
    return a, b


def run(G, sheet, dexpi_path, pdf_path, decisions, pdf_name=None, page_number=0):
    """Check the sheet graph G against its PDF and write the results into G. Returns the summary dict (pdf* keys).
    A PDF of another sheet gives pdfStatus 'mismatch' and changes nothing in G."""
    pdf_name = pdf_name or os.path.basename(pdf_path)
    base = {"pdfFile": pdf_name, "pdfPath": os.path.abspath(pdf_path), "pdfDexpiPath": os.path.abspath(dexpi_path),
            "pdfChecked": datetime.now().isoformat(timespec="seconds")}
    try:
        res = pv.check(pdf_path, dexpi_path, page_number)
    except pv.PdfMismatch as exc:
        return {**base, "pdfStatus": "mismatch", "pdfMessage": str(exc), "pdfTagScore": exc.tags["score"],
                "pdfTagsMatched": exc.tags["matched"], "pdfTagsTotal": exc.tags["total"]}
    rejected = {k for k, v in decisions.items() if v.get("decision") == "rejected"}
    uid = uid_map(G, sheet, res["objects"])
    unmapped = 0

    def uids(pairs):
        nonlocal unmapped
        out = []
        for a, b in pairs:
            if a in uid and b in uid and uid[a] != uid[b]:
                out.append((uid[a], uid[b]))
            else:
                unmapped += 1
        return out

    for a, b in uids(res["confirmed"]):
        _mark(G, a, b, "confirmed")
    not_drawn = uids(res["dexpi_only"])
    for a, b in not_drawn:
        _mark(G, a, b, "not_drawn")
    drawn_only, already, skipped = uids(res["drawn_only"]), 0, 0
    for a, b in drawn_only:
        if _mark(G, a, b, "confirmed", NOTE_ALREADY_LINKED):
            already += 1
        elif pair_key(a, b) in rejected:
            skipped += 1
        elif not pm.edges_between(G, a, b, "PIPE"):   # not proposed twice (a re-run on the same graph)
            src, dst = _direction(G, a, b)
            pm.add_edge(G, src, dst, "PIPE", source="drawing", status="candidate", sheet=sheet, pdfFile=pdf_name,
                        dexpiType="Pipe", pdfCheck="drawn_only")
    return {**base, "pdfStatus": "ok", "pdfTagScore": res["tag_match"]["score"],
            "pdfTagsMatched": res["tag_match"]["matched"], "pdfTagsTotal": res["tag_match"]["total"],
            "pdfWidths": res["calibration"]["widths"],
            "pdfCalibration": json.dumps({f"{w:g}": n for w, n in res["calibration"]["scores"].items()}),
            "pdfConfirmed": len(res["confirmed"]), "pdfDrawnOnly": len(res["drawn_only"]),
            "pdfNotDrawn": len(res["dexpi_only"]), "pdfAlreadyLinked": already, "pdfRejectedSkipped": skipped,
            "pdfUnmapped": unmapped, "pdfDrawnOnlyPairs": json.dumps(drawn_only),
            "pdfNotDrawnPairs": json.dumps(not_drawn)}


def summary_line(s):
    """'PDF check: 34 confirmed · 2 drawn only · 0 not drawn' (+ what happened to the drawn-only pairs)."""
    if not s:
        return "Loaded without drawing check"
    if s.get("pdfStatus") == "mismatch":
        return f"PDF check: {s['pdfMessage']}"
    line = f"PDF check: {s['pdfConfirmed']} confirmed · {s['pdfDrawnOnly']} drawn only · {s['pdfNotDrawn']} not drawn"
    notes = []
    if s.get("pdfAlreadyLinked"):
        notes.append(f"{s['pdfAlreadyLinked']} already linked by the loader")
    if s.get("pdfRejectedSkipped"):
        notes.append(f"{s['pdfRejectedSkipped']} rejected before")
    open_ = s["pdfDrawnOnly"] - s.get("pdfAlreadyLinked", 0) - s.get("pdfRejectedSkipped", 0)
    if open_ > 0:
        notes.append(f"{open_} candidate{'s' if open_ != 1 else ''} to review in tab 6")
    return line + (f" ({', '.join(notes)})" if notes else "")


# --------------------------------------------------------------------------- pairing PDFs with DEXPI files

def drawing_name(dexpi_head):
    """The DEXPI <Drawing Name="..."> from the start of a file (bytes), or ''."""
    m = re.search(rb'<Drawing\b[^>]*\bName="([^"]*)"', dexpi_head)
    return m.group(1).decode("utf-8", "replace") if m else ""


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def sheet_keys(sheet, file_name, drawing):
    """Names a PDF of this sheet may carry: the sheet name, its drawing number, the sheet name without the drawing
    number (its label, e.g. the unit), and the COMOS object ID in the DEXPI file name."""
    keys = {sheet, drawing, file_name}
    if drawing and _norm(sheet).endswith(_norm(drawing)):
        keys.add(re.sub(re.escape(drawing) + r"$", "", sheet))
    keys.update(re.findall(r"\(([A-Z0-9]{6,})\)", file_name))
    return {k for k in map(_norm, keys) if len(k) >= 3}


def pair_pdfs(sheets, pdf_names):
    """{sheet: pdf name or ''}: a PDF goes with a sheet when its file name contains one of the sheet's keys that no
    other sheet in `sheets` shares, and no other PDF does the same for that sheet. sheets: [{sheet, file, drawing}]."""
    keys = {s["sheet"]: sheet_keys(s["sheet"], s["file"], s.get("drawing", "")) for s in sheets}
    pairs = {}
    for s, own in keys.items():
        others = set().union(*(k for o, k in keys.items() if o != s))
        own = {k for k in own if not any(k in o for o in others)}
        found = [p for p in pdf_names if any(k in _norm(os.path.splitext(p)[0]) for k in own)]
        pairs[s] = found[0] if len(found) == 1 else ""
    return pairs
