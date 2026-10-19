"""
Write a sheet's PDF cross-check (pdf_vs_dexpi.check) into the plant graph, and review its candidates.

    confirmed    the sheet's PIPE between the two assets gets pdfCheck = 'confirmed'
    drawn_only   a PIPE the loader already made (e.g. from the drawing geometry) gets pdfCheck = 'confirmed' and
                 pdfNote; otherwise a new PIPE {source: 'drawing', status: 'candidate', sheet, pdfFile}, left out of
                 the views until approved (or the Graph tab's "Show drawing candidates" is ticked)
    dexpi_only   the sheet's PIPE between the two assets gets pdfCheck = 'not_drawn'

The summary goes on the Sheet node (pdf* properties). Re-running replaces the sheet's previous results, so links are
never duplicated. Removing the sheet removes its candidates and pdfCheck properties (they carry the sheet, like every
link of a sheet). Review decisions (Sheet.pdfApproved / pdfRejected, pair keys "uid|uid") are kept when the sheet is
reloaded (plant_loader.KEEP_SHEET_PROPS): an approved pair comes back approved, a rejected one is not proposed again.
"""
import json
import os
import re
from datetime import datetime

from crosscheck import pdf_vs_dexpi as pv

SHEET_PROPS = ("pdfStatus", "pdfMessage", "pdfFile", "pdfPath", "pdfDexpiPath", "pdfTagScore", "pdfTagsMatched",
               "pdfTagsTotal", "pdfWidths", "pdfCalibration", "pdfConfirmed", "pdfDrawnOnly", "pdfNotDrawn",
               "pdfAlreadyLinked", "pdfRejectedSkipped", "pdfUnmapped", "pdfDrawnOnlyPairs", "pdfNotDrawnPairs",
               "pdfChecked")
NOTE_ALREADY_LINKED = "drawn; not on a DEXPI pipe segment, already linked by the loader"


def pair_key(a, b):
    return "|".join(sorted((a, b)))


def clear(db, sheet):
    """Remove a sheet's previous check: its candidates, pdfCheck marks and summary (not the review decisions)."""
    db.run("MATCH ()-[r:PIPE {source: 'drawing'}]->() WHERE r.sheet = $sheet DELETE r", sheet=sheet)
    db.run("MATCH ()-[r]->() WHERE r.sheet = $sheet AND r.pdfCheck IS NOT NULL REMOVE r.pdfCheck, r.pdfNote",
           sheet=sheet)
    db.run("MATCH (s:Sheet {name: $sheet}) SET " + ", ".join(f"s.{p} = null" for p in SHEET_PROPS), sheet=sheet)


def uid_map(db, sheet, objects):
    """XML ID -> asset uid for the checked objects: by full label (plant-wide identity), else the sheet's own node for
    that ID, else the sheet's asset made from that ID (proteusId) when nothing says it is another item."""
    rows = db.run("""MATCH (:Sheet {name: $sheet})-[:DEPICTS]->(a:Asset)
                     RETURN a.uid AS uid, a.proteusId AS pid, a.fullTag AS full""", sheet=sheet)
    by_full = {r["full"]: r["uid"] for r in rows if r["full"]}
    by_uid = {r["uid"] for r in rows}
    by_pid = {}
    for r in rows:
        if r["pid"]:
            by_pid.setdefault(r["pid"], []).append(r)
    out = {}
    for xid, o in objects.items():
        uid = by_full.get(o["fullLabel"]) or (f"{sheet}::{xid}" if f"{sheet}::{xid}" in by_uid else None)
        if uid is None:
            same = [r["uid"] for r in by_pid.get(xid, []) if not r["full"] or r["full"] == o["fullLabel"]]
            uid = same[0] if len(same) == 1 else None
        if uid:
            out[xid] = uid
    return out


def _mark(db, sheet, a, b, check, note=None):
    """Mark the sheet's PIPE(s) between a and b; how many were marked."""
    rows = db.run("""MATCH (x:Asset {uid: $a})-[r:PIPE]-(y:Asset {uid: $b})
                     WHERE r.sheet = $sheet AND coalesce(r.source, '') <> 'drawing'
                     SET r.pdfCheck = $check, r.pdfNote = $note
                     RETURN count(r) AS n""", a=a, b=b, sheet=sheet, check=check, note=note)
    return rows[0]["n"] if rows else 0


