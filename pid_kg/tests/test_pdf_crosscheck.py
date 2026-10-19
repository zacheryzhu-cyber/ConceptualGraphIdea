"""Acceptance test for the PDF drawing check (crosscheck/pdf_vs_dexpi.py, crosscheck/store.py) on every sheet with a
tests/expected_pdf_crosscheck_<name>.json. The check runs on the sheet's graph in memory while it is loaded; its
results go into the sheet's snapshot, and only approved links reach Neo4j (as FLOWS_TO {source: 'drawing'}). The
expected values live in the JSON files only.
    python -m pytest tests -q
"""
import glob
import os

import pytest

import check_islands
import plant_model as pm
from conftest import PDF_CHECK_PREFIX, PROJECT, load_json, project_path
from crosscheck import pdf_vs_dexpi as pv
from crosscheck import store as pdf_store

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
            sheets.append({"sheet": sheet, "file": os.path.basename(rel),
                           "drawing": pdf_store.drawing_name(fh.read(20000))})
    assert pdf_store.pair_pdfs(sheets, exp["pairing"]["pdfs"]) == exp["pairing"]["expected"]


# --------------------------------------------------------------------------- loading with the PDF (test database)

def load_plant(st, exp):
    """The other sheets without a PDF, then the checked sheet with its PDF; returns the checked sheet's load result."""
    for sheet, rel in others(exp).items():
        st.load_sheet(project_path(rel), sheet)
    return st.load_sheet(project_path(exp["sheets"][exp["sheet"]]), exp["sheet"], pdf_path=project_path(exp["pdf"]))


def pdf_marks(snap):
    """{pdfCheck: count} over the DEXPI links of a snapshot."""
    marks = {}
    for rels in snap["adjacency"].values():
        for r in rels:
            c = r["properties"].get("pdfCheck")
            if c and r["properties"].get("source") != "drawing":
                marks[c] = marks.get(c, 0) + 1
    return marks


def flows(db):
    return db.run("MATCH (a:Asset)-[f:FLOWS_TO]->(b:Asset) RETURN a.uid AS src, b.uid AS dst, properties(f) AS p")


@pytest.mark.parametrize("set_file", SETS)
def test_store_as_loaded(store, set_file):
    exp = load_json(set_file)
    result = load_plant(store, exp)
    summary = result["pdf"]
    assert (summary["pdfConfirmed"], summary["pdfDrawnOnly"], summary["pdfNotDrawn"]) == \
        (exp["counts"]["confirmed"], exp["counts"]["drawn_only"], exp["counts"]["dexpi_only"])
    assert summary["pdfAlreadyLinked"] == exp["drawn_only_already_linked"]
    assert summary["pdfUnmapped"] == 0
    assert summary["pdfWidths"] == exp["pipe_widths"]
    snap = store.snapshot(exp["sheet"])
    marks = pdf_marks(snap)
    assert marks.get("confirmed", 0) >= exp["counts"]["confirmed"] and "not_drawn" not in marks

    # the drawn-only pairs are already linked by the loader (drawn against each other): the PDF only confirms them
    nodes = snap["nodes"]
    confirmed = {frozenset(nodes[u]["fullTag"] for u in (u0, r["to"]))
                 for u0, rels in snap["adjacency"].items() for r in rels
                 if r["properties"].get("pdfNote") == pdf_store.NOTE_ALREADY_LINKED}
    assert confirmed == {frozenset(p) for p in exp["drawn_only"]}
    assert not store.drawing_links() and result["candidates"] == 0
    assert len(flows(store.db)) == exp["neo4j"]["flows_as_loaded"]
    assert result["unconnected"] == []
    row = store.db.run("MATCH (s:Sheet {name: $s}) RETURN s.pdfStatus AS st, s.pdfOpenCandidates AS n",
                       s=exp["sheet"])[0]
    assert row == {"st": "ok", "n": 0}

    # sheets loaded without a PDF are untouched
    for sheet in others(exp):
        assert store.snapshot(sheet)["sheet"]["pdf"] is None
        assert not pdf_marks(store.snapshot(sheet))
        assert store.db.run("MATCH (s:Sheet {name: $s}) RETURN s.pdfStatus AS x", s=sheet)[0]["x"] is None

    # the snapshot carries source, status and pdfCheck on every relationship
    assert all({"source", "status", "pdfCheck"} <= set(r["properties"])
               for rels in snap["adjacency"].values() for r in rels)

    # loading again replaces the results: same marks, no new links
    load_plant(store, exp)
    again = store.snapshot(exp["sheet"])
    assert pdf_marks(again) == marks and again["counts"]["relationships"] == snap["counts"]["relationships"]

    # a wrong pairing records the mismatch and leaves no results
    wrong = exp["wrong_pairing"]
    r = store.load_sheet(project_path(wrong["dexpi"]), wrong["sheet"], pdf_path=project_path(exp["pdf"]))
    assert r["pdf"]["pdfStatus"] == "mismatch" and wrong["message"] in r["pdf"]["pdfMessage"]
    assert not pdf_marks(store.snapshot(wrong["sheet"]))
    assert store.db.run("MATCH (s:Sheet {name: $s}) RETURN s.pdfStatus AS st", s=wrong["sheet"])[0]["st"] == "mismatch"

    # removing the sheet removes its marks
    store.remove_sheet(exp["sheet"])
    assert store.snapshot(exp["sheet"]) is None


