"""Acceptance test for the conceptual-only Neo4j (plant_store.py) on every sheet set with a tests/expected_<sheetset>.json
that has a "neo4j" section, and on every PDF check set (tests/expected_pdf_crosscheck_<name>.json):

    - Rebuild: Neo4j holds main equipment, off-page connectors, loops and the valves loops drive; nothing else
    - a sheet loaded without its PDF reports "Loaded without drawing check" and its unconnected equipment
    - with the PDF, the same flows, the PDF only confirming them (the candidate review: test_pdf_crosscheck.py)
    - loops MEASURE / CONTROL what the expected file says
    - the Process and Complete levels, now computed from the file snapshots, are the same as before the change
      (tests/golden_views_*.json, made by the former pipeline that kept the complete graph in Neo4j)
    - the hierarchy (Plant tab) is unchanged

The expected values live in the JSON files only.
    python -m pytest tests/test_conceptual_store.py -q
"""
import glob
import os

import pytest

import plant_store as ps
from conftest import PDF_CHECK_PREFIX, PROJECT, load, load_json, sheet_sets

SETS = [s for s in sheet_sets() if "neo4j" in load_json(s)]
PDF_SETS = sorted(os.path.basename(f) for f in glob.glob(os.path.join(PROJECT, "tests", f"{PDF_CHECK_PREFIX}*.json")))


def count(db, query, **params):
    return db.run(query, **params)[0]["n"]


def rebuild(store, expected, pdfs=None):
    """Register the set's sheets (as the app does when loading them), then Rebuild graph."""
    load(store, expected, list(expected["sheets"]), pdfs)
    results, problems = store.rebuild()
    assert not problems
    return {r["sheet"]: r for r in results}


@pytest.mark.parametrize("set_file", SETS)
def test_rebuild_without_pdf(store, set_file):
    expected = load_json(set_file)
    want = expected["neo4j"]
    results = rebuild(store, expected)
    db = store.db
    assert count(db, "MATCH (a:Asset) RETURN count(a) AS n") == want["assets"]
    assert count(db, "MATCH (a:Asset:Equipment) RETURN count(a) AS n") == want["equipment"]
    assert count(db, "MATCH (a:Asset:Boundary) RETURN count(a) AS n") == want["boundaries"]
    assert count(db, "MATCH ()-[f:FLOWS_TO]->() RETURN count(f) AS n") == want["flows"]
    assert count(db, "MATCH (l:Loop) RETURN count(l) AS n") == want["loops"]
    for sheet, loose in want["unconnected"].items():
        for uid in loose:
            assert count(db, "MATCH (a:Asset {uid: $u})-[f:FLOWS_TO]-() RETURN count(f) AS n", u=uid) == 0
        names = [db.run("MATCH (a:Asset {uid: $u}) RETURN a.name AS n", u=u)[0]["n"] for u in loose]
        assert results[sheet]["unconnected"] == names            # the Load tab's count
        assert results[sheet]["pdf"] is None and results[sheet]["pdfLine"] == "Loaded without drawing check"
        assert count(db, "MATCH (s:Sheet {name: $s}) RETURN s.unconnectedEquipment AS n", s=sheet) == len(loose)
    # every FLOWS_TO says where it comes from; via / viaUids / lines whenever it passes something
    for f in db.run("MATCH ()-[f:FLOWS_TO]->() RETURN properties(f) AS p"):
        assert {"crossSheet", "source", "kind"} <= set(f["p"]) and f["p"]["source"] == "dexpi"
        assert len(f["p"].get("via", [])) == len(f["p"].get("viaUids", []))


@pytest.mark.parametrize("set_file", SETS)
def test_clean_database(store, set_file):
    expected = load_json(set_file)
    want = expected["neo4j"]
    rebuild(store, expected)
    db = store.db
    assert count(db, "MATCH (n) WHERE n.type IN $t RETURN count(n) AS n", t=want["absent_types"]) == 0
    labels = {r["l"] for r in db.run("MATCH (n) UNWIND labels(n) AS l RETURN DISTINCT l")}
    # besides the allowed labels, only DEXPI class labels of the stored equipment, boundaries and valves
    classes = {r["t"] for r in db.run("MATCH (n) WHERE n:Asset OR n:Valve RETURN DISTINCT n.type AS t")}
    assert labels - set(want["labels_allowed"]) <= classes
    rels = {r["t"] for r in db.run("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")}
    assert rels <= set(want["relationships_allowed"])
    assert count(db, "MATCH (n) WHERE NOT (n:Plant OR n:Area OR n:System OR n:Sheet OR n:Asset OR n:Loop OR n:Valve) "
                     "RETURN count(n) AS n") == 0
    # a valve node only where a loop drives it
    assert count(db, "MATCH (v:Valve) WHERE NOT (:Loop)-[:CONTROLS]->(v) RETURN count(v) AS n") == 0


