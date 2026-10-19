"""
Links read straight from the DEXPI / Proteus XML, by DEXPI association and class names.

Runs after pyDEXPI (plant_loader.parse_sheet) and the drawing pass (proteus_topology.complete) have built a sheet's
graph, and adds what both leave out. Every rule follows the XML structure and DEXPI names only, so it applies to any
DEXPI file; nothing here knows a tag, an ID or a sheet. Relationship names are the DEXPI association / class names
in upper case (rel_type), like OPERATED_VALVE_REFERENCE and MEASURING_LINE_FUNCTION elsewhere in the loader.

  1. ActuatingSystem -[:FULFILLS]-> ActuatingFunction             <Association Type="fulfills">
  2. InstrumentationLoopFunction node -[:IS_A_COLLECTION_INCLUDING]-> each member   <Association Type="is a collection
     including">; uid <sheet>::<ID>, name its FullLabel (or TagName)
  3. InformationFlow (measuring / signal line) between its logical start and end. An end on a nozzle goes to the
     equipment that owns it, with the nozzle tag kept as the property `nozzle`
  4. ... and an end on a pipe centre line goes to that pipe line: a PipingNetworkSystem node (uid LINE:<FullLabel>),
     which -[:CONTAINS]-> the components of that segment
  5. PipingNetworkSegment: PIPE along [FromID] + inline items + [ToID], where the existing pipes don't already join
     the two neighbours (property `line` = the PipingNetworkSystem's FullLabel)
  6. Alarms: an Equipment with no ComponentClass whose FullLabel is an instrument function's FullLabel plus one more
     part (COMOS writes alarms this way) becomes type Alarm, and the function -[:HAS_ALARM]-> it
  7. Export artefacts (SKIP_CLASSES) are left out of the graph
  8. Open pipe ends (a segment with an empty FromID or ToID) are returned, so plant_loader.link_sheets can join the
     two ends of the same line on different sheets

owner_uid(xml_id) maps any XML ID to an existing graph node: the node <sheet>::<ID> or the node the loader made from
that ID, else the node whose fullTag is the element's FullLabel, else (for a Nozzle, a COMOS <Nozzles>, an
ActuatingSystemComponent or a CenterLine) its parent element, the same way up.
"""
from collections import Counter, defaultdict

from proteus_topology import ITEM_TAGS, PIPE_RELS

SKIP_CLASSES = ("OrphanInformationFlowCollectorDummy",)   # COMOS export artefacts, not plant items
WALK_UP = ("Nozzle", "Nozzles", "ActuatingSystemComponent", "CenterLine")
NOZZLES = ("Nozzle", "Nozzles")                           # COMOS writes <Nozzles>, which the XSD doesn't have
ASSOCIATIONS = ("fulfills", "is a collection including")  # followed one way only; the inverse names are skipped
PROCESS_SIDE = ("Equipment", "Nozzle", "Nozzles", "PipingComponent", "CenterLine", "PipingNetworkSegment")
INSTRUMENT_TAGS = ("ProcessInstrumentationFunction",)


def full_label(el):
    """The element's FullLabel GenericAttribute (COMOS writes it in the 'ComosProperties' set), or None."""
    for gas in el.findall("GenericAttributes"):
        for ga in gas.findall("GenericAttribute"):
            if ga.get("Name") == "FullLabel" and ga.get("Value"):
                return ga.get("Value")
    return None


def segment_chain(seg, owner):
    """Rule 5: the items a PipingNetworkSegment runs through, in order: its FromID, its inline items, its ToID, each
    mapped by owner(xml_id) (None: not a node) and repeats dropped. A segment recording the same item for both ends
    uses neither end. Also used by crosscheck.pdf_vs_dexpi, with its own owner."""
    conn = seg.find("Connection")
    start, end = (conn.get("FromID"), conn.get("ToID")) if conn is not None else (None, None)
    if start and start == end:  # the same item recorded for both ends: one of them is wrong, use neither
        start = end = None
    chain = []
    for u in [owner(start)] + [owner(c.get("ID")) for c in seg if c.tag in ITEM_TAGS] + [owner(end)]:
        if u and (not chain or chain[-1] != u):
            chain.append(u)
    return chain


