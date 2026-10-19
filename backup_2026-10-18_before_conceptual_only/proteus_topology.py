"""
Fill the gaps pyDEXPI leaves in a sheet's graph, straight from the Proteus XML.

pyDEXPI's conceptual graph drops items it has no DEXPI class for and the links that hang off them.
Exports from COMOS (and other tools writing plain Proteus) lose a lot this way: shut-off and control
valves, pump motors, signal lines between instrument functions and actuators, and pipes drawn to a
pump that has connection points instead of nozzles. COMOS also writes some pipe segments with their
contents out of order and with wrong start / end references. This pass:

  1. adds plant items that are in the XML but not in the graph,
  2. links pipes from the drawing itself: every pipe centre line joins the items whose connection
     points sit at its two ends (in the direction it is drawn); the segment's recorded start / end is
     only used where nothing is drawn at a line end. A fitting drawn straight onto another item (a
     reducer on a pump nozzle, no pipe between) is joined to it too,
  3. attaches drives (a pump's motor, an agitator's motor) drawn on another piece of equipment to it,
  4. adds actuator -> valve references and signal / measuring lines (InformationFlow),
  5. reports pipe ends that stay open, and (report_unconnected, once dexpi_links has run too) instrument functions
     nothing connects to.

Links are only added between items that are not already connected, and instrument links only where
pyDEXPI left an end unconnected, so sheets pyDEXPI reads fully come out unchanged.
"""
import itertools
import math
import re
from collections import defaultdict

ITEM_TAGS = ("Equipment", "PipingComponent", "ProcessInstrumentationFunction",
             "ProcessSignalGeneratingFunction", "ActuatingFunction", "ActuatingSystem",
             "PipeOffPageConnector", "SignalOffPageConnector")
PROCESS_SIDE = ("Equipment", "Nozzle", "PipingComponent")
SNAP_MM = 1.0  # a line end and a connection point this close (drawing units, normally mm) are joined
PIPE_RELS = ("PIPE", "DIRECT_PIPING_CONNECTION", "CONTINUES_TO")
# parts of an equipment item's own body, not plant items in their own right (a vessel's shell, an exchanger's
# chambers and tube bundle); other sub-equipment, such as an agitator in a tank, is kept as part of its parent
STRUCTURAL_PARTS = ("Shell", "Chamber", "TubeBundle", "Jacket", "Channel", "Head", "Nozzle")


