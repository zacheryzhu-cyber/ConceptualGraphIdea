# Task: Set up a P&ID → knowledge graph PoC (pyDEXPI + Neo4j)

You are setting up a small proof of concept on my machine. Before running anything, explain your plan in a few lines (folders, packages, commands) and confirm my operating system. Then do the work step by step and stop to tell me if any step fails. Do not add features or change scope beyond what is written here.

## Goal
Take a DEXPI (Proteus XML) P&ID, convert it with the open-source pyDEXPI library into a graph of equipment, valves and instruments, and load it into a local Neo4j database so I can run relationship queries.

## Step 1 — Project folder and Python environment
1. Create a folder called `pid_kg` in my home directory (or the current directory if I'm already in a projects folder — ask me if unsure).
2. Check Python is 3.10 or newer (`python --version`). If it's missing or older, stop and tell me.
3. Create a virtual environment `.venv` inside `pid_kg` and activate it (use the right command for my OS).
4. `pip install pydexpi neo4j networkx`
5. Create `requirements.txt` with those three packages.

## Step 2 — Sample P&ID
Download the DEXPI reference P&ID (© DEXPI e.V., shipped with pyDEXPI) into `pid_kg`:
https://raw.githubusercontent.com/process-intelligence-research/pyDEXPI/master/data/C01V04-VER.EX01.xml
If the download fails, clone https://github.com/process-intelligence-research/pyDEXPI and copy `data/C01V04-VER.EX01.xml` from it.

## Step 3 — Converter script
Create `pid_to_graph.py` with EXACTLY this content (it has already been tested on the sample file):

```python
"""
P&ID (DEXPI / Proteus XML) -> asset knowledge graph.

Usage:
    python pid_to_graph.py <path-to-dexpi.xml> [--out output_folder]
    python pid_to_graph.py <path-to-dexpi.xml> --neo4j bolt://localhost:7687 --user neo4j --password <pw>

Outputs (in the --out folder, default ./output):
    nodes.csv          one row per asset (equipment, valve, instrument, ...)
    edges.csv          one row per connection (pipe, signal line, ...)
    pid_graph.cypher   paste into Neo4j Browser to build the graph
"""
import argparse
import csv
import json
import os

from pydexpi.loaders import GraphAbstractor, GraphLoader, ProteusSerializer


def category_of(labels: str) -> str:
    """Rough E / P / I grouping from the DEXPI class hierarchy string."""
    if "Equipment" in labels:
        return "Equipment"
    if "Instrumentation" in labels or "Function" in labels or "Signal" in labels:
        return "Instrumentation"
    return "Piping"


def display_name(d: dict) -> str:
    """Prefer the tag shown on the drawing (e.g. P4711), then fall back."""
    for key in ("tagName", "processInstrumentationFunctionNumber",
                "processSignalGeneratingFunctionNumber", "actuatingFunctionNumber", "proteusId"):
        if d.get(key):
            return str(d[key])
    return d.get("label", "unknown")


KEEP = ["proteusId", "pipingComponentName", "lineNumber", "fluidCode", "nominalDiameterRepresentation", "pipingClassCode"]


def build(xml_path: str):
    folder, filename = os.path.split(os.path.abspath(xml_path))
    model = ProteusSerializer().load(folder, filename)
    plant_graph = GraphLoader().parse_dexpi_to_graph(model)
    graph = GraphAbstractor.build_conceptual_graph(plant_graph)

    nodes = []
    for node_id, d in graph.nodes(data=True):
        labels = str(d.get("labels", ""))
        row = {"id": node_id, "name": display_name(d), "type": d.get("label", "Unknown"),
               "category": category_of(labels)}
        row.update({k: d.get(k, "") for k in KEEP})
        nodes.append(row)

    edges = []
    for src, dst, d in graph.edges(data=True):
        row = {"source": src, "target": dst, "type": d.get("label", "connected")}
        row.update({k: d.get(k, "") for k in KEEP})
        edges.append(row)
    return nodes, edges


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def rel_type(edge_type: str) -> str:
    """Neo4j relationship names: Pipe -> PIPE, MeasuringLineFunction -> MEASURING_LINE_FUNCTION."""
    out = ""
    for i, ch in enumerate(edge_type):
        if ch.isupper() and i > 0 and not edge_type[i - 1].isupper():
            out += "_"
        out += ch.upper() if ch.isalnum() else "_"
    return out or "CONNECTED_TO"


def write_cypher(path, nodes, edges):
    with open(path, "w", encoding="utf-8") as f:
        f.write("// Clear old demo data, then rebuild the P&ID graph\n")
        f.write("MATCH (n:Asset) DETACH DELETE n;\n\n")
        for n in nodes:
            props = {k: v for k, v in n.items() if v != "" and k not in ("type", "category")}
            props["type"] = n["type"]
            f.write(f"CREATE (:Asset:{n['category']}:{n['type']} {to_cypher_map(props)});\n")
        f.write("\n")
        for e in edges:
            props = {k: v for k, v in e.items() if v != "" and k not in ("source", "target", "type")}
            props["dexpiType"] = e["type"]
            f.write(
                f"MATCH (a:Asset {{id:'{e['source']}'}}), (b:Asset {{id:'{e['target']}'}}) "
                f"CREATE (a)-[:{rel_type(e['type'])} {to_cypher_map(props)}]->(b);\n"
            )


def to_cypher_map(props: dict) -> str:
    parts = [f"{k}: {json.dumps(str(v))}" for k, v in props.items()]
    return "{" + ", ".join(parts) + "}"


def push_to_neo4j(uri, user, password, nodes, edges):
    from neo4j import GraphDatabase  # pip install neo4j

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session() as s:
            s.run("MATCH (n:Asset) DETACH DELETE n")
            for n in nodes:
                props = {k: v for k, v in n.items() if v != ""}
                s.run(f"CREATE (x:Asset:{n['category']}:{n['type']}) SET x = $p", p=props)
            for e in edges:
                props = {k: v for k, v in e.items() if v != "" and k not in ("source", "target")}
                s.run(
                    f"MATCH (a:Asset {{id:$s}}), (b:Asset {{id:$t}}) "
                    f"CREATE (a)-[r:{rel_type(e['type'])}]->(b) SET r = $p",
                    s=e["source"], t=e["target"], p=props,
                )
    print(f"Loaded into Neo4j at {uri}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("xml")
    ap.add_argument("--out", default="output")
    ap.add_argument("--neo4j")
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password")
    args = ap.parse_args()

    nodes, edges = build(args.xml)
    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "nodes.csv"), nodes)
    write_csv(os.path.join(args.out, "edges.csv"), edges)
    write_cypher(os.path.join(args.out, "pid_graph.cypher"), nodes, edges)
    print(f"{len(nodes)} assets, {len(edges)} connections -> {args.out}/")

    if args.neo4j:
        push_to_neo4j(args.neo4j, args.user, args.password, nodes, edges)
```

Run it:
```
python pid_to_graph.py C01V04-VER.EX01.xml
```
Expected console output: `36 assets, 39 connections -> output/`
Expected files: `output/nodes.csv`, `output/edges.csv`, `output/pid_graph.cypher`.
If the numbers differ, stop and show me the output.

## Step 4 — Offline check (no Neo4j needed)
Write and run a short script `check_graph.py` that reads `output/nodes.csv` and `output/edges.csv` with networkx, uses only edges of type `Pipe` and `DirectPipingConnection` as flow edges, and prints:
- all assets with category `Equipment` → expected: H1007, H1008, P4711, P4712, T4750
- equipment downstream of `P4711` → expected: H1007, H1008, P4712, T4750
- shortest flow path P4711 → T4750 → expected: P4711 → H1007 → GlobeValve-2 → T4750
Report pass/fail for each.

## Step 5 — Demo queries file
Create `queries.cypher` with EXACTLY this content:

```cypher
// Demo queries for the P&ID knowledge graph (DEXPI reference P&ID C01).
// Run them one at a time in Neo4j Browser.

// 1. Show the whole graph
MATCH (n:Asset)-[r]->(m:Asset) RETURN n, r, m;

// 2. List all equipment on the P&ID
MATCH (e:Equipment) RETURN e.name AS tag, e.type AS type ORDER BY tag;

// 3. Which equipment is downstream of pump P4711? (follows pipes in flow direction)
MATCH (p:Asset {name: 'P4711'})-[:PIPE|DIRECT_PIPING_CONNECTION*1..]->(e:Equipment)
RETURN DISTINCT e.name AS downstream_equipment, e.type AS type;

// 4. Show the actual flow path from P4711 to tank T4750
MATCH path = shortestPath((p:Asset {name: 'P4711'})-[:PIPE|DIRECT_PIPING_CONNECTION*]->(t:Asset {name: 'T4750'}))
RETURN path;

// 5. Control loops: sensor -> controller function -> actuator -> valve
MATCH (s:ProcessSignalGeneratingFunction)-[:MEASURING_LINE_FUNCTION]->(f:ProcessInstrumentationFunction)
      -[:SIGNAL_CONVEYING_FUNCTION]->(a:ActuatingFunction)-[:OPERATED_VALVE_REFERENCE]->(v:Asset)
RETURN s.name AS sensor, f.name AS loop, a.name AS actuator, v.name AS valve;

// 6. Impact question: if the valve driven by PV4712.02 closes, which equipment loses supply?
//    Expected: H1008, P4712, T4750
MATCH (:Asset {name: 'PV4712.02'})-[:OPERATED_VALVE_REFERENCE]->(v:Asset)
MATCH (v)-[:PIPE|DIRECT_PIPING_CONNECTION*1..]->(e:Equipment)
RETURN v.name AS valve, collect(DISTINCT e.name) AS affected_equipment;
```

## Step 6 — Load into Neo4j (only after I confirm Neo4j is running)
Ask me for: the Neo4j connection URI (local is usually `bolt://localhost:7687`, Aura is `neo4j+s://...`), the username (default `neo4j`) and the password. Do not write the password into any file.
Then run:
```
python pid_to_graph.py C01V04-VER.EX01.xml --neo4j <URI> --user <USER> --password <PASSWORD>
```
Then, using the neo4j Python driver, run queries 2, 3, 5 and 6 from `queries.cypher` and compare with the expected results:
- Query 2: 5 equipment (H1007, H1008, P4711, P4712, T4750)
- Query 3: H1007, H1008, P4712, T4750
- Query 5: 2 rows — PT4712.02→4712.02→PV4712.02→GlobeValve-1 and TT4750.03→4750.03→TV4750.03→GlobeValve-3
- Query 6: H1008, P4712, T4750
Report pass/fail. If the load fails, show me the error and fix only what is needed.

## Step 7 — README
Write a short `README.md` in `pid_kg` that lists: what the PoC does, how to re-run steps 3 and 6, and the licence note: pyDEXPI is AGPL-3.0 — free for this PoC/internal demo, but a commercial licence from TU Delft is needed before using it in a closed-source product or a hosted service.
