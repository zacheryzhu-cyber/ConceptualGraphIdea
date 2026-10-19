"""
Detail levels of the graph view, after the three graph levels of ChatP&ID (TU Delft, arXiv 2603.22528):

    Complete    every object read from the sheets (their file snapshots)
    Process     pipe fittings collapsed: equipment, valves, off-page connectors and one node per instrument loop
    Conceptual  valves collapsed too: main equipment and off-page connectors, with the flow links between them

A level is computed in Python from the complete graph (an adjacency export, plant_store.PlantStore.export, made from
the sheets' file snapshots). plant_store writes the Conceptual level to Neo4j; Process and Complete are computed each
time a view is drawn. Which class goes where is configured in graph_levels.yaml, keyed by the DEXPI class name in
the node's `type`.

build(export, level) returns the same adjacency format as its input, with "level" in the header,
a `representative` map (stored uid -> the node that shows it at this level), and on top of the stored properties:
    nodes:  folded       what was folded into the node ("motor M1 (...)", "actuated, loop Y024", "alarm LISA.AH")
            instruments  Conceptual: loops on that equipment ("L001 LISA, T001 TI")
    links:  via / lines  the collapsed items passed, in order, and the pipe lines (FullLabel) passed
            crossSheet   the flow crosses to another sheet (drawn dashed)
            source / status / pdfCheck   from the stored links passed (link_origin): a flow through a pipe proposed
                         by the PDF drawing check keeps source 'drawing'
            loops        Conceptual: loops measuring or acting on the pipe or valves along the link
            loopLink     Process: a loop's link to what its members measure or act on (drawn dotted)
            viaUids      the uids of the items in via (to show a link's detail from the Complete graph)
    loopTargets  Process / Conceptual: loop uid -> [{target, rel, out, acts}], what each loop (or lone instrument)
                 measures or acts on, as the Process node that shows it. acts: the stored item at that end is a valve,
                 an actuator part or a drive (fold_into_equipment) rather than equipment, a nozzle or a pipe.
"""
import os
from collections import Counter, defaultdict, deque

import yaml

LEVELS = ("Conceptual", "Process", "Complete")
FLOW_RELS = ("PIPE", "DIRECT_PIPING_CONNECTION", "CONTINUES_TO", "FLOWS_TO")   # FLOWS_TO: the conceptual level in Neo4j
FLOW_GROUPS = ("main_equipment", "valve", "boundary")
KEPT = {"Process": FLOW_GROUPS, "Conceptual": ("main_equipment", "boundary")}
FOLDED = ("fold_into_equipment", "fold_into_valve", "fold_into_instrument", "instrument")
# relationships that tie an item to the node it folds into, rather than to what it measures or acts on
STRUCTURE = ("IS_A_COLLECTION_INCLUDING", "HAS_ALARM", "COMPOSITION", "CONTAINS")
# a loop linked to an item of these groups acts on it (drives a valve or a motor) rather than measuring
ACTING_GROUPS = ("valve", "fold_into_valve", "fold_into_equipment")
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph_levels.yaml")


def load_config(path=CONFIG):
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    groups = {}
    for group, types in raw.items():
        if group != "fold_into_equipment_descriptions" and isinstance(types, list):
            for t in types:
                groups.setdefault(str(t), group)
    return {"groups": groups, "descriptions": tuple(raw.get("fold_into_equipment_descriptions") or ())}


def classify(node, config):
    """(group, listed): the node's group, and whether graph_levels.yaml lists it (False: the category rule)."""
    t = str(node.get("type") or "")
    if t in config["groups"]:
        return config["groups"][t], True
    if config["descriptions"] and str(node.get("description") or "").startswith(config["descriptions"]):
        return "fold_into_equipment", True
    return ("main_equipment" if node.get("category") == "Equipment" else "pass_through"), False


def unclassified(export, config=None):
    """Types in the graph that graph_levels.yaml doesn't list: [{type, category, count, treated as}]."""
    config = config or load_config()
    seen = Counter()
    for n in export["nodes"].values():
        group, listed = classify(n, config)
        if not listed:
            seen[(n.get("type") or "", n.get("category") or "", group)] += 1
    return [{"type": t, "category": c, "count": k, "treated as": g} for (t, c, g), k in sorted(seen.items())]


def short(name):
    """Last part of a tag: '=P1.H3.T2.L001' -> 'L001', 'GO+- (P1.H3.T1.Y022)' -> 'GO+-'."""
    return str(name).split(" (")[0].rstrip(".").split(".")[-1]