def enrich(root, sheet, assets, uid_by_pid, edges, issues, rel_type):
    """Add the links above to `edges` and the nodes they need to `assets` (uid -> asset row), both changed in place.

    uid_by_pid: DEXPI ID -> uid for the items pyDEXPI and proteus_topology produced.
    Returns (open_ends, rules): open_ends [{line, dir: 'out'|'in', asset}], rules Counter of what each rule added.
    """
    parent = {c: p for p in root.iter() for c in p}
    catalogue = {el for sc in root.iter("ShapeCatalogue") for el in sc.iter()}
    by_id = {el.get("ID"): el for el in root.iter() if el.get("ID") and el not in catalogue}
    by_label = {}
    for el in by_id.values():
        by_label.setdefault(full_label(el), el)
    by_full = {a["fullTag"]: u for u, a in assets.items() if a.get("fullTag")}
    rules = Counter()

    # 7. export artefacts out first, so nothing below links to them
    for el in by_id.values():
        if el.get("ComponentClass") in SKIP_CLASSES:
            for u in {f"{sheet}::{el.get('ID')}", uid_by_pid.get(el.get("ID"))} & set(assets):
                del assets[u]
                edges[:] = [e for e in edges if u not in (e["src"], e["dst"])]
                rules["skipped artefact"] += 1

    def owner(xml_id):
        """(uid, path): the graph node for an XML ID, and the elements walked through to reach it."""
        el, path = by_id.get(xml_id) if xml_id else None, []
        while el is not None:
            path.append(el)
            pid = el.get("ID")
            for u in (f"{sheet}::{pid}", uid_by_pid.get(pid)) if pid else ():
                if u in assets:
                    return u, path
            label = full_label(el)
            if label in by_full and by_full[label] in assets:
                return by_full[label], path
            if el.tag not in WALK_UP:
                break
            el = parent.get(el)
        return None, path

    def owner_uid(xml_id):
        return owner(xml_id)[0]

    piped = defaultdict(set)         # uid -> uids it shares a pipe with (as loaded before this pass)
    for e in edges:
        if e["rel"] in PIPE_RELS:
            piped[e["src"]].add(e["dst"])
            piped[e["dst"]].add(e["src"])
    directed = {(e["src"], e["dst"], e["rel"]) for e in edges}
    signalled = {frozenset((e["src"], e["dst"])) for e in edges if e["rel"] not in PIPE_RELS}

    def add(src, dst, rel, rule, **props):
        if not src or not dst or src == dst or (src, dst, rel) in directed:
            return False
        directed.add((src, dst, rel))
        if rel not in PIPE_RELS:
            signalled.add(frozenset((src, dst)))
        edges.append({"src": src, "dst": dst, "rel": rel, "props": {k: str(v) for k, v in props.items() if v}})
        rules[rule] += 1
        return True

    def new_node(uid, name, dtype, category, tagged, el, label=None):
        if uid not in assets:
            row = {"uid": uid, "name": name, "type": dtype, "category": category, "tagged": tagged,
                   "proteusId": el.get("ID")}
            if label:
                row["fullTag"] = label
            if not tagged:
                row["sheet"] = sheet
            assets[uid] = row
            rules[f"{dtype} node"] += 1
        return uid

    # 1. / 2. associations: ActuatingSystem fulfills ActuatingFunction; a loop is a collection including its members
    for el in by_id.values():
        for assoc in el.findall("Association"):
            kind = assoc.get("Type")
            if kind not in ASSOCIATIONS:
                continue
            src, dst = owner_uid(el.get("ID")), owner_uid(assoc.get("ItemID"))
            if src is None and dst and el.tag == "InstrumentationLoopFunction":
                label = full_label(el)
                src = new_node(f"{sheet}::{el.get('ID')}", label or el.get("TagName") or el.get("ID"),
                               el.get("ComponentClass") or el.tag, "Instrumentation", False, el, label)
            add(src, dst, rel_type(kind), kind, dexpiType=kind)

    # 4. a pipe line as a node: the PipingNetworkSystem, holding the components of the segment that was referenced
    def line_node(el):
        seg = el if el.tag == "PipingNetworkSegment" else parent.get(el)
        if seg is None or seg.tag != "PipingNetworkSegment":
            return None
        system = parent.get(seg)
        system = system if system is not None and system.tag == "PipingNetworkSystem" else seg
        label = full_label(system) or system.get("TagName")
        uid = (new_node(f"LINE:{label}", label, "PipingNetworkSystem", "Piping", True, system, full_label(system))
               if label else new_node(f"{sheet}::{system.get('ID')}", system.get("ID"), "PipingNetworkSystem",
                                      "Piping", False, system))
        for item in seg:
            if item.tag in ITEM_TAGS and item.get("ID"):
                add(uid, owner_uid(item.get("ID")), "CONTAINS", "line contains", dexpiType="contains")
        return uid

    # 3. / 4. measuring and signal lines between their logical start and end
    for flow in by_id.values():
        if flow.tag != "InformationFlow":
            continue
        logical = {a.get("Type"): a.get("ItemID") for a in flow.findall("Association")}
        conn = flow.find("Connection")
        ends = [logical.get("has logical start") or (conn.get("FromID") if conn is not None else None),
                logical.get("has logical end") or (conn.get("ToID") if conn is not None else None)]
        uids, nozzles, rule = [], [], "information flow"
        for xml_id in ends:
            uid, path = owner(xml_id)
            if uid is None and path and path[-1].tag in ("CenterLine", "PipingNetworkSegment"):
                uid, rule = line_node(path[-1]), "information flow to pipe line"
            nozzles += [n.get("TagName") or full_label(n) or n.get("ID") for n in path if n.tag in NOZZLES]
            uids.append(uid)
        if None in uids or frozenset(uids) in signalled:
            continue
        kind = flow.get("ComponentClass")
        if not kind:  # class left out (COMOS): process side -> instrument is a measurement, as in proteus_topology
            start = by_id.get(ends[0])
            kind = "MeasuringLineFunction" if start is not None and start.tag in PROCESS_SIDE else "SignalLineFunction"
        if nozzles and rule == "information flow":
            rule = "information flow to nozzle"
        add(uids[0], uids[1], rel_type(kind), rule, dexpiType=kind, proteusId=flow.get("ID"),
            nozzle=", ".join(nozzles))

    # 5. pipes along each segment, and 8. its open ends
    open_ends = []
    line_of = {}       # segment ID -> its pipe line's FullLabel (or tag)
    for seg in by_id.values():
        if seg.tag != "PipingNetworkSegment":
            continue
        system = parent.get(seg)
        line = (full_label(system) or system.get("TagName")) if system is not None else None
        line = line or full_label(seg) or seg.get("TagName")
        if line:
            line_of[seg.get("ID")] = line
        conn = seg.find("Connection")
        chain = segment_chain(seg, owner_uid)
        # only fill gaps: two neighbours the existing pipes on this segment already join (directly or through the
        # segment's other items) are left alone, so a segment listed out of order adds no second route
        group = {u: u for u in chain}

        def find(u):
            while group[u] != u:
                u = group[u]
            return u
        for u in chain:
            for v in piped[u] & set(chain):
                group[find(u)] = find(v)
        for a, b in zip(chain, chain[1:]):
            if find(a) != find(b):
                add(a, b, "PIPE", "pipe along segment", dexpiType="Pipe", proteusId=seg.get("ID"), line=line)
                group[find(a)] = find(b)
        if conn is not None and chain and line:
            if not conn.get("ToID"):
                open_ends.append({"line": line, "dir": "out", "asset": chain[-1]})
            if not conn.get("FromID"):
                open_ends.append({"line": line, "dir": "in", "asset": chain[0]})
    rules["open pipe end"] += len(open_ends)
    # every pipe drawn on a segment gets that segment's line (the views list the lines a flow passes)
    for e in edges:
        if e["rel"] in PIPE_RELS and not e["props"].get("line") and e["props"].get("proteusId") in line_of:
            e["props"]["line"] = line_of[e["props"]["proteusId"]]
            rules["pipe line named"] += 1

    # 6. alarms: Equipment with no class, named after an instrument function plus one part (=...LISA -> =...LISA.AH)
    for el in by_id.values():
        label = full_label(el)
        if el.tag != "Equipment" or el.get("ComponentClass") or not label or "." not in label:
            continue
        instrument = by_label.get(label.rsplit(".", 1)[0])
        alarm = owner_uid(el.get("ID"))
        if instrument is None or instrument.tag not in INSTRUMENT_TAGS or alarm is None:
            continue
        assets[alarm].update(type="Alarm", category="Instrumentation")
        add(owner_uid(instrument.get("ID")), alarm, "HAS_ALARM", "alarm", dexpiType="alarm")

    return open_ends, rules