def complete(root, sheet, uid_by_pid, edges, issues, add_asset, rel_type):
    """Add missing items (through add_asset) and links to `edges` (changed in place). Returns links added.

    uid_by_pid: DEXPI ID -> asset uid for the items pyDEXPI already produced (extended in place).
    add_asset(element, category) -> uid: creates an asset row for an XML element.
    rel_type(dexpi class) -> relationship type, the same naming as the rest of the loader.
    """
    parent = {c: p for p in root.iter() for c in p}
    by_id = {el.get("ID"): el for el in root.iter() if el.get("ID")}
    catalogue = {el for sc in root.iter("ShapeCatalogue") for el in sc.iter()}
    before = len(edges)

    def ancestor(el, tags):
        el = parent.get(el)
        while el is not None and el.tag not in tags:
            el = parent.get(el)
        return el

    def item_of(el):
        """The plant item an XML element stands for: a nozzle is its equipment, an actuator part is
        its actuating system (or the pump it sits on), a connection point is its owner."""
        if el is None:
            return None
        if el.tag == "Node":
            el = parent.get(parent.get(el))
        if el is None:
            return None
        if el.tag == "Nozzle":
            el = ancestor(el, ("Equipment",))
        elif el.tag == "ActuatingSystemComponent":
            el = ancestor(el, ("ActuatingSystem",)) or ancestor(el, ("Equipment",))
        return el if el is not None and el.tag in ITEM_TAGS and el not in catalogue else None

    created = set()      # items this pass had to add

    def uid(el, create=True):
        el = item_of(el)
        if el is None:
            return None
        pid = el.get("ID")
        if pid not in uid_by_pid:
            if not create:
                return None
            uid_by_pid[pid] = add_asset(el, category(el))
            created.add(uid_by_pid[pid])
            owner = ancestor(el, ("Equipment",)) if el.tag == "Equipment" else None
            if owner is not None:  # sub-equipment is part of its parent
                link(uid(owner), uid_by_pid[pid], "COMPOSITION", "composition")
        return uid_by_pid[pid]

    def category(el):
        if el.tag == "Equipment":
            return "Equipment"
        if "Function" in el.tag or "Signal" in el.tag:
            return "Instrumentation"
        return "Piping"

    linked = set()       # undirected pairs already joined by a pipe
    signalled = set()    # undirected pairs already joined by anything else
    for e in edges:
        (linked if e["rel"] in PIPE_RELS else signalled).add(frozenset((e["src"], e["dst"])))
    isolated = set(uid_by_pid.values()) - {e["src"] for e in edges} - {e["dst"] for e in edges}

    def link(a, b, rel, kind, **props):
        if not a or not b or a == b:
            return
        pair = frozenset((a, b))
        seen = linked if rel in PIPE_RELS else signalled
        if pair in seen:
            return
        seen.add(pair)
        edges.append({"src": a, "dst": b, "rel": rel, "props": {"dexpiType": kind, **props}})

    # 1. items pyDEXPI did not produce (valves, tanks, instrument functions, agitators, ...). Sub-equipment with a
    #    tag of its own (an agitator AG001 in tank VE001) is added as part of its parent (COMPOSITION); structural parts
    #    (a vessel's shell) and untagged internals (a pump's impeller) are only added if a link needs them. Actuating
    #    systems (pyDEXPI models those as actuating functions) are also only added when a link needs them, so sheets
    #    pyDEXPI reads fully get no duplicates.
    def own_part(el):
        cls = (el.get("ComponentClass") or "").lower()
        return has_tag(el) and not any(part.lower() in cls for part in STRUCTURAL_PARTS)
    for el in by_id.values():
        if el.tag not in ITEM_TAGS or el.tag in ("ActuatingSystem", "ActuatingFunction") or el in catalogue:
            continue
        if ancestor(el, ("Equipment",)) is None or (el.tag == "Equipment" and own_part(el)):
            uid(el)

    # 2. pipes, from the drawing
    points = [(x, y, node) for x, y, node in connection_points(root, catalogue) if item_of(node) is not None]
    lines = []
    for seg in root.iter("PipingNetworkSegment"):
        if seg in catalogue:
            continue
        centre = [c for c in seg if c.tag == "CenterLine"]
        for k, cl in enumerate(centre):
            coords = cl.findall("Coordinate")
            if len(coords) >= 2:
                lines.append({"seg": seg, "ends": (xy(coords[0]), xy(coords[-1])),
                              "first": k == 0, "last": k == len(centre) - 1})
    at_item = {}                 # (line, end) -> connection point drawn there
    free = defaultdict(list)     # spot -> line ends with nothing drawn there
    for i, ln in enumerate(lines):
        for end, p in enumerate(ln["ends"]):
            node = nearest(points, p)
            if node is not None:
                at_item[(i, end)] = node
            else:
                free[spot(p)].append((i, end))
    # line pieces whose loose ends meet are one run of pipe
    run_of = list(range(len(lines)))

    def find(i):
        while run_of[i] != i:
            run_of[i] = run_of[run_of[i]]
            i = run_of[i]
        return i
    for ends in free.values():
        for (i, _), (j, _) in zip(ends, ends[1:]):
            run_of[find(i)] = find(j)
    runs = defaultdict(list)
    for i in range(len(lines)):
        runs[find(i)].append(i)
    for members in runs.values():
        ups, downs = [], []
        for i in members:
            ln = lines[i]
            for end, p in enumerate(ln["ends"]):
                node = at_item.get((i, end))
                if node is None and len(free[spot(p)]) == 1:  # loose end: fall back on the recorded start / end
                    node = recorded_end(ln, end, by_id)
                    if node is None:
                        issues.append(f"open pipe end ({'start' if end == 0 else 'end'} of segment "
                                      f"{ln['seg'].get('ID')}) at x={p[0]:g}, y={p[1]:g}: nothing drawn there; "
                                      "if the line continues on another sheet, add an off-page arrow or a links.csv row")
                if node is not None:
                    (ups if end == 0 else downs).append(node)
        ups, downs = unique(uid(n) for n in ups), unique(uid(n) for n in downs)
        line_no = line_number(lines[members[0]]["seg"], ancestor)
        props = {"lineNumber": line_no} if line_no else {}
        props["proteusId"] = lines[members[0]]["seg"].get("ID")
        if ups and downs:
            for a in ups:
                for b in downs:
                    link(a, b, "PIPE", "Pipe", **props)
        else:  # every item at the same kind of end: the drawing gives no direction
            items = ups or downs
            for a, b in zip(items, items[1:]):
                link(a, b, "PIPE", "Pipe", **props)

    # 2b. fittings drawn straight onto another item, with no pipe between (a reducer on a pump's inlet): the two
    #     connection points sit on the same spot. Only pairs with a piping component, so equipment drawn overlapping
    #     (a motor on its pump) is left to step 3.
    def starts_run(a, b):
        """True if the pipes so far put a after b: a only feeds pipes, or b only receives them."""
        out_of = lambda u: any(e["src"] == u and e["rel"] in PIPE_RELS for e in edges)
        into = lambda u: any(e["dst"] == u and e["rel"] in PIPE_RELS for e in edges)
        return out_of(a) and not into(a) or into(b) and not out_of(b)

    touching = defaultdict(list)
    for x, y, node in points:
        el = item_of(node)
        if el.tag in PROCESS_SIDE:
            touching[spot((x, y))].append((el, node.get("Name") or ""))
    for here in touching.values():
        for (a, na), (b, nb) in itertools.combinations(here, 2):
            if a is b or "PipingComponent" not in (a.tag, b.tag) or ancestor(a, ITEM_TAGS) is b \
                    or ancestor(b, ITEM_TAGS) is a:
                continue
            ua, ub = uid(a), uid(b)
            if is_inlet(na) and is_outlet(nb) or not (is_outlet(na) and is_inlet(nb)) and starts_run(ua, ub):
                (a, ua), (b, ub) = (b, ub), (a, ua)
            seg = ancestor(a if a.tag == "PipingComponent" else b, ("PipingNetworkSegment",))
            props = {"inferred": "drawn touching"}
            if seg is not None:
                props["proteusId"] = seg.get("ID")
                if line_number(seg, ancestor):
                    props["lineNumber"] = line_number(seg, ancestor)
            link(ua, ub, "PIPE", "Pipe", **props)

    # 3. drives (motors) drawn on a piece of equipment and connected to nothing else belong to it; the smallest item
    #    it is drawn on wins, so a tank's agitator motor goes to the agitator rather than the tank
    piped = {u for pair in linked for u in pair}
    equipment = [el for el in by_id.values()
                 if el.tag == "Equipment" and el not in catalogue and ancestor(el, ("Equipment",)) is None]
    hosts_all = [el for el in by_id.values()
                 if el.tag == "Equipment" and el not in catalogue and uid(el, create=False)]
    for el in equipment:
        u, p = uid(el, create=False), position(el)
        if not u or u in piped or p is None:
            continue
        hosts = [(area(b), h) for h in hosts_all if h is not el and (b := extent(h)) and inside(p, b)]
        if hosts:
            link(uid(min(hosts, key=lambda t: t[0])[1]), u, "COMPOSITION", "composition", inferred="drawn attached")

    # 4. actuator -> operated valve, for valves pyDEXPI gave no actuator (it models most through ActuatingFunction)
    operated = {e["dst"] for e in edges if e["rel"] == "OPERATED_VALVE_REFERENCE"}
    for ref in root.iter("ActuatingSystemComponent"):
        if ref.get("ComponentClass") != "OperatedValveReference" or ref in catalogue:
            continue
        for assoc in ref.findall("Association"):
            valve = uid(by_id.get(assoc.get("ItemID"))) if assoc.get("Type") == "refers to" else None
            if valve and valve not in operated:
                link(uid(ref), valve, "OPERATED_VALVE_REFERENCE", "OperatedValveReference")
                operated.add(valve)

    # signal and measuring lines, where pyDEXPI left an end unconnected or the end is an item added here
    # (on sheets pyDEXPI reads fully, the same loops are already there in its own form)
    for flow in root.iter("InformationFlow"):
        conn = flow.find("Connection")
        if flow in catalogue or conn is None:
            continue
        src, dst = by_id.get(conn.get("FromID")), by_id.get(conn.get("ToID"))
        if not ({uid(src, create=False), uid(dst, create=False)} & (isolated | created)):
            continue
        kind = flow.get("ComponentClass")
        if not kind:  # COMOS leaves the class out: process side -> instrument is a measurement
            kind = "MeasuringLineFunction" if src is not None and src.tag in PROCESS_SIDE else "SignalLineFunction"
        link(uid(src), uid(dst), rel_type(kind), kind)
    return len(edges) - before


