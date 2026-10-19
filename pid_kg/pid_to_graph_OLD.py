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