@pytest.fixture
def without_step_2b(monkeypatch):
    """Load sheets without the links proteus_topology step 2b infers from items drawn against each other, as a loader
    without that rule reads them: the PDF check then has to propose the drawn-only pairs as candidates."""
    plain = pm.sheet_graph

    def sheet_graph(assets, edges, sheet):
        return plain(assets, [e for e in edges if e["props"].get("inferred") != "drawn touching"], sheet)
    monkeypatch.setattr(pm, "sheet_graph", sheet_graph)


@pytest.mark.parametrize("set_file", SETS)
def test_candidates_review(store, without_step_2b, set_file):
    exp = load_json(set_file)
    sheet = exp["sheet"]
    result = load_plant(store, exp)
    assert result["pdf"]["pdfAlreadyLinked"] == 0
    cands = store.drawing_links(sheet)
    assert len(cands) == exp["counts"]["drawn_only"] and {c["status"] for c in cands} == {"candidate"}
    assert result["candidates"] == len(cands)
    assert len(flows(store.db)) == exp["neo4j"]["flows_before_approval"]
    nodes = store.snapshot(sheet)["nodes"]
    assert result["unconnected"] == [nodes[u]["name"] for u in exp["neo4j"]["unconnected_before_approval"]]

    # candidates stay out of the plant graph (and the islands check) until approved
    groups = check_islands.components(store.export())
    assert len(groups) == exp["islands_without_candidates"]
    a, b = exp["split_by_candidates"]
    assert not any(a in g and b in g for g in groups)
    shown = store.export(candidates=True)
    assert sum(r["properties"]["status"] == "candidate" for rels in shown["adjacency"].values() for r in rels) == \
        len(cands)

    # approving both: one connected plant, and Neo4j gains the flow through them, from the drawing
    for c in cands:
        store.approve(c["src"], c["dst"])
    assert len(check_islands.components(store.export())) == 1
    links = {(r["src"], r["dst"]): r["p"] for r in flows(store.db)}
    assert len(links) == exp["neo4j"]["flows_after_approval"]
    for pair in exp["conceptual_after_approval"]:
        assert links[tuple(pair)]["source"] == "drawing" and links[tuple(pair)]["status"] == "approved"
    assert not store.unconnected(sheet)

    # rejecting one: removed, and not proposed again on a reload or a rebuild
    rejected = cands[0]
    store.reject(rejected["src"], rejected["dst"])
    left = store.drawing_links(sheet)
    assert len(left) == len(cands) - 1 and {c["status"] for c in left} == {"approved"}
    assert len(flows(store.db)) == exp["neo4j"]["flows_after_one_rejected"]
    r = load_plant(store, exp)
    assert r["pdf"]["pdfRejectedSkipped"] == 1
    results, problems = store.rebuild()
    assert not problems
    again = store.drawing_links(sheet)
    assert {(c["src"], c["dst"]) for c in again} == {(c["src"], c["dst"]) for c in left}
    assert {c["status"] for c in again} == {"approved"}
    assert store.decisions()[pm.pair_key(rejected["src"], rejected["dst"])]["decision"] == "rejected"

    # removing the sheet removes its candidates
    store.remove_sheet(sheet)
    assert not store.drawing_links()
