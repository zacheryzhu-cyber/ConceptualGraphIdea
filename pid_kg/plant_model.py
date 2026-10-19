"""
The complete graph, in memory. Neo4j holds only the conceptual level (plant_store.py); everything below it lives here
while a sheet is loaded and is kept as a file per sheet (snapshots/<sheet>.json).

    sheet_graph(assets, edges, sheet)   parse_sheet's rows -> networkx MultiDiGraph (node = asset uid, edge key =
                                        relationship type, every edge carries `sheet`)
    apply_decisions(G, decisions)       approved / rejected drawing links (sources/decisions.json, keyed by pair_key)
    to_export(G, ...) / from_export     the graph <-> the adjacency export format (plant_loader's former
                                        export_adjacency), which is also the snapshot file format
    plant_graph(snapshots, ...)         the sheets together: assets with the same uid merged, plus the links between
                                        sheets (off-page arrows with the same line number, open pipe ends of the same
                                        line, links.csv)

Drawing links: PIPE {source: 'drawing', status: 'candidate' | 'approved'}, proposed by the PDF check for pairs the
loader does not link. Candidates stay out of plant_graph (and so out of Neo4j) unless candidates=True; approved ones are
ordinary pipes. Links the loader infers from the drawing geometry (proteus_topology step 2b, "drawn touching") are
ordinary pipes from the start; the PDF check only confirms them.
"""
import csv
import os
from datetime import datetime

import networkx as nx

OFFPAGE_OUT, OFFPAGE_IN = "FlowOutPipeOffPageConnector", "FlowInPipeOffPageConnector"
# on every exported relationship: where it came from (dexpi or the drawing), its review status and its PDF check
EXPORT_REL_PROPS = ("source", "status", "pdfCheck")


def pair_key(a, b):
    """Key of a drawing link between two assets, either direction."""
    return "|".join(sorted((a, b)))


# --------------------------------------------------------------------------- one sheet

def sheet_graph(assets, edges, sheet):
    G = nx.MultiDiGraph(sheet=sheet)
    for a in assets:
        G.add_node(a["uid"], **a)
    for e in edges:
        if e["src"] in G and e["dst"] in G:
            G.add_edge(e["src"], e["dst"], key=_key(G, e["src"], e["dst"], e["rel"]), type=e["rel"],
                       **{**e["props"], "sheet": sheet})
    return G


def _key(G, src, dst, rel):
    """A free edge key for rel between src and dst (the same type may join two items twice)."""
    key, k = rel, 1
    while G.has_edge(src, dst, key):
        k += 1
        key = f"{rel}#{k}"
    return key


def add_edge(G, src, dst, rel, **props):
    G.add_edge(src, dst, key=_key(G, src, dst, rel), type=rel, **props)


def edges_between(G, a, b, rel=None):
    """(src, dst, key, data) of the links between a and b, either direction."""
    out = []
    for s, t in ((a, b), (b, a)):
        if G.has_edge(s, t):
            out += [(s, t, k, d) for k, d in G[s][t].items() if rel is None or d["type"] == rel]
    return out


def apply_decisions(G, decisions):
    """Approved drawing links get status 'approved', rejected ones are removed. Returns how many were removed."""
    gone = []
    for s, t, k, d in G.edges(keys=True, data=True):
        if d.get("source") != "drawing":
            continue
        decision = (decisions.get(pair_key(s, t)) or {}).get("decision")
        if decision == "rejected":
            gone.append((s, t, k))
        elif decision == "approved":
            d["status"] = "approved"
        else:
            d["status"] = "candidate"
    G.remove_edges_from(gone)
    return len(gone)


def drawing_links(G):
    """[(src, dst, data)] of the drawing links (candidates and approved)."""
    return [(s, t, d) for s, t, d in G.edges(data=True) if d.get("source") == "drawing"]


# --------------------------------------------------------------------------- graph <-> adjacency export / snapshot

def to_export(G, candidates=True, **header):
    """The adjacency export format: nodes (uid -> properties, labels, sheets) and adjacency (uid -> outgoing links
    [{to, toName, type, properties}], every node listed). Every link carries source, status and pdfCheck (None when
    not set). candidates=False leaves out the drawing links that are not approved yet."""
    nodes = {}
    for u in sorted(G.nodes):
        n = dict(G.nodes[u])
        n.setdefault("labels", sorted({str(n.get("category") or ""), str(n.get("type") or "")} - {""}))
        n["sheets"] = sorted(n.get("sheets") or ([G.graph["sheet"]] if G.graph.get("sheet") else []))
        nodes[u] = n
    adjacency = {u: [] for u in nodes}
    n_rels = 0
    for s, t, d in sorted(G.edges(data=True), key=lambda e: (e[0], e[2]["type"], e[1])):
        if not candidates and d.get("status") == "candidate":
            continue
        props = {**dict.fromkeys(EXPORT_REL_PROPS), **{k: v for k, v in d.items() if k != "type"}}
        adjacency[s].append({"to": t, "toName": nodes[t].get("name", t), "type": d["type"], "properties": props})
        n_rels += 1
    return {"format": "pid-kg-adjacency", "version": 1,
            "exported": datetime.now().isoformat(timespec="seconds"), **header, "directed": True,
            "counts": {"nodes": len(nodes), "relationships": n_rels}, "nodes": nodes, "adjacency": adjacency}