class _Graph:
    def __init__(self, export):
        self.nodes = export["nodes"]
        self.out, self.inn, self.edges = defaultdict(list), defaultdict(list), []
        for src, rels in export["adjacency"].items():
            for r in rels:
                if src in self.nodes and r["to"] in self.nodes:
                    e = (src, r["to"], r["type"], r.get("properties") or {})
                    self.edges.append(e)
                    self.out[src].append(e)
                    self.inn[r["to"]].append(e)

    def name(self, u):
        return str(self.nodes[u].get("name") or u)

    def nearest(self, starts, kept):
        """Nearest kept node along the pipes, either direction (first found, in a stable order)."""
        seen, queue = set(starts), deque(sorted(starts))
        while queue:
            x = queue.popleft()
            if x in kept:
                return x
            nxt = [t for _, t, typ, _ in self.out[x] if typ in FLOW_RELS] + \
                  [s for s, _, typ, _ in self.inn[x] if typ in FLOW_RELS]
            for y in sorted(set(nxt) - seen):
                seen.add(y)
                queue.append(y)
        return None


def build(export, level, config=None):
    """The graph at one detail level, in adjacency export format (see the module docstring)."""
    head = {k: v for k, v in export.items() if k not in ("nodes", "adjacency", "counts")}
    if level == "Complete":
        return {**head, "level": level, "counts": export["counts"], "nodes": export["nodes"],
                "adjacency": export["adjacency"], "representative": {u: u for u in export["nodes"]}}
    if level not in KEPT:
        raise ValueError(f"unknown level {level!r}; one of {', '.join(LEVELS)}")
    config = config or load_config()
    g = _Graph(export)
    group = {u: classify(n, config)[0] for u, n in g.nodes.items()}
    flow = {u for u in g.nodes if group[u] in FLOW_GROUPS}       # on the pipes at Process
    kept = {u for u in g.nodes if group[u] in KEPT[level]}       # on the pipes at this level

    # ---- what each folded item belongs to (its host at the Process level)
    def host(u, depth=0):
        grp = group[u]
        if depth > 20 or grp not in FOLDED:
            return u
        if grp == "fold_into_equipment":   # motor / agitator -> the equipment it is part of
            parents = [s for s, _, typ, _ in g.inn[u] if typ == "COMPOSITION"]
            for p in sorted(parents):
                h = host(p, depth + 1)
                if group.get(h) == "main_equipment":
                    return h
            return None
        if grp == "fold_into_valve":       # actuator parts -> the valve they operate
            seen, queue = {u}, deque([u])
            while queue:
                x = queue.popleft()
                for y in sorted({t for _, t, _, _ in g.out[x]} | {s for s, _, _, _ in g.inn[x]}):
                    if group[y] == "valve":
                        return y
                    if group[y] == "fold_into_valve" and y not in seen:
                        seen.add(y)
                        queue.append(y)
            return None
        if grp == "instrument":            # instrument function -> its loop
            loops = sorted(s for s, _, typ, _ in g.inn[u] if typ == "IS_A_COLLECTION_INCLUDING" and group[s] == "loop")
            return loops[0] if loops else None
        if grp == "fold_into_instrument":  # alarm -> its instrument (-> that instrument's loop)
            for s in sorted(s for s, _, typ, _ in g.inn[u] if typ == "HAS_ALARM"):
                return host(s, depth + 1) or s
            return None

    hosts = {u: host(u) for u in g.nodes}
    # Process nodes: the pipe network, loops, and anything folded that has nowhere to fold into
    signal_nodes = {u for u in g.nodes if group[u] == "loop" or (group[u] in FOLDED and hosts[u] is None)}
    shown = kept | (signal_nodes if level == "Process" else set())

    def at_process(u):
        """The Process node that shows u (itself, its host, or the nearest pipe-network item for a line / fitting)."""
        h = hosts[u] if group[u] in FOLDED else u
        if h is None:
            h = u
        if h in flow or h in signal_nodes:
            return h
        if group[h] == "line":
            contained = [t for _, t, typ, _ in g.out[h] if typ == "CONTAINS"]
            upstream = [c for c in contained
                        if not any(s in contained for s, _, typ, _ in g.inn[c] if typ in FLOW_RELS)]
            return g.nearest(upstream or contained, flow)
        return g.nearest([h], flow)

    # ---- flow links: from each kept node along the pipes, through what isn't kept, to each kept node reached
    links = defaultdict(dict)    # src -> dst -> link
    for k in sorted(kept):
        prev, queue = {k: None}, deque([k])
        while queue:
            x = queue.popleft()
            for _, t, typ, p in g.out[x]:
                if typ not in FLOW_RELS or t in prev:
                    continue
                prev[t] = (x, typ, p)
                if t not in kept:
                    queue.append(t)
                elif t != k and t not in links[k]:
                    path, steps, y = [], [], t
                    while prev[y] is not None:
                        x0, typ0, p0 = prev[y]
                        steps.append((typ0, p0))
                        if x0 != k:
                            path.append(x0)
                        y = x0
                    path.reverse()
                    steps.reverse()
                    lines = []
                    for _, p0 in steps:
                        line = p0.get("line") or p0.get("lineNumber")
                        if line and line not in lines:
                            lines.append(str(line))
                    sheets = {p0.get("sheet") for _, p0 in steps}
                    props = {"via": [g.name(v) for v in path], "viaUids": path, "lines": lines,
                             "crossSheet": any(p0.get("crossSheet") or typ0 == "CONTINUES_TO" for typ0, p0 in steps)}
                    if len(sheets) == 1 and None not in sheets:
                        props["sheet"] = sheets.pop()
                    props.update(link_origin([p0 for _, p0 in steps]))
                    rel = steps[0][0] if len(steps) == 1 else "PIPE"
                    links[k][t] = {"to": t, "type": rel, "properties": props}

    # ---- loops: what their members (and items folded into them) measure or act on
    loop_targets = defaultdict(list)   # loop / lone instrument -> [(Process target, relationship, towards target, acts)]
    for a, b, typ, p in g.edges:
        if typ in FLOW_RELS or typ in STRUCTURE:
            continue
        ra, rb = at_process(a), at_process(b)
        if ra is None or rb is None or (ra in signal_nodes) == (rb in signal_nodes):
            continue
        sig, other, out, stored = (ra, rb, True, b) if ra in signal_nodes else (rb, ra, False, a)
        if other in flow and all(t != other for t, _, _, _ in loop_targets[sig]):
            loop_targets[sig].append((other, typ, out, group[stored] in ACTING_GROUPS))

    folded = defaultdict(list)
    for u in sorted(g.nodes, key=g.name):
        h = hosts[u] if group[u] in FOLDED else None
        if h is None:
            continue
        if group[u] == "fold_into_equipment":
            word = next((d for d in config["descriptions"] if str(g.nodes[u].get("description") or "").startswith(d)),
                        g.nodes[u].get("type") or "")
            folded[h].append(f"{str(word).split()[-1].lower()} {g.name(u)}")
        elif group[u] == "fold_into_instrument":
            folded[h].append(f"{str(g.nodes[u].get('type')).lower()} {g.name(u)}")
        elif group[u] == "instrument" and level == "Process":
            folded[h].append(g.name(u))
    members = defaultdict(list)
    for u in g.nodes:
        if group[u] == "instrument" and hosts[u]:
            members[hosts[u]].append(short(g.nodes[u].get("fullTag") or g.name(u)))

    def loop_label(sig):
        tag = short(g.name(sig))
        return f"{tag} {'/'.join(sorted(members[sig]))}" if members[sig] else tag

    valve_notes = defaultdict(list)
    for u in g.nodes:
        if group[u] == "fold_into_valve" and hosts[u] and "actuated" not in valve_notes[hosts[u]]:
            valve_notes[hosts[u]].insert(0, "actuated")

    adjacency = defaultdict(list)
    for k in links:
        adjacency[k] += list(links[k].values())
    instruments = defaultdict(list)
    for sig in sorted(loop_targets, key=g.name):
        for target, typ, out, _ in loop_targets[sig]:
            if group[target] == "valve" and f"loop {short(g.name(sig))}" not in valve_notes[target]:
                valve_notes[target].append(f"loop {short(g.name(sig))}")
            if level == "Process":
                src, dst = (sig, target) if out else (target, sig)
                adjacency[src].append({"to": dst, "type": typ, "properties": {"loopLink": True}})
            elif target in kept:
                instruments[target].append(loop_label(sig))
            else:   # on a valve or pipe: the flow links through it carry the loop, else the nearest equipment
                through = [l for src in links for l in links[src].values() if target in l["properties"]["viaUids"]]
                for l in through:
                    l["properties"].setdefault("loops", []).append(loop_label(sig))
                near = None if through else g.nearest([target], kept)
                if near:
                    instruments[near].append(loop_label(sig))
    if level == "Process":
        for v, notes in valve_notes.items():
            folded[v].append(", ".join(notes))

    # ---- representative: which shown node stands for each stored one (to centre a view on any asset)
    rep = {}
    for u in g.nodes:
        r = at_process(u)
        if level == "Conceptual" and r is not None and r not in kept:
            targets = [r] if r in flow else [t for t, _, _, _ in loop_targets.get(r, [])]
            r = next((t for t in targets if t in kept), None) or (g.nearest(targets[:1], kept) if targets else None)
        rep[u] = r if r in shown else None

    nodes = {}
    for u in shown:
        n = dict(g.nodes[u])
        n["group"] = group[u]
        if folded[u]:
            n["folded"] = folded[u]
        if instruments[u]:
            n["instruments"] = sorted(set(instruments[u]))
        nodes[u] = n
    names = {u: g.name(u) for u in g.nodes}
    adj = {u: [] for u in sorted(nodes)}
    for src, rels in adjacency.items():
        for r in rels:
            adj[src].append({"to": r["to"], "toName": names[r["to"]], "type": r["type"], "properties": r["properties"]})
    for rels in adj.values():
        rels.sort(key=lambda r: (r["type"], r["to"]))
    n_rels = sum(len(r) for r in adj.values())
    targets = {sig: [{"target": t, "rel": typ, "out": out, "acts": acts} for t, typ, out, acts in loop_targets[sig]]
               for sig in sorted(loop_targets)}
    return {**head, "level": level, "counts": {"nodes": len(nodes), "relationships": n_rels},
            "nodes": nodes, "adjacency": adj, "representative": rep, "loopTargets": targets}