def _direction(db, a, b):
    """(src, dst) for a new pipe between a and b: the drawing has no direction, so follow the pipes already there
    (an item that only feeds pipes starts a run, one that only receives them ends one); else a -> b."""
    flow = {r["u"]: r for r in db.run("""UNWIND $u AS uid MATCH (x:Asset {uid: uid})
                                         RETURN uid AS u,
                                                size([(x)-[p:PIPE]->() WHERE coalesce(p.status, '') <> 'candidate' | p])
                                                  AS out,
                                                size([()-[p:PIPE]->(x) WHERE coalesce(p.status, '') <> 'candidate' | p])
                                                  AS inn""", u=[a, b])}
    fa, fb = flow.get(a, {"out": 0, "inn": 0}), flow.get(b, {"out": 0, "inn": 0})
    if (fa["out"] and not fa["inn"]) or (fb["inn"] and not fb["out"]):
        return b, a
    return a, b


def run(db, sheet, dexpi_path, pdf_path, pdf_name=None, page_number=0):
    """Check a loaded sheet against its PDF and write the results (replacing the previous ones). Returns the summary
    dict that is also stored on the Sheet node. Raises pdf_vs_dexpi.PdfMismatch (after recording it on the Sheet)."""
    clear(db, sheet)
    pdf_name = pdf_name or os.path.basename(pdf_path)
    base = {"pdfFile": pdf_name, "pdfPath": os.path.abspath(pdf_path), "pdfDexpiPath": os.path.abspath(dexpi_path),
            "pdfChecked": datetime.now().isoformat(timespec="seconds")}
    try:
        res = pv.check(pdf_path, dexpi_path, page_number)
    except pv.PdfMismatch as exc:
        db.run("MATCH (s:Sheet {name: $sheet}) SET s += $props", sheet=sheet,
               props={**base, "pdfStatus": "mismatch", "pdfMessage": str(exc), "pdfTagScore": exc.tags["score"],
                      "pdfTagsMatched": exc.tags["matched"], "pdfTagsTotal": exc.tags["total"]})
        raise
    decided = db.run("MATCH (s:Sheet {name: $sheet}) RETURN s.pdfApproved AS ok, s.pdfRejected AS no", sheet=sheet)
    approved = set((decided[0]["ok"] or []) if decided else [])
    rejected = set((decided[0]["no"] or []) if decided else [])
    uid = uid_map(db, sheet, res["objects"])
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
        _mark(db, sheet, a, b, "confirmed")
    not_drawn = uids(res["dexpi_only"])
    for a, b in not_drawn:
        _mark(db, sheet, a, b, "not_drawn")
    drawn_only, already, skipped, candidates = uids(res["drawn_only"]), 0, 0, []
    for a, b in drawn_only:
        if _mark(db, sheet, a, b, "confirmed", NOTE_ALREADY_LINKED):
            already += 1
        elif pair_key(a, b) in rejected:
            skipped += 1
        else:
            src, dst = _direction(db, a, b)
            candidates.append({"src": src, "dst": dst,
                               "status": "approved" if pair_key(a, b) in approved else "candidate"})
    if candidates:
        db.run("""UNWIND $rows AS row
                  MATCH (a:Asset {uid: row.src}), (b:Asset {uid: row.dst})
                  CREATE (a)-[r:PIPE]->(b)
                  SET r.source = 'drawing', r.status = row.status, r.sheet = $sheet, r.pdfFile = $pdf,
                      r.dexpiType = 'Pipe', r.pdfCheck = 'drawn_only'""", rows=candidates, sheet=sheet, pdf=pdf_name)
    summary = {**base, "pdfStatus": "ok", "pdfTagScore": res["tag_match"]["score"],
               "pdfTagsMatched": res["tag_match"]["matched"], "pdfTagsTotal": res["tag_match"]["total"],
               "pdfWidths": res["calibration"]["widths"],
               "pdfCalibration": json.dumps({f"{w:g}": n for w, n in res["calibration"]["scores"].items()}),
               "pdfConfirmed": len(res["confirmed"]), "pdfDrawnOnly": len(res["drawn_only"]),
               "pdfNotDrawn": len(res["dexpi_only"]), "pdfAlreadyLinked": already, "pdfRejectedSkipped": skipped,
               "pdfUnmapped": unmapped, "pdfDrawnOnlyPairs": json.dumps(drawn_only),
               "pdfNotDrawnPairs": json.dumps(not_drawn)}
    db.run("MATCH (s:Sheet {name: $sheet}) SET s += $props", sheet=sheet, props=summary)
    return summary


