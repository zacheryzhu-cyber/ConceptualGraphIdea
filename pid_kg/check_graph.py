"""Offline check of output/nodes.csv + output/edges.csv with networkx (no Neo4j needed)."""
import csv

import networkx as nx

FLOW_TYPES = {"Pipe", "DirectPipingConnection"}

with open("output/nodes.csv", encoding="utf-8") as f:
    nodes = list(csv.DictReader(f))
with open("output/edges.csv", encoding="utf-8") as f:
    edges = list(csv.DictReader(f))

name = {n["id"]: n["name"] for n in nodes}
by_name = {n["name"]: n["id"] for n in nodes}
equipment_ids = {n["id"] for n in nodes if n["category"] == "Equipment"}

flow = nx.DiGraph()
flow.add_nodes_from(name)
flow.add_edges_from((e["source"], e["target"]) for e in edges if e["type"] in FLOW_TYPES)

results = []


def check(label, actual, expected):
    ok = actual == expected
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"       actual:   {actual}")
    if not ok:
        print(f"       expected: {expected}")


equipment = sorted(name[i] for i in equipment_ids)
check("Equipment assets", equipment, ["H1007", "H1008", "P4711", "P4712", "T4750"])

downstream = sorted(name[i] for i in nx.descendants(flow, by_name["P4711"]) & equipment_ids)
check("Equipment downstream of P4711", downstream, ["H1007", "H1008", "P4712", "T4750"])

path = [name[i] for i in nx.shortest_path(flow, by_name["P4711"], by_name["T4750"])]
check("Shortest flow path P4711 -> T4750", " -> ".join(path), "P4711 -> H1007 -> GlobeValve-2 -> T4750")

print(f"\n{sum(results)}/{len(results)} checks passed")
