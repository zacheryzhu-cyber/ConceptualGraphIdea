"""Acceptance test for the PDF drawing check (crosscheck/pdf_vs_dexpi.py, crosscheck/store.py) on every sheet with a
tests/expected_pdf_crosscheck_<name>.json. The expected values live in the JSON files only.
    python -m pytest tests -q
"""
import glob
import os

import pytest

import check_islands
import graph_levels as gl
import plant_loader as pl
from conftest import PDF_CHECK_PREFIX, PROJECT, load_json, project_path
from crosscheck import pdf_vs_dexpi as pv
from crosscheck import store

SETS = sorted(os.path.basename(f) for f in glob.glob(os.path.join(PROJECT, "tests", f"{PDF_CHECK_PREFIX}*.json")))


def others(exp):
    """The sheets of the set loaded without a PDF."""
    return {s: rel for s, rel in exp["sheets"].items() if s != exp["sheet"]}


def labels(result, pairs):
    return {frozenset(result["objects"][x]["fullLabel"] for x in p) for p in pairs}


# --------------------------------------------------------------------------- the check itself, no database

@pytest.mark.parametrize("set_file", SETS)
def test_check(set_file):
    exp = load_json(set_file)
    res = pv.check(project_path(exp["pdf"]), project_path(exp["sheets"][exp["sheet"]]))
    score = res["tag_match"]["score"]
    assert score >= exp["tag_score"]["min"]
    assert abs(score - exp["tag_score"]["about"]) <= exp["tag_score"]["tolerance"]
    assert res["calibration"]["widths"] == exp["pipe_widths"]
    for width, n in exp["calibration"].items():
        assert res["calibration"]["scores"][float(width)] == n
    assert {k: len(res[k]) for k in exp["counts"]} == exp["counts"]
    assert labels(res, res["drawn_only"]) == {frozenset(p) for p in exp["drawn_only"]}


@pytest.mark.parametrize("set_file", SETS)
def test_wrong_pairing_is_rejected(set_file):
    exp = load_json(set_file)
    with pytest.raises(pv.PdfMismatch, match=exp["wrong_pairing"]["message"]) as err:
        pv.check(project_path(exp["pdf"]), project_path(exp["wrong_pairing"]["dexpi"]))
    assert err.value.tags["score"] < pv.TAG_MIN_SCORE


@pytest.mark.parametrize("set_file", SETS)
def test_pdf_pairing(set_file):
    exp = load_json(set_file)
    sheets = []
    for sheet, rel in exp["sheets"].items():
        with open(project_path(rel), "rb") as fh:
            sheets.append({"sheet": sheet, "file": os.path.basename(rel), "drawing": store.drawing_name(fh.read(20000))})
    assert store.pair_pdfs(sheets, exp["pairing"]["pdfs"]) == exp["pairing"]["expected"]


# --------------------------------------------------------------------------- in the test database

def load_plant(db, exp):
    """The other sheets without a PDF, then the checked sheet with its PDF; returns the check summary."""
    for sheet, rel in others(exp).items():
        pl.load_sheet(db, project_path(rel), sheet, pl.DEFAULT_PLANT, None, None, None)
    pl.load_sheet(db, project_path(exp["sheets"][exp["sheet"]]), exp["sheet"], pl.DEFAULT_PLANT, None, None, None)
    summary = store.run(db, exp["sheet"], project_path(exp["sheets"][exp["sheet"]]), project_path(exp["pdf"]))
    pl.link_sheets(db)
    return summary


def drawing_pipes(db):
    return db.run("""MATCH (a:Asset)-[r:PIPE {source: 'drawing'}]->(b:Asset)
                     RETURN a.uid AS src, b.uid AS dst, r.status AS status, r.sheet AS sheet""")


def pdf_marks(db):
    return {r["c"]: r["n"] for r in db.run("""MATCH ()-[r]->() WHERE r.pdfCheck IS NOT NULL
                                              RETURN r.pdfCheck AS c, count(r) AS n""")}


def unlink_drawn_only(db, exp):
    """Delete the loader's own links for the drawn-only pairs, as a loader without proteus_topology step 2b leaves
    them: the PDF check then has to propose them."""
    for a, b in exp["drawn_only"]:
        db.run("MATCH (:Asset {fullTag: $a})-[r:PIPE]-(:Asset {fullTag: $b}) DELETE r", a=a, b=b)