def report_unconnected(root, uid_by_pid, edges, issues, skip_classes=()):
    """5. instrument functions still on their own: nothing in the export ties them to the process. Run once every
    pass has added its links (plant_loader.parse_sheet does)."""
    catalogue = {el for sc in root.iter("ShapeCatalogue") for el in sc.iter()}
    connected = {e["src"] for e in edges} | {e["dst"] for e in edges}
    for el in root.iter("ProcessInstrumentationFunction"):
        if el in catalogue or el.get("ComponentClass") in skip_classes or uid_by_pid.get(el.get("ID")) in connected:
            continue
        issues.append(f"instrument function {el.get('TagName') or el.get('ID')} ({el.get('ID')}) is not connected "
                      "to anything on the drawing; check its signal or measuring line in the source")


# --------------------------------------------------------------------------- drawing geometry

def xy(coord):
    return float(coord.get("X")), float(coord.get("Y"))


def spot(p):
    """Grid cell for matching line ends that meet (half a drawing unit)."""
    return round(p[0] * 2), round(p[1] * 2)


def has_tag(el):
    """True if the element carries a tag: a TagName attribute (COMOS, plain Proteus) or a DEXPI tag attribute."""
    if el.get("TagName"):
        return True
    return any(ga.get("Value") and ga.get("Name", "").startswith("TagName")
               for gas in el.findall("GenericAttributes") for ga in gas.findall("GenericAttribute"))


