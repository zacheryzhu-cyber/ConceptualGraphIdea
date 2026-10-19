"""Acceptance test for the XML link rules (dexpi_links.py) on every sheet set with a tests/expected_<sheetset>.json.

The plant graph checked here is the complete graph of the loaded sheets, put together from their file snapshots
(plant_store.PlantStore.export); Neo4j only holds the conceptual level. The expected tags and counts live in the JSON
files only; the loader and these checks are generic.
    python -m pytest tests -q
"""
import os
from collections import Counter

import pytest

import check_islands
import plant_loader as pl
from conftest import load, load_json, project_path, sheet_sets

SETS = sheet_sets()


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


# --------------------------------------------------------------------------- whole plant

@pytest.mark.parametrize("set_file", SETS)
@pytest.mark.parametrize("reverse", [False, True], ids=["in order", "reverse order"])
def test_plant_islands(store, set_file, reverse):
    expected = load_json(set_file)
    order = list(expected["sheets"])[::-1] if reverse else list(expected["sheets"])
    load(store, expected, order)
    assert not store.drawing_links()          # without a PDF nothing waits for review
    export = store.export()
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
def test_remove_sheet(store, set_file):
    """Removing a sheet removes its snapshot, its sources, its Neo4j nodes and the links across to it."""
    expected = load_json(set_file)
    sheets = list(expected["sheets"])
    load(store, expected, sheets)
    n_cross = len(expected["cross_sheet_pipes"])
    cross = lambda: sum(r["properties"].get("crossSheet") is True and r["type"] == "PIPE"
                        for rels_ in store.export()["adjacency"].values() for r in rels_)
    assert cross() == n_cross

    gone = sheets[-1]
    store.remove_sheet(gone)
    assert cross() == 0
    assert not os.path.exists(store.snapshot_path(gone))
    assert gone not in store.registry()
    db = store.db
    assert not db.run("MATCH (s:Sheet {name: $s}) RETURN s", s=gone)
    assert db.run("MATCH (n) WHERE (n:Asset OR n:Loop OR n:Valve) AND $s IN n.sheets RETURN count(n) AS n",
                  s=gone)[0]["n"] == 0
    assert db.run("MATCH ()-[f:FLOWS_TO {crossSheet: true}]->() RETURN count(f) AS n")[0]["n"] == 0
    assert db.run("MATCH (a:Asset) RETURN count(a) AS n")[0]["n"] > 0   # the other sheet stays

    load(store, expected, [gone])
    assert cross() == n_cross
