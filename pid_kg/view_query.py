"""
The Neo4j query that shows in Neo4j Browser what a graph view in the app shows (the graph's "Copy Neo4j query").

Conceptual level from Neo4j: a live query on the stored conceptual graph (Asset, FLOWS_TO, and with "Show loops"
Loop / Valve with MEASURES / CONTROLS), one per view mode, picking the same items as the app.

Process and Complete levels (and anything computed from the file snapshots) are not in Neo4j, so the query carries
the view itself: its items and links become APOC virtual nodes and relationships. Nothing is written to Neo4j, but
the APOC plugin is needed (check with  RETURN apoc.version(); ).
"""
import re

FLOW = "FLOWS_TO"
SIGNAL = "MEASURES|CONTROLS"
TIP = ("// Tip: in Neo4j Browser settings, turn off \"Connect result nodes\" so only the links below are drawn.\n")
NODE_KEYS = ("uid", "name", "tag", "fullTag", "type", "category", "description", "sheets", "folded", "instruments",
             "lineNumber", "valveRole", "group", "functions")
LINK_KEYS = ("via", "lines", "loops", "crossSheet", "sheet", "source", "status", "pdfCheck")


def lit(value):
    """A Cypher literal for a str / number / bool / list of those (None for anything else)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"
    if isinstance(value, (list, tuple)):
        items = [lit(v) for v in value if v is not None]
        return "[" + ", ".join(i for i in items if i is not None) + "]"
    return None


def props(d, keys):
    parts = []
    for k in keys:
        v = d.get(k)
        if v is None or v == [] or v == "":
            continue
        if isinstance(v, (list, tuple)) and not all(isinstance(x, (str, int, float, bool)) for x in v):
            v = [str(x) for x in v]
        text = lit(v)
        if text is not None:
            parts.append(f"{k}: {text}")
    return "{" + ", ".join(parts) + "}"


def label(text):
    t = re.sub(r"\W", "", str(text or "")) or "Item"
    return t if t[0].isalpha() else "T" + t


def header(title, lines):
    return "\n".join([f"// {title}"] + [f"// {line}" for line in lines]) + "\n"


def describe(view):
    bits = [view.get("mode", "")]
    for k, word in (("sheet", "sheet"), ("asset", "asset"), ("steps", "steps out")):
        if view.get(k):
            bits.append(f"{word} {view[k]}")
    return ", ".join(b for b in bits if b)


def conceptual(view, loops=False):
    """Live query on the conceptual graph stored in Neo4j for a view {mode, sheet?, assetUid?, steps?}."""
    mode = view.get("mode", "Whole plant")
    rels = f"{FLOW}|{SIGNAL}" if loops else FLOW
    is_node = "(n:Asset OR n:Loop OR n:Valve)" if loops else "n:Asset"
    title = f"Conceptual view from Neo4j: {describe(view)}" + (" (with loops)" if loops else "")
    ret = """UNWIND ns AS a
OPTIONAL MATCH (a)-[f:{rels}]->(b) WHERE b IN ns
RETURN a, f, b;""".format(rels=rels)
    if mode == "One sheet" and view.get("sheet"):
        pick = f"MATCH (n) WHERE {is_node} AND {lit(view['sheet'])} IN n.sheets\nWITH collect(n) AS ns"
    elif mode == "Around an asset" and view.get("assetUid"):
        pick = (f"MATCH (c {{uid: {lit(view['assetUid'])}}})-[:{rels}*0..{int(view.get('steps') or 1)}]-(n)\n"
                f"WHERE {is_node}\nWITH collect(DISTINCT n) AS ns")
    elif mode == "Downstream of an asset" and view.get("assetUid"):
        pick = (f"MATCH (c {{uid: {lit(view['assetUid'])}}})-[:{FLOW}*0..100]->(n:Asset)\n"
                "WITH collect(DISTINCT n) AS ns")
        ret = f"""UNWIND ns AS a
OPTIONAL MATCH (a)-[f:{FLOW}]->(b) WHERE b IN ns
RETURN a, f, b;"""
    else:
        pick = f"MATCH (n) WHERE {is_node}\nWITH collect(n) AS ns"
    return header(title, ["Live query on the conceptual graph stored in Neo4j (main equipment, off-page connectors,",
                          "FLOWS_TO links" + (", loops with MEASURES / CONTROLS)." if loops else ")."),
                          TIP.strip()[3:]]) + pick + "\n" + ret + "\n"


def embedded(subset_view, view, level, origin):
    """Query that draws a view computed outside Neo4j (Process / Complete, file snapshots) as virtual graph items.
    subset_view: graph_levels.subset(...) of what is shown."""
    nodes = subset_view["nodes"]
    n_rows, r_rows = [], []
    for u in sorted(nodes):
        n = nodes[u]
        labels = [label(n.get("category") or n.get("group") or "Item")]
        if n.get("type") and label(n["type"]) not in labels:
            labels.append(label(n["type"]))
        n_rows.append(f"  {{uid: {lit(u)}, labels: {lit(labels)}, props: {props({**n, 'uid': u}, NODE_KEYS)}}}")
    for u in sorted(subset_view["adjacency"]):
        for r in subset_view["adjacency"][u]:
            if u in nodes and r["to"] in nodes:
                r_rows.append(f"  {{s: {lit(u)}, t: {lit(r['to'])}, type: {lit(label(r['type']))}, "
                              f"props: {props(r.get('properties') or {}, LINK_KEYS)}}}")
    title = f"{level} view, {origin}: {describe(view)} ({len(n_rows)} items, {len(r_rows)} links)"
    return (header(title, ["These items are not stored in Neo4j (it holds only the conceptual level), so the query",
                           "carries them and draws them as virtual nodes and links. Nothing is written.",
                           "Needs the APOC plugin (check with  RETURN apoc.version(); ).", TIP.strip()[3:]])
            + "WITH [\n" + ",\n".join(n_rows) + "\n] AS ns, [\n" + ",\n".join(r_rows) + "\n] AS rs\n"
            + """UNWIND ns AS n
WITH rs, apoc.map.fromPairs(collect([n.uid, apoc.create.vNode(n.labels, n.props)])) AS v
RETURN [k IN keys(v) | v[k]] AS nodes,
       [r IN rs | apoc.create.vRelationship(v[r.s], r.type, r.props, v[r.t])] AS links;
""")


def for_view(plant_view, subset_view, view, level, origin, loops=False):
    """The query for what a graph view shows. origin 'Neo4j' (the stored conceptual graph): a live query;
    anything else: the view carried in the query."""
    if level == "Conceptual" and origin == "Neo4j":
        return conceptual(view, loops)
    return embedded(subset_view, view, level, origin)