def connection_points(root, catalogue):
    """(x, y, Node element) for every drawn connection point."""
    out = []
    for cps in root.iter("ConnectionPoints"):
        if cps in catalogue:
            continue
        for node in cps.findall("Node"):
            loc = node.find("Position/Location")
            if loc is not None and node.get("Name") != "Origin":
                out.append((float(loc.get("X")), float(loc.get("Y")), node))
    return out


def nearest(points, p):
    best = min(((math.hypot(x - p[0], y - p[1]), node) for x, y, node in points), default=None, key=lambda t: t[0])
    return best[1] if best and best[0] <= SNAP_MM else None


def is_inlet(name):
    """Connection point named as an inlet (COMOS: '0.DI1')."""
    return re.search(r"(^|\.)DI\d*$", name) is not None


def is_outlet(name):
    return re.search(r"(^|\.)DO\d*$", name) is not None


def recorded_end(line, end, by_id):
    """The item the segment's Connection names for a loose end, unless it names the same item for both ends."""
    conn = line["seg"].find("Connection")
    if conn is None or conn.get("FromID") == conn.get("ToID"):
        return None
    if end == 0 and line["first"]:
        return by_id.get(conn.get("FromID"))
    if end == 1 and line["last"]:
        return by_id.get(conn.get("ToID"))
    return None


def line_number(seg, ancestor):
    system = ancestor(seg, ("PipingNetworkSystem",))
    return (system.get("TagName") if system is not None else None) or seg.get("TagName")


def position(el):
    loc = el.find("Position/Location")
    return None if loc is None else xy(loc)


def extent(el):
    ext = el.find("Extent")
    if ext is None or ext.find("Min") is None or ext.find("Max") is None:
        return None
    return (*xy(ext.find("Min")), *xy(ext.find("Max")))


def inside(p, box):
    return box[0] - SNAP_MM <= p[0] <= box[2] + SNAP_MM and box[1] - SNAP_MM <= p[1] <= box[3] + SNAP_MM


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def unique(items):
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out
