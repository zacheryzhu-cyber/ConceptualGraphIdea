"""Acceptance test for the XML link rules (dexpi_links.py) on every sheet set with a tests/expected_<sheetset>.json.

The expected tags and counts live in the JSON files only; the loader and these checks are generic.
    python -m pytest tests -q
"""
from collections import Counter

import pytest

import check_islands
import plant_loader as pl
from conftest import load_json, project_path, sheet_sets

SETS = sheet_sets()


def load(db, expected, order):
    for sheet in order:
        pl.load_sheet(db, project_path(expected["sheets"][sheet]), sheet, pl.DEFAULT_PLANT, None, None, None)
        pl.link_sheets(db)
    return pl.export_adjacency(db)


def rels(export):
    return [{"src": src, **r} for src, out in export["adjacency"].items() for r in out]


# --------------------------------------------------------------------------- per sheet, no database needed

@pytest.mark.parametrize("set_file", SETS)
def test_rules_per_sheet(set_file):
    expected = load_json(set_file)
    for sheet, rules in expected["rules_per_sheet"].items():
        details = {}
        assets, edges, issues = pl.parse_sheet(project_path(expected["sheets"][sheet]), sheet, details)
        got = {k: v for k, v in details["rules"].items() if k in rules}
        assert got == rules, f"{sheet}: rule counts"
        uids = {a["uid"] for a in assets}
        assert not uids & set(expected["skipped"]), f"{sheet}: export artefacts must be left out"
        assert len(uids) == len(assets)


# --------------------------------------------------------------------------- whole plant, in the test database

@pytest.mark.parametrize("set_file", SETS)
@pytest.mark.parametrize("reverse", [False, True], ids=["in order", "reverse order"])
def test_plant_islands(clean_db, set_file, reverse):
    expected = load_json(set_file)
    order = list(expected["sheets"])[::-1] if reverse else list(expected["sheets"])
    export = load(clean_db, expected, order)
    nodes, all_rels = export["nodes"], rels(export)

    # identity: the assets that existed before keep their uids
    assert not set(expected["unchanged_uids"]) - set(nodes)

    # relationship counts
    counts = Counter(r["type"] for r in all_rels)
    for rel, n in expected["plant_relationships"].items():
        assert counts[rel] == n, rel
    nozzle = [r for r in all_rels if r["type"] == "MEASURING_LINE_FUNCTION" and r["properties"].get("nozzle")]
    assert len(nozzle) == expected["measuring_lines_to_nozzle"]
    line = expected["measuring_lines_to_pipe_line"]
    assert nodes[line["line"]]["type"] == "PipingNetworkSystem"
    assert sum(r["type"] == "MEASURING_LINE_FUNCTION" and r["to"] == line["line"] for r in all_rels) == line["count"]
    sheet_pipes = [r for r in all_rels if r["type"] == "PIPE" and not r["properties"].get("crossSheet")]
    assert len(sheet_pipes) == expected["pipes_on_sheets"]
    assert all(r["properties"].get("sheet") for r in sheet_pipes)

    # alarms and skipped artefacts
    for uid in expected["alarms"]:
        assert (nodes[uid]["type"], nodes[uid]["category"]) == ("Alarm", "Instrumentation")
        assert any(r["type"] == "HAS_ALARM" and r["to"] == uid and
                   nodes[r["src"]]["type"] == "ProcessInstrumentationFunction" for r in all_rels)
    assert not set(expected["skipped"]) & set(nodes)

    # pipes across sheets, from the open pipe ends
    cross = sorted((r["src"], r["to"], r["properties"]["line"]) for r in all_rels if r["properties"].get("crossSheet"))
    assert cross == sorted((c["from"], c["to"], c["line"]) for c in expected["cross_sheet_pipes"])

    # islands
    groups = check_islands.components(export)
    assert len(groups) == expected["islands"]
    assert sum(len(g) == 1 for g in groups) == expected["single_nodes"]
    island_of = {u: k for k, g in enumerate(groups, 1) for u in g}
    for k, members in enumerate(expected["island_members"], 1):
        assert {island_of[u] for u in members} == {k}, f"island {k}"
        if k in expected["island_exact"]:
            assert set(groups[k - 1]) == set(members), f"island {k} should hold only {members}"
    leave = expected.get("island_leaves_only_through")
    if leave:
        sheets_of = {s for u in groups[leave["island"] - 1] for s in nodes[u]["sheets"]}
        assert len(sheets_of) == 1
        assert sum(nodes[u]["type"] in leave["types"] for u in groups[leave["island"] - 1]) == leave["count"]


@pytest.mark.parametrize("set_file", SETS)
def test_remove_sheet_removes_open_ends(clean_db, set_file):
    expected = load_json(set_file)
    sheets = list(expected["sheets"])
    load(clean_db, expected, sheets)
    n_cross = len(expected["cross_sheet_pipes"])
    count = lambda q: clean_db.run(q)[0]["n"]
    assert count("MATCH ()-[p:PIPE {crossSheet: true}]->() RETURN count(p) AS n") == n_cross

    pl.remove_sheet(clean_db, sheets[-1])
    pl.link_sheets(clean_db)
    assert count("MATCH ()-[p:PIPE {crossSheet: true}]->() RETURN count(p) AS n") == 0
    assert clean_db.run("MATCH (o:OpenEnd {sheet: $s}) RETURN count(o) AS n", s=sheets[-1])[0]["n"] == 0
    assert clean_db.run("MATCH (o:OpenEnd) RETURN count(o) AS n")[0]["n"] > 0   # the other sheet's ends stay

    load(clean_db, expected, sheets[-1:])
    assert count("MATCH ()-[p:PIPE {crossSheet: true}]->() RETURN count(p) AS n") == n_cross