def from_export(export):
    G = nx.MultiDiGraph(sheet=(export.get("sheet") or {}).get("sheet"))
    for u, n in export["nodes"].items():
        G.add_node(u, **n)
    for src, rels in export["adjacency"].items():
        for r in rels:
            props = {k: v for k, v in r["properties"].items() if v is not None or k not in EXPORT_REL_PROPS}
            add_edge(G, src, r["to"], r["type"], **props)
    return G


# --------------------------------------------------------------------------- the plant: sheets together

def plant_graph(snapshots, links_csv=None, candidates=False):
    """One graph of the given sheet snapshots (in the order given; the first sheet showing a shared asset gives its
    properties, later ones fill in what it lacks), with the links between sheets:
        CONTINUES_TO {matchedBy: 'lineNumber'}     off-page out arrow -> in arrow with the same line number
        PIPE {crossSheet: true, line, fromSheet, toSheet}   open pipe end 'out' -> open end 'in' of the same line
        CONTINUES_TO {matchedBy: 'links.csv'}      links.csv rows (from_sheet,from_id,to_sheet,to_id)
    Returns (graph, issues) - issues: [{sheet, item, detail}] for assets merged with a type conflict."""
    G = nx.MultiDiGraph()
    issues = []
    for snap in snapshots:
        sheet = snap["sheet"]["sheet"]
        S = from_export(snap)
        for u, n in S.nodes(data=True):
            if u in G:
                cur = G.nodes[u]
                if n.get("type") != cur.get("type"):
                    issues.append({"sheet": sheet, "item": cur.get("name", u),
                                   "detail": f"{n.get('type')} here but {cur.get('type')} on another sheet; "
                                             f"kept {cur.get('type')}"})
                for k, v in n.items():
                    if k != "sheets":
                        cur.setdefault(k, v)
                cur["sheets"] = sorted(set(cur.get("sheets", [])) | set(n.get("sheets", [])))
            else:
                G.add_node(u, **n)
        for s, t, d in S.edges(data=True):
            if candidates or d.get("status") != "candidate":
                add_edge(G, s, t, d["type"], **{k: v for k, v in d.items() if k != "type"})

    # off-page arrows with the same line number on two sheets
    outs = [(u, n) for u, n in G.nodes(data=True) if n.get("type") == OFFPAGE_OUT and n.get("lineNumber")]
    ins = [(u, n) for u, n in G.nodes(data=True) if n.get("type") == OFFPAGE_IN and n.get("lineNumber")]
    for o, on in outs:
        for i, inn in ins:
            if on["lineNumber"] == inn["lineNumber"] and on.get("sheet") != inn.get("sheet"):
                add_edge(G, o, i, "CONTINUES_TO", matchedBy="lineNumber")
    # open pipe ends of the same line on two sheets
    ends = [{**e, "sheet": snap["sheet"]["sheet"]} for snap in snapshots for e in snap["sheet"].get("open_ends") or []]
    for o in (e for e in ends if e["dir"] == "out"):
        for i in (e for e in ends if e["dir"] == "in"):
            if o["line"] == i["line"] and o["sheet"] != i["sheet"] and o["asset"] in G and i["asset"] in G \
                    and o["asset"] != i["asset"]:
                add_edge(G, o["asset"], i["asset"], "PIPE", crossSheet=True, line=o["line"], fromSheet=o["sheet"],
                         toSheet=i["sheet"], dexpiType="Pipe", matchedBy="open pipe end")
    # links.csv: off-page arrows that line numbers can't match
    if links_csv and os.path.exists(links_csv):
        with open(links_csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                a, b = f"{r['from_sheet']}::{r['from_id']}", f"{r['to_sheet']}::{r['to_id']}"
                if a in G and b in G:
                    add_edge(G, a, b, "CONTINUES_TO", matchedBy="links.csv")
                else:
                    issues.append({"sheet": "", "item": f"{a} -> {b}", "detail": "links.csv row matches no loaded item"})
    return G, issues
