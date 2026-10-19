"""Detail levels of the graph view (graph_levels.py) on every sheet set with a tests/expected_<sheetset>.json,
computed from the sheets' file snapshots, plus a run of all three levels over every DEXPI 1.3 file in the project.

    python -m pytest tests/test_levels.py -q -s
"""
import os
from collections import Counter

import pytest

import graph_levels as gl
import plant_loader as pl
from conftest import PROJECT, load, load_json, sheet_sets
from test_generality import dexpi_files

SETS = sheet_sets()


def flow(view):
    return [(u, r) for u, rels in view["adjacency"].items() for r in rels if r["type"] in gl.FLOW_RELS]


def process_counts(view):
    flow_nodes = [u for u, n in view["nodes"].items() if n["group"] in gl.FLOW_GROUPS]
    return len(flow_nodes), len(flow(view))


@pytest.mark.parametrize("set_file", SETS)
def test_levels_on_plant(store, set_file):
    expected = load_json(set_file)
    load(store, expected, list(expected["sheets"]))
    count = lambda: (store.db.run("MATCH (n) RETURN count(n) AS n")[0]["n"],
                     store.db.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"])
    stored = count()
    export = store.export()
    complete = gl.build(export, "Complete")
    assert complete["counts"] == export["counts"]

    process = gl.build(export, "Process")
    want = expected["levels"]["Process"]
    assert process_counts(process) == (want["flow_nodes"], want["flow_links"])
    loops = [u for u, n in export["nodes"].items() if n["type"] in gl.load_config()["groups"]
             and gl.load_config()["groups"][n["type"]] == "loop"]
    assert set(loops) <= set(process["nodes"])          # one node per loop
    assert all(r["properties"].get("loopLink") for _, rels in process["adjacency"].items() for r in rels
               if r["type"] not in gl.FLOW_RELS)

    concept = gl.build(export, "Conceptual")
    want = expected["levels"]["Conceptual"]
    assert len(concept["nodes"]) == want["nodes"]
    links = flow(concept)
    assert len(links) == want["links"]
    assert {(u, r["to"]) for u, r in links} >= {tuple(p) for p in want["links_present"]}
    for src, dst, line in want["cross_sheet"]:
        link = next(r for u, r in links if (u, r["to"]) == (src, dst))
        assert link["properties"]["crossSheet"] and line in link["properties"]["lines"]
    linked = {u for u, r in links} | {r["to"] for u, r in links}
    for u in want["alone"]:
        assert u in concept["nodes"] and u not in linked

    assert count() == stored, "building a level must not change the database"


@pytest.mark.parametrize("rel", dexpi_files())
def test_levels_on_every_file(rel):
    assets, edges, issues = pl.parse_sheet(os.path.join(PROJECT, rel), "sheet")
    export = gl.from_rows(assets, edges, "sheet")
    sizes = {}
    for level in gl.LEVELS:
        view = gl.build(export, level)
        assert set(view["nodes"]) <= set(export["nodes"])
        for u, rels in view["adjacency"].items():
            assert u in view["nodes"] and all(r["to"] in view["nodes"] for r in rels)
        sizes[level] = (len(view["nodes"]), len(flow(view)))
    loose = Counter({r["type"]: r["count"] for r in gl.unclassified(export)})
    print(f"\n{rel}: (nodes, flow links) {sizes}; unclassified types: {dict(loose) or 'none'}")
    assert sizes["Conceptual"][0] <= sizes["Process"][0] <= sizes["Complete"][0]