def link_origin(steps):
    """source / status / pdfCheck of a flow link from the stored links it passes: source 'drawing' if one of them was
    proposed by the PDF check (status 'candidate' while any such step is unreviewed, else 'approved'); pdfCheck
    'not_drawn' if any step is not drawn, 'confirmed' if every step is."""
    drawing = [p.get("status") for p in steps if p.get("source") == "drawing"]
    checks = [p.get("pdfCheck") for p in steps]
    return {"source": "drawing" if drawing else None,
            "status": ("candidate" if "candidate" in drawing else "approved") if drawing else None,
            "pdfCheck": "not_drawn" if "not_drawn" in checks else
                        "confirmed" if checks and all(c in ("confirmed", "drawn_only") for c in checks) else None}


def subset(view, uids):
    """The part of a level graph between `uids` (same format)."""
    keep = set(uids) & set(view["nodes"])
    adj = {u: [r for r in view["adjacency"].get(u, []) if r["to"] in keep] for u in sorted(keep)}
    return {**view, "nodes": {u: view["nodes"][u] for u in keep}, "adjacency": adj,
            "counts": {"nodes": len(keep), "relationships": sum(len(r) for r in adj.values())}}


def neighbourhood(view, start, steps):
    """Shown nodes up to `steps` links from start, either direction."""
    nbrs = defaultdict(set)
    for u, rels in view["adjacency"].items():
        for r in rels:
            nbrs[u].add(r["to"])
            nbrs[r["to"]].add(u)
    seen, frontier = {start}, {start}
    for _ in range(steps):
        frontier = {v for u in frontier for v in nbrs[u]} - seen
        seen |= frontier
    return seen


def downstream(view, start):
    """Shown nodes reached from start along flow links, in their direction (start included)."""
    seen, queue = {start}, deque([start])
    while queue:
        u = queue.popleft()
        for r in view["adjacency"].get(u, []):
            if r["type"] in FLOW_RELS and r["to"] not in seen:
                seen.add(r["to"])
                queue.append(r["to"])
    return seen


def from_rows(assets, edges, sheet=""):
    """An adjacency export straight from parse_sheet's rows (no database), e.g. for tests."""
    nodes = {a["uid"]: {**a, "sheets": [sheet] if sheet else []} for a in assets}
    adj = {u: [] for u in nodes}
    for e in edges:
        if e["src"] in nodes and e["dst"] in nodes:
            adj[e["src"]].append({"to": e["dst"], "toName": nodes[e["dst"]]["name"], "type": e["rel"],
                                  "properties": {**e["props"], "sheet": sheet}})
    return {"format": "pid-kg-adjacency", "version": 1, "directed": True, "nodes": nodes, "adjacency": adj,
            "counts": {"nodes": len(nodes), "relationships": sum(len(r) for r in adj.values())}}