@pytest.mark.parametrize("set_file", SETS)
def test_loops(store, set_file):
    expected = load_json(set_file)
    rebuild(store, expected)
    db = store.db
    for want in expected["neo4j"]["loop_links"]:
        rows = db.run("""MATCH (l:Loop {tag: $tag}) OPTIONAL MATCH (l)-[r:MEASURES|CONTROLS]->(t)
                         RETURN type(r) AS rel, t.uid AS uid""", tag=want["loop"])
        assert rows, f"no loop {want['loop']}"
        got = {"measures": sorted(r["uid"] for r in rows if r["rel"] == "MEASURES"),
               "controls": sorted(r["uid"] for r in rows if r["rel"] == "CONTROLS")}
        assert got == {"measures": sorted(want["measures"]), "controls": sorted(want["controls"])}
    loop = db.run("MATCH (l:Loop) WHERE size(l.functions) > 0 RETURN count(l) AS n")[0]["n"]
    assert loop > 0


@pytest.mark.parametrize("set_file", SETS)
def test_hierarchy_unchanged(store, set_file):
    expected = load_json(set_file)
    rebuild(store, expected)
    rows = store.db.run("""MATCH (p:Plant)-[:HAS_AREA]->(ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)
                           OPTIONAL MATCH (s)-[:DEPICTS]->(a:Asset)
                           RETURN p.name AS plant, ar.name AS area, sy.name AS system, s.name AS sheet, count(a) AS n
                           ORDER BY sheet""")
    assert [(r["plant"], r["area"], r["system"], r["sheet"]) for r in rows] == \
        [(ps.pl.DEFAULT_PLANT, ps.pl.UNASSIGNED, ps.pl.UNASSIGNED, s) for s in sorted(expected["sheets"])]
    assert all(r["n"] > 0 for r in rows)
    stats = store.stats()
    assert stats["Sheets"] == len(expected["sheets"])
    assert stats["Cross-sheet links"] == len(expected["cross_sheet_pipes"])   # the same count as before


def slim(view):
    """The parts of a level view the golden files keep."""
    nodes = {u: {k: n.get(k) for k in ("name", "type", "category", "sheets", "group", "folded")}
             for u, n in view["nodes"].items()}
    links = sorted([u, r["to"], r["type"], {k: r["properties"].get(k)
                                           for k in ("via", "lines", "crossSheet", "loopLink", "line", "sheet")}]
                   for u, rels in view["adjacency"].items() for r in rels)
    return {"nodes": nodes, "links": links}


@pytest.mark.parametrize("set_file", SETS)
def test_detail_views_from_snapshots(store, set_file):
    expected = load_json(set_file)
    golden = load_json(expected["neo4j"]["golden_views"])
    load(store, expected, list(expected["sheets"]))
    for level in ("Complete", "Process"):
        got = slim(store.level_view(level))
        assert got["nodes"] == golden[level]["nodes"], level
        assert got["links"] == golden[level]["links"], level

    # "Show detail" of a FLOWS_TO: the Process path between its ends, through the valves it passes
    f = store.db.run("""MATCH (a:Asset)-[f:FLOWS_TO]->(b:Asset) WHERE size(f.viaUids) > 0
                        RETURN a.uid AS src, b.uid AS dst, f.viaUids AS via ORDER BY size(f.via) DESC LIMIT 1""")[0]
    detail = store.path_detail(f["src"], f["dst"], f["via"])
    assert {f["src"], f["dst"]} <= set(detail["nodes"])
    assert set(detail["nodes"]) - {f["src"], f["dst"]} <= set(f["via"])
    assert any(r["type"] in ("PIPE", "DIRECT_PIPING_CONNECTION", "CONTINUES_TO")
               for rels in detail["adjacency"].values() for r in rels)


@pytest.mark.parametrize("set_file", PDF_SETS)
def test_rebuild_with_pdf(store, set_file):
    exp = load_json(set_file)
    want = exp["neo4j"]
    results = rebuild(store, exp, {exp["sheet"]: exp["pdf"]})
    db = store.db
    r = results[exp["sheet"]]
    assert r["pdf"]["pdfStatus"] == "ok" and r["pdf"]["pdfAlreadyLinked"] == exp["drawn_only_already_linked"]
    assert not store.drawing_links() and not r["unconnected"]
    assert count(db, "MATCH ()-[f:FLOWS_TO]->() RETURN count(f) AS n") == want["flows_as_loaded"]
    # the flows through the drawn-only pairs come from the loader; the PDF only confirms them
    for src, dst in exp["conceptual_after_approval"]:
        f = db.run("MATCH (:Asset {uid: $a})-[f:FLOWS_TO]->(:Asset {uid: $b}) RETURN properties(f) AS p",
                   a=src, b=dst)
        assert len(f) == 1 and f[0]["p"]["source"] == "dexpi" and f[0]["p"]["pdfCheck"] == "confirmed"