def summary_line(s):
    """'PDF check: 34 confirmed · 2 drawn only · 0 not drawn' (+ what happened to the drawn-only pairs)."""
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


# --------------------------------------------------------------------------- review

def _remember(db, sheet, prop, key, add=True):
    db.run(f"""MATCH (s:Sheet {{name: $sheet}})
               SET s.{prop} = [k IN coalesce(s.{prop}, []) WHERE k <> $key] + CASE WHEN $add THEN [$key] ELSE [] END""",
           sheet=sheet, key=key, add=add)


def approve(db, sheet, src, dst):
    """Approve a candidate: it becomes part of the graph (and the views) like any other PIPE."""
    db.run("""MATCH (:Asset {uid: $a})-[r:PIPE {source: 'drawing'}]->(:Asset {uid: $b}) WHERE r.sheet = $sheet
              SET r.status = 'approved'""", a=src, b=dst, sheet=sheet)
    _remember(db, sheet, "pdfApproved", pair_key(src, dst))
    _remember(db, sheet, "pdfRejected", pair_key(src, dst), add=False)


def reject(db, sheet, src, dst):
    """Reject a candidate: the link is deleted, and the pair is not proposed again for this sheet."""
    db.run("""MATCH (:Asset {uid: $a})-[r:PIPE {source: 'drawing'}]-(:Asset {uid: $b}) WHERE r.sheet = $sheet
              DELETE r""", a=src, b=dst, sheet=sheet)
    _remember(db, sheet, "pdfRejected", pair_key(src, dst))
    _remember(db, sheet, "pdfApproved", pair_key(src, dst), add=False)


def checks(db):
    """One row per sheet: its PDF check summary (pdfStatus None: no PDF check) and open candidates."""
    return db.run(f"""MATCH (s:Sheet)
                      OPTIONAL MATCH ()-[r:PIPE {{source: 'drawing', status: 'candidate'}}]->() WHERE r.sheet = s.name
                      WITH s, count(r) AS openCandidates
                      RETURN s.name AS sheet, {', '.join(f's.{p} AS {p}' for p in SHEET_PROPS)}, openCandidates,
                             s.pdfApproved AS pdfApproved, s.pdfRejected AS pdfRejected
                      ORDER BY sheet""")


def drawing_links(db, sheet=None):
    """Links proposed by the drawing check: candidates and approved ones, with both assets' names, classes and
    full labels."""
    return db.run("""MATCH (a:Asset)-[r:PIPE {source: 'drawing'}]->(b:Asset)
                     WHERE $sheet IS NULL OR r.sheet = $sheet
                     RETURN r.sheet AS sheet, r.status AS status, a.uid AS src, b.uid AS dst,
                            a.name AS from_name, a.type AS from_class, a.fullTag AS from_label,
                            b.name AS to_name, b.type AS to_class, b.fullTag AS to_label
                     ORDER BY sheet, status, from_name, to_name""", sheet=sheet)


def describe_pairs(db, pairs_json):
    """[(uid, uid)] stored on the Sheet node -> rows with both assets' names, classes and full labels."""
    pairs = json.loads(pairs_json or "[]")
    if not pairs:
        return []
    info = {r["uid"]: r for r in db.run("""UNWIND $u AS uid MATCH (a:Asset {uid: uid})
                                           RETURN a.uid AS uid, a.name AS name, a.type AS cls, a.fullTag AS label""",
                                        u=sorted({u for p in pairs for u in p}))}
    rows = []
    for a, b in pairs:
        x, y = info.get(a, {}), info.get(b, {})
        rows.append({"item A": x.get("name", a), "class A": x.get("cls"), "full label A": x.get("label"),
                     "item B": y.get("name", b), "class B": y.get("cls"), "full label B": y.get("label"),
                     "src": a, "dst": b})
    return rows