@pytest.mark.parametrize("set_file", SETS)
def test_store_as_loaded(clean_db, set_file):
    exp = load_json(set_file)
    summary = load_plant(clean_db, exp)
    assert (summary["pdfConfirmed"], summary["pdfDrawnOnly"], summary["pdfNotDrawn"]) == \
        (exp["counts"]["confirmed"], exp["counts"]["drawn_only"], exp["counts"]["dexpi_only"])
    assert summary["pdfAlreadyLinked"] == exp["drawn_only_already_linked"]
    assert summary["pdfUnmapped"] == 0
    assert summary["pdfWidths"] == exp["pipe_widths"]
    marks = pdf_marks(clean_db)
    assert marks.get("confirmed", 0) >= exp["counts"]["confirmed"] and "not_drawn" not in marks
    assert not drawing_pipes(clean_db)

    # sheets loaded without a PDF are untouched
    for sheet in others(exp):
        assert clean_db.run("MATCH (s:Sheet {name: $s}) RETURN s.pdfStatus AS x", s=sheet)[0]["x"] is None
        assert not clean_db.run("MATCH ()-[r]->() WHERE r.sheet = $s AND r.pdfCheck IS NOT NULL RETURN r", s=sheet)

    # the export carries source, status and pdfCheck on every relationship
    export = pl.export_adjacency(clean_db)
    assert all({"source", "status", "pdfCheck"} <= set(r["properties"])
               for rels in export["adjacency"].values() for r in rels)

    # re-running replaces the results: same marks, no new links
    n_rels = clean_db.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    store.run(clean_db, exp["sheet"], project_path(exp["sheets"][exp["sheet"]]), project_path(exp["pdf"]))
    assert pdf_marks(clean_db) == marks
    assert clean_db.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"] == n_rels

    # a wrong pairing records the mismatch and leaves no results
    wrong = exp["wrong_pairing"]
    with pytest.raises(pv.PdfMismatch):
        store.run(clean_db, wrong["sheet"], project_path(wrong["dexpi"]), project_path(exp["pdf"]))
    row = clean_db.run("MATCH (s:Sheet {name: $s}) RETURN s.pdfStatus AS st, s.pdfMessage AS msg", s=wrong["sheet"])
    assert row[0]["st"] == "mismatch" and wrong["message"] in row[0]["msg"]
    assert not clean_db.run("MATCH ()-[r]->() WHERE r.sheet = $s AND r.pdfCheck IS NOT NULL RETURN r", s=wrong["sheet"])

    # removing the sheet removes its marks
    pl.remove_sheet(clean_db, exp["sheet"])
    assert not pdf_marks(clean_db)


@pytest.mark.parametrize("set_file", SETS)
def test_candidates_review(clean_db, set_file):
    exp = load_json(set_file)
    sheet, dexpi, pdf = exp["sheet"], project_path(exp["sheets"][exp["sheet"]]), project_path(exp["pdf"])
    load_plant(clean_db, exp)
    unlink_drawn_only(clean_db, exp)
    summary = store.run(clean_db, sheet, dexpi, pdf)
    cands = drawing_pipes(clean_db)
    assert len(cands) == exp["counts"]["drawn_only"] and {c["status"] for c in cands} == {"candidate"}
    assert summary["pdfAlreadyLinked"] == 0

    # candidates stay out of the views (and the islands check) until approved
    groups = check_islands.components(pl.export_adjacency(clean_db))
    assert len(groups) == exp["islands_without_candidates"]
    a, b = exp["split_by_candidates"]
    assert not any(a in g and b in g for g in groups)
    shown = pl.export_adjacency(clean_db, candidates=True)
    assert sum(r["properties"]["status"] == "candidate" for rels in shown["adjacency"].values() for r in rels) == \
        len(cands)

    # re-running never duplicates
    store.run(clean_db, sheet, dexpi, pdf)
    assert len(drawing_pipes(clean_db)) == len(cands)

    # approving both: one connected plant, and the Conceptual view gains the flow through them, from the drawing
    for c in cands:
        store.approve(clean_db, sheet, c["src"], c["dst"])
    export = pl.export_adjacency(clean_db)
    assert len(check_islands.components(export)) == 1
    concept = gl.build(export, "Conceptual")
    links = {(u, r["to"]): r["properties"] for u, rels in concept["adjacency"].items() for r in rels}
    for pair in exp["conceptual_after_approval"]:
        assert links[tuple(pair)]["source"] == "drawing" and links[tuple(pair)]["status"] == "approved"

    # rejecting one: deleted, and not proposed again on a re-run, nor after reloading the sheet
    rejected = cands[0]
    store.reject(clean_db, sheet, rejected["src"], rejected["dst"])
    left = drawing_pipes(clean_db)
    assert len(left) == len(cands) - 1 and {c["status"] for c in left} == {"approved"}
    pl.load_sheet(clean_db, dexpi, sheet, pl.DEFAULT_PLANT, None, None, None)
    unlink_drawn_only(clean_db, exp)
    summary = store.run(clean_db, sheet, dexpi, pdf)
    again = drawing_pipes(clean_db)
    assert summary["pdfRejectedSkipped"] == 1
    assert {(c["src"], c["dst"]) for c in again} == {(c["src"], c["dst"]) for c in left}
    assert {c["status"] for c in again} == {"approved"}

    # removing the sheet removes its candidates
    pl.remove_sheet(clean_db, sheet)
    assert not drawing_pipes(clean_db)
