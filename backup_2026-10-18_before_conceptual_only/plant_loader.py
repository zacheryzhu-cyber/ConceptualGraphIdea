"""
Multi-sheet P&ID -> plant knowledge graph loader (DEXPI / Proteus XML -> Neo4j).

Every sheet you load is ADDED to the plant graph. Loading a sheet that is already
in the graph replaces only that sheet; the rest of the plant is untouched.

Connection (set once in your terminal, so the password is never written to a file):
    Windows PowerShell:  $env:NEO4J_URI="bolt://localhost:7687"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="..."
    Mac/Linux:           export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=...

Commands:
    python plant_loader.py load  sheet.xml --sheet 101 --area "Area 1" --system "Cooling Water" --revision A
    python plant_loader.py load  pids_folder/                      (every DEXPI file in the folder: .xml or COMOS export)
    python plant_loader.py load  pids_folder/ --manifest manifest.csv
    python plant_loader.py remove --sheet 101
    python plant_loader.py summary
    python plant_loader.py report                                  (writes output/validation_report.csv)
    python plant_loader.py reset --yes                             (deletes the whole plant graph and manual edits;
                                                                    add --keep-edits to keep the edits)

manifest.csv columns: file,sheet,revision,area,system
links.csv (optional, for off-page arrows that line numbers can't match):
    from_sheet,from_id,to_sheet,to_id     (ids are the DEXPI IDs, e.g. FlowOutPipeOffPageConnector-1)

Graph model:
    (Plant)-[:HAS_AREA]->(Area)-[:HAS_SYSTEM]->(System)-[:HAS_SHEET]->(Sheet)-[:DEPICTS]->(Asset)
    (Asset)-[:PIPE | DIRECT_PIPING_CONNECTION | MEASURING_LINE_FUNCTION | ...]->(Asset)   topology, per sheet
    (off-page out)-[:CONTINUES_TO]->(off-page in)                                         across sheets

Asset identity:
    Tagged items (equipment tag, instrument tag) are plant-wide: the same tag on two sheets is ONE node.
    Untagged items (valves without tag, tees, reducers, off-page arrows) belong to their sheet.

Manual edits (web interface, tab "Edit"):
    Stored as (:ManualEdit) records and re-applied after every load, so they survive reloading a sheet.
    'add' creates a relationship with source='manual'; 'remove' hides a relationship that came from a sheet.
"""
import argparse
import codecs
import csv
import glob
import json
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime

from defusedxml import ElementTree as ET
from pydexpi.loaders import GraphAbstractor, GraphLoader, ProteusSerializer

import dexpi_links
import proteus_topology

DEFAULT_PLANT = "Demo Plant"
UNASSIGNED = "Unassigned"

# Fields that carry a plant-wide tag, in priority order
TAG_FIELDS = ["tagName", "processSignalGeneratingFunctionNumber", "actuatingFunctionNumber",
              "processInstrumentationFunctionNumber"]
KEEP_PROPS = ["proteusId", "pipingComponentName", "lineNumber", "fluidCode",
              "nominalDiameterRepresentation", "pipingClassCode"]
OFFPAGE_TYPES = ("FlowInPipeOffPageConnector", "FlowOutPipeOffPageConnector")
# kept on a Sheet node when the sheet is reloaded: the reviewer's decisions on its PDF drawing check (crosscheck/store.py)
KEEP_SHEET_PROPS = ("pdfApproved", "pdfRejected")
# on every exported relationship: where it came from (manual, drawing), its review status and its PDF check
EXPORT_REL_PROPS = ("source", "status", "pdfCheck")
PIPE_LINE_TYPES = ("Pipe", "PipingNetworkSegment", "PipingNetworkSystem")


# --------------------------------------------------------------------------- parsing

def category_of(labels: str) -> str:
    if "Equipment" in labels:
        return "Equipment"
    if "Instrumentation" in labels or "Function" in labels or "Signal" in labels:
        return "Instrumentation"
    return "Piping"


def valve_role(dtype: str, actuated: bool):
    """'safety' (safety / relief valves), 'actuated' (control and on/off valves with an actuator), 'manual'
    (any other valve), or None if this is not a valve."""
    t = str(dtype).lower()
    if "valve" not in t:
        return None
    if any(w in t for w in ("safety", "relief", "breather")):
        return "safety"
    if actuated or any(w in t for w in ("control", "operated", "actuated", "motor", "solenoid")):
        return "actuated"
    return "manual"


def clean_category(text) -> str:
    """A category typed by a person: any short name with a letter in it (Equipment, Electrical, Safety device)."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not re.search(r"[A-Za-z]", t) or len(t) > 40:
        raise ValueError("Category needs a name of up to 40 characters, with at least one letter.")
    return t


def clean_label(text: str) -> str:
    text = re.sub(r"\W", "", str(text)) or "Unknown"
    return text if text[0].isalpha() else "T" + text


def rel_type(edge_type: str) -> str:
    out = ""
    for i, ch in enumerate(edge_type):
        if ch.isupper() and i > 0 and not edge_type[i - 1].isupper():
            out += "_"
        out += ch.upper() if ch.isalnum() else "_"
    return out or "CONNECTED_TO"


def read_xml_text(xml_path: str) -> str:
    """File contents as text, whatever the file is called (COMOS exports have no .xml extension)
    and however it is encoded (COMOS writes UTF-8 with a byte-order mark, which pyDEXPI's own
    file reader cannot handle on Windows)."""
    with open(xml_path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    declared = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)", raw[:200])
    return raw.decode(declared.group(1).decode() if declared else "utf-8")


def looks_like_dexpi(head: bytes) -> bool:
    """True if the start of a file is a Proteus / DEXPI document, whatever its file name."""
    return b"<PlantModel" in head[:4000]


def sheet_name_from_file(filename: str) -> str:
    """Default sheet name: the file name without .xml, and without the system UID that COMOS
    appends to exported objects ('=P002.A13.FB.001 (A4S1A2Y1M7)' -> '=P002.A13.FB.001')."""
    name = os.path.basename(filename)
    if name.lower().endswith(".xml"):
        name = name[:-4]
    return re.sub(r"\s*\([A-Z0-9]{6,}\)$", "", name) or name


def comos_properties(root) -> dict:
    """COMOS exports keep the real tags outside the DEXPI attributes: a TagName attribute plus a
    'ComosProperties' set (Label, FullLabel, Description). Returns {DEXPI ID: {name: value}}."""
    out = {}
    for el in root.iter():
        if not el.get("ID"):
            continue
        props = {ga.get("Name"): ga.get("Value") for gas in el.findall("GenericAttributes")
                 if gas.get("Set") == "ComosProperties" for ga in gas.findall("GenericAttribute")}
        if el.get("TagName"):
            props.setdefault("Label", el.get("TagName"))
        if props.get("FullLabel"):
            out[el.get("ID")] = props
    return out


def comos_name(c: dict) -> str:
    """Short readable tag: SE001, or Y001.YS for an instrument function whose own label is only its letters."""
    label = c.get("Label") or c["FullLabel"].lstrip("=")
    parts = c["FullLabel"].lstrip("=").split(".")
    if label.isalpha() and len(parts) > 1 and parts[-1] == label:
        return f"{parts[-2]}.{label}"
    return label


def parse_sheet(xml_path: str, sheet: str, details=None):
    """Read one DEXPI file and return its assets, connections and load issues.
    details: optional dict, filled with 'open_ends' (pipe ends that leave the sheet, for link_sheets) and 'rules'
    (how many links each dexpi_links rule added)."""
    text = read_xml_text(xml_path)
    model = ProteusSerializer().load_from_string(text)
    graph = GraphAbstractor.build_conceptual_graph(GraphLoader().parse_dexpi_to_graph(model))
    root = ET.fromstring(text)
    comos = comos_properties(root)

    issues = []
    assets = {}       # uid -> asset row
    uid_of = {}       # pyDEXPI node id -> uid

    def add_asset(dtype, category, tag, local_id, data=None, has_id=True):
        data = data or {}
        c = comos.get(local_id)
        if not has_id:
            issues.append(f"no DEXPI ID for {dtype}; identity will change on reload")
        if c:
            # COMOS full label (e.g. =P002.A13.A11SE001) is unique plant-wide, unlike the short tag
            uid = f"TAG:{c['FullLabel']}"
            tag = tag or comos_name(c)
            if uid in assets:
                issues.append(f"tag {c['FullLabel']} appears twice on this sheet; merged into one asset")
        elif tag:
            uid = f"TAG:{tag}"
            if uid in assets:
                issues.append(f"tag {tag} appears twice on this sheet; merged into one asset")
        else:
            uid = f"{sheet}::{local_id}"
            if category == "Equipment":
                issues.append(f"equipment without tag: {local_id}")
        props = {k: str(data[k]) for k in KEEP_PROPS if data.get(k) not in (None, "")}
        props.update({"uid": uid, "name": tag or str(local_id), "type": dtype,
                      "category": category, "tagged": bool(tag)})
        if c:
            props["fullTag"] = c["FullLabel"]
            if c.get("Description"):
                props["description"] = c["Description"]
        if not tag:
            props["sheet"] = sheet
        if dtype in OFFPAGE_TYPES and not data.get("lineNumber"):
            issues.append(f"off-page arrow {local_id} has no line number; add it to links.csv")
        if uid in assets:  # same tag again: keep the first, fill in what it lacks
            for k, v in props.items():
                assets[uid].setdefault(k, v)
        else:
            assets[uid] = props
        return uid

    for node_id, d in graph.nodes(data=True):
        if not d:
            continue  # empty placeholder pyDEXPI makes for an open pipe end or unresolved reference
        tag = next((str(d[f]) for f in TAG_FIELDS if d.get(f)), None)
        uid_of[node_id] = add_asset(str(d.get("label", "Unknown")), category_of(str(d.get("labels", ""))), tag,
                                    d.get("proteusId") or node_id, d, has_id=bool(d.get("proteusId")))

    edges = []
    for src, dst, d in graph.edges(data=True):
        if src not in uid_of or dst not in uid_of:
            continue
        props = {k: str(d[k]) for k in KEEP_PROPS if d.get(k) not in (None, "")}
        props["dexpiType"] = str(d.get("label", "connected"))
        edges.append({"src": uid_of[src], "dst": uid_of[dst], "rel": rel_type(props["dexpiType"]),
                      "props": props})

    # items and links pyDEXPI does not carry over (see proteus_topology.py)
    uid_by_pid = {graph.nodes[n].get("proteusId"): u for n, u in uid_of.items() if graph.nodes[n].get("proteusId")}
    proteus_topology.complete(
        root, sheet, uid_by_pid, edges, issues, rel_type=rel_type,
        add_asset=lambda el, category: add_asset(el.get("ComponentClass") or el.tag, category,
                                                 None, el.get("ID")))
    # pipe-line nodes left with no connection carry nothing the pipe links don't already show
    linked = {e["src"] for e in edges} | {e["dst"] for e in edges}
    for uid in [u for u, a in assets.items() if u not in linked and a["type"] in PIPE_LINE_TYPES]:
        del assets[uid]

    # valves: control / on-off valves with an actuator and safety valves are plant equipment (maintained, tagged
    # assets); manual valves stay piping. valveRole keeps the distinction for queries.
    operated = {e["dst"] for e in edges if e["rel"] == "OPERATED_VALVE_REFERENCE"}
    for uid, a in assets.items():
        role = valve_role(a["type"], uid in operated)
        if role:
            a["valveRole"] = role
            if role != "manual" and a["category"] == "Piping":
                a["category"] = "Equipment"

    # two COMOS items with the same short tag (e.g. two tanks VE001 in areas A10 and A12): add where each one sits,
    # taken from the full tag minus the part every tag on this sheet shares (=P002.A13.A10VE001 -> VE001 (A10),
    # =P002.A13.A11.N003.EU.A10-M001 -> M001 (A11.N003.EU.A10))
    seen = defaultdict(list)
    for a in assets.values():
        if a.get("fullTag"):
            seen[a["name"]].append(a)
    split = [a["fullTag"].lstrip("=").split(".") for a in assets.values() if a.get("fullTag")]
    common = 0
    while split and all(len(p) > common + 1 and p[common] == split[0][common] for p in split):
        common += 1
    for short, same in seen.items():
        if len(same) > 1:
            for a in same:
                rest = ".".join(a["fullTag"].lstrip("=").split(".")[common:])
                where = rest[:-len(short)].rstrip(".-+ ") if rest.endswith(short) else ""
                a["name"] = f"{short} ({where})" if where else rest

    # links in the XML that neither pyDEXPI nor the drawing pass reads (see dexpi_links.py)
    open_ends, rules = dexpi_links.enrich(root, sheet, assets, uid_by_pid, edges, issues, rel_type)
    proteus_topology.report_unconnected(root, uid_by_pid, edges, issues, dexpi_links.SKIP_CLASSES)
    if details is not None:
        details.update(open_ends=open_ends, rules=dict(rules))

    return list(assets.values()), edges, issues


# --------------------------------------------------------------------------- database

class Db:
    """Thin wrapper: Neo4j in normal use; an embedded FalkorDB file only for offline testing."""

    def __init__(self, args):
        self.kind = "falkordb" if args.test_db else "neo4j"
        if self.kind == "neo4j":
            from neo4j import GraphDatabase
            uri = args.uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
            user = args.user or os.environ.get("NEO4J_USER", "neo4j")
            password = args.password or os.environ.get("NEO4J_PASSWORD")
            if not password:
                sys.exit("Set NEO4J_PASSWORD (see the top of this file) or pass --password.")
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
            self.database = args.database or os.environ.get("NEO4J_DATABASE")
        else:
            from redislite.falkordb_client import FalkorDB
            self.graph = FalkorDB(args.test_db).select_graph("plant")

    def run(self, query, **params):
        if self.kind == "neo4j":
            records, _, _ = self.driver.execute_query(query, params, database_=self.database)
            return [r.data() for r in records]
        res = self.graph.query(query, params)
        header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
        return [dict(zip(header, row)) for row in res.result_set]

    def setup(self):
        if self.kind == "neo4j":
            self.run("CREATE CONSTRAINT asset_uid IF NOT EXISTS FOR (a:Asset) REQUIRE a.uid IS UNIQUE")
            self.run("CREATE CONSTRAINT sheet_name IF NOT EXISTS FOR (s:Sheet) REQUIRE s.name IS UNIQUE")
            self.run("CREATE INDEX asset_name IF NOT EXISTS FOR (a:Asset) ON (a.name)")
            self.run("CREATE INDEX manual_edit_id IF NOT EXISTS FOR (e:ManualEdit) ON (e.id)")
            self.run("CREATE INDEX open_end_line IF NOT EXISTS FOR (o:OpenEnd) ON (o.line)")
        else:
            for q in ("CREATE INDEX FOR (a:Asset) ON (a.uid)", "CREATE INDEX FOR (s:Sheet) ON (s.name)"):
                try:
                    self.run(q)
                except Exception:
                    pass

    def close(self):
        if self.kind == "neo4j":
            self.driver.close()


def remove_sheet(db, sheet):
    db.run("MATCH ()-[r]->() WHERE r.sheet = $sheet DELETE r", sheet=sheet)
    db.run("MATCH ()-[p:PIPE {crossSheet: true}]->() WHERE p.fromSheet = $sheet OR p.toSheet = $sheet DELETE p",
           sheet=sheet)
    db.run("MATCH (o:OpenEnd {sheet: $sheet}) DELETE o", sheet=sheet)
    db.run("MATCH (s:Sheet {name: $sheet})-[d:DEPICTS]->() DELETE d", sheet=sheet)
    db.run("MATCH (a:Asset) WHERE NOT ()-[:DEPICTS]->(a) DETACH DELETE a")
    db.run("MATCH (s:Sheet {name: $sheet}) DETACH DELETE s", sheet=sheet)
    db.run("MATCH (y:System) WHERE NOT (y)-[:HAS_SHEET]->() DETACH DELETE y")
    db.run("MATCH (x:Area) WHERE NOT (x)-[:HAS_SYSTEM]->() DETACH DELETE x")
    db.run("MATCH (p:Plant) WHERE NOT (p)-[:HAS_AREA]->() DETACH DELETE p")


def load_sheet(db, xml_path, sheet, plant, area, system, revision):
    """Load one DEXPI file as a sheet."""
    details = {}
    assets, edges, issues = parse_sheet(xml_path, sheet, details)
    return write_sheet(db, sheet, assets, edges, issues, plant, area, system, revision,
                       os.path.basename(xml_path), source="dexpi", open_ends=details["open_ends"])


def write_sheet(db, sheet, assets, edges, issues, plant, area, system, revision, file, source="dexpi", open_ends=()):
    """Write one sheet's assets and connections, replacing that sheet if it is already loaded.
    Used by the DEXPI route and by the reviewed results of the PDF route (source='pdf').
    open_ends: pipe ends that leave the sheet [{line, dir: 'out'|'in', asset}], kept as (:OpenEnd) records so
    link_sheets can join them to the other end of the same line on another sheet."""
    issues = list(issues)
    kept = db.run(f"MATCH (s:Sheet {{name: $sheet}}) RETURN {', '.join(f's.{p} AS {p}' for p in KEEP_SHEET_PROPS)}",
                  sheet=sheet)
    remove_sheet(db, sheet)

    # Tagged assets that already exist (from other sheets): keep their type, flag conflicts
    tagged = [a["uid"] for a in assets if a["tagged"]]
    found = db.run("UNWIND $uids AS u MATCH (a:Asset {uid: u}) RETURN a.uid AS uid, a.type AS type, "
                   "a.category AS category", uids=tagged)
    existing = {r["uid"]: r["type"] for r in found}
    for a in assets:
        old = existing.get(a["uid"])
        if old and old != a["type"]:
            issues.append(f"tag {a['name']} is {a['type']} here but {old} on another sheet; kept {old}")
            a["type"] = old
    # a shared asset whose category changed (e.g. a control valve now counted as equipment) loses the old label
    new_cat = {a["uid"]: a["category"] for a in assets}
    for r in found:
        if r["category"] and r["category"] != new_cat.get(r["uid"]):
            db.run(f"MATCH (a:Asset {{uid: $u}}) REMOVE a:{clean_label(r['category'])}", u=r["uid"])

    area = area or UNASSIGNED
    system = system or UNASSIGNED
    db.run("""
        MERGE (p:Plant {name: $plant})
        MERGE (ar:Area {uid: $plant + '/' + $area}) SET ar.name = $area
        MERGE (p)-[:HAS_AREA]->(ar)
        MERGE (sy:System {uid: $plant + '/' + $area + '/' + $system}) SET sy.name = $system
        MERGE (ar)-[:HAS_SYSTEM]->(sy)
        MERGE (s:Sheet {name: $sheet})
        SET s.revision = $revision, s.file = $file, s.issues = $issues, s.source = $source
        MERGE (sy)-[:HAS_SHEET]->(s)
        """, plant=plant, area=area, system=system, sheet=sheet, revision=revision or "",
           file=file, issues=issues, source=source)
    if kept:
        db.run("MATCH (s:Sheet {name: $sheet}) SET s += $props", sheet=sheet,
               props={k: v for k, v in kept[0].items() if v is not None})

    groups = defaultdict(list)
    for a in assets:
        groups[(a["category"], a["type"])].append(a)
    for (category, dtype), rows in groups.items():
        db.run(f"""
            UNWIND $rows AS row
            MERGE (a:Asset {{uid: row.uid}})
            SET a += row
            SET a:{clean_label(category)}:{clean_label(dtype)}
            WITH a
            MATCH (s:Sheet {{name: $sheet}})
            MERGE (s)-[:DEPICTS]->(a)
            """, rows=rows, sheet=sheet)

    by_rel = defaultdict(list)
    for e in edges:
        by_rel[e["rel"]].append({"src": e["src"], "dst": e["dst"], "props": e["props"]})
    for rel, rows in by_rel.items():
        db.run(f"""
            UNWIND $rows AS row
            MATCH (a:Asset {{uid: row.src}}), (b:Asset {{uid: row.dst}})
            CREATE (a)-[r:{rel}]->(b)
            SET r += row.props, r.sheet = $sheet
            """, rows=rows, sheet=sheet)
    if open_ends:
        db.run("""UNWIND $ends AS e
                  CREATE (:OpenEnd {line: e.line, dir: e.dir, asset: e.asset, sheet: $sheet})""",
               ends=list(open_ends), sheet=sheet)

    print(f"  sheet {sheet}: {len(assets)} assets, {len(edges)} connections, {len(issues)} issues")
    return {"assets": len(assets), "connections": len(edges), "issues": issues}


def link_sheets(db, links_csv=None):
    """Rebuild the links between sheets: CONTINUES_TO between off-page arrows, and PIPE {crossSheet: true} between
    the open ends of the same pipe line on two sheets (the line leaves one sheet and goes on on the other)."""
    db.run("MATCH ()-[c:CONTINUES_TO]->() DELETE c")
    db.run("""
        MATCH (o:FlowOutPipeOffPageConnector), (i:FlowInPipeOffPageConnector)
        WHERE o.lineNumber IS NOT NULL AND o.lineNumber = i.lineNumber AND o.sheet <> i.sheet
        MERGE (o)-[c:CONTINUES_TO]->(i)
        SET c.matchedBy = 'lineNumber'
        """)
    db.run("MATCH ()-[p:PIPE {crossSheet: true}]->() DELETE p")
    db.run("""
        MATCH (o:OpenEnd {dir: 'out'}), (i:OpenEnd {dir: 'in'})
        WHERE o.line = i.line AND o.sheet <> i.sheet
        MATCH (a:Asset {uid: o.asset}), (b:Asset {uid: i.asset})
        WHERE a <> b
        MERGE (a)-[p:PIPE {crossSheet: true, line: o.line, fromSheet: o.sheet, toSheet: i.sheet}]->(b)
        SET p.dexpiType = 'Pipe', p.matchedBy = 'open pipe end'
        """)
    if links_csv and os.path.exists(links_csv):
        rows = []
        with open(links_csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows.append({"src": f"{r['from_sheet']}::{r['from_id']}", "dst": f"{r['to_sheet']}::{r['to_id']}"})
        found = db.run("""
            UNWIND $rows AS row
            MATCH (a:Asset {uid: row.src}), (b:Asset {uid: row.dst})
            MERGE (a)-[c:CONTINUES_TO]->(b)
            SET c.matchedBy = 'links.csv'
            RETURN count(c) AS n
            """, rows=rows)
        n = found[0]["n"] if found else 0
        if n < len(rows):
            print(f"  warning: {len(rows) - n} row(s) in {links_csv} did not match a loaded sheet/ID")
    # after the automatic links, so manual edits can also add or remove CONTINUES_TO
    pending = apply_manual_edits(db)
    if pending:
        print(f"  manual edits waiting for assets that are not loaded: {pending}")
    n = db.run(CROSS_SHEET_COUNT)[0]["n"]
    print(f"  cross-sheet links: {n}")
    return n


CROSS_SHEET_COUNT = """MATCH ()-[c]->() WHERE type(c) = 'CONTINUES_TO' OR (type(c) = 'PIPE' AND c.crossSheet = true)
                       RETURN count(c) AS n"""


# --------------------------------------------------------------------------- manual edits

def clean_rel_type(text):
    """Relationship type typed by a person -> a safe Neo4j type: 'pipe' -> PIPE, 'feeds to' -> FEEDS_TO."""
    t = re.sub(r"[^A-Za-z0-9_]+", "_", str(text).strip()).upper().strip("_")
    if not t or not t[0].isalpha():
        raise ValueError(f"Invalid relationship type: {text!r} (use letters, digits and _)")
    return t


ASSET_FIELDS = ("name", "category", "type", "description")   # what set_asset_props may change


def _set_asset(db, uid, props):
    """Write asset properties; a new category or type also swaps the matching Neo4j labels. False if not loaded."""
    cur = db.run("MATCH (a:Asset {uid: $u}) RETURN a.category AS category, a.type AS type", u=uid)
    if not cur:
        return False
    new = {**cur[0], **{k: v for k, v in props.items() if k in ("category", "type")}}
    db.run(f"""MATCH (a:Asset {{uid: $u}})
               REMOVE a:{clean_label(cur[0]['category'])}:{clean_label(cur[0]['type'])}
               SET a += $props
               SET a:{clean_label(new['category'])}:{clean_label(new['type'])}""", u=uid, props=props)
    return True


def set_asset_props(db, uid, props, note=""):
    """Change an asset's name, category, type or description by hand. Saved as a ManualEdit (action 'set'),
    so it is re-applied after each load like relationship edits. Returns the edit id, or None if nothing changed."""
    cur = db.run("MATCH (a:Asset {uid: $u}) RETURN a.name AS name, a.category AS category, a.type AS type, "
                 "a.description AS description", u=uid)
    if not cur:
        raise ValueError("This asset is not loaded.")
    props = {k: str(v).strip() for k, v in props.items() if k in ASSET_FIELDS and v is not None}
    if not props.get("name", "x") or not props.get("type", "x"):
        raise ValueError("Name and type can't be empty.")
    if "category" in props:
        props["category"] = clean_category(props["category"])
    props = {k: v for k, v in props.items() if v != (cur[0][k] or "")}
    if not props:
        return None
    return add_manual_edit(db, "set", uid, uid, "SET_PROPERTIES", props, note,
                           original={k: cur[0][k] or "" for k in props})


def delete_plant(db, keep_edits=False):
    """Delete every sheet, asset, system, area and plant. Manual edits (ManualEdit records: hand-added or hidden
    links, renames) go too, unless keep_edits, in which case they are re-applied when sheets are loaded again."""
    db.run("MATCH (n) WHERE n:Asset OR n:Sheet OR n:System OR n:Area OR n:Plant OR n:OpenEnd DETACH DELETE n")
    if not keep_edits:
        db.run("MATCH (e:ManualEdit) DELETE e")


def classify_valves(db):
    """Give valves already in the database the valve rule that loading now applies (see valve_role): actuated and
    safety valves become Equipment, every valve gets valveRole. Only touches valves without a valveRole, so it does
    nothing once a database is up to date. Manual edits are re-applied afterwards, so a category set by hand wins.
    Returns how many valves were updated."""
    rows = db.run("""MATCH (v:Asset) WHERE v.valveRole IS NULL AND toLower(v.type) CONTAINS 'valve'
                     OPTIONAL MATCH (:Asset)-[r:OPERATED_VALVE_REFERENCE]->(v)
                     RETURN v.uid AS uid, v.type AS type, v.category AS category, count(r) > 0 AS actuated""")
    if not rows:
        return 0
    roles = [{"uid": r["uid"], "role": valve_role(r["type"], r["actuated"])} for r in rows]
    db.run("UNWIND $rows AS row MATCH (a:Asset {uid: row.uid}) SET a.valveRole = row.role", rows=roles)
    to_equipment = [r["uid"] for r, x in zip(rows, roles) if x["role"] != "manual" and r["category"] == "Piping"]
    if to_equipment:
        db.run("""UNWIND $uids AS u MATCH (a:Asset {uid: u})
                  REMOVE a:Piping SET a:Equipment, a.category = 'Equipment'""", uids=to_equipment)
    apply_manual_edits(db)
    return len(rows)


def apply_manual_edits(db):
    """Re-apply every manual edit: removes first, then adds, then asset changes (in the order they were made).
    Returns how many edits wait for a missing asset."""
    edits = db.run("""MATCH (e:ManualEdit) RETURN e.id AS id, e.action AS action, e.src AS src, e.dst AS dst,
                      e.type AS type, e.props AS props ORDER BY e.created""")
    for e in edits:
        if e["action"] == "remove":
            db.run(f"""MATCH (:Asset {{uid: $src}})-[r:{clean_rel_type(e['type'])}]->(:Asset {{uid: $dst}})
                       WHERE coalesce(r.source, '') <> 'manual' DELETE r""", src=e["src"], dst=e["dst"])
    pending = 0
    for e in edits:
        if e["action"] == "add":
            n = db.run(f"""MATCH (a:Asset {{uid: $src}}), (b:Asset {{uid: $dst}})
                           MERGE (a)-[r:{clean_rel_type(e['type'])} {{editId: $id}}]->(b)
                           SET r += $props, r.source = 'manual'
                           RETURN count(r) AS n""", src=e["src"], dst=e["dst"], id=e["id"],
                       props=json.loads(e["props"] or "{}"))
            pending += 0 if n and n[0]["n"] else 1
    for e in edits:
        if e["action"] == "set":
            pending += 0 if _set_asset(db, e["src"], json.loads(e["props"] or "{}")) else 1
    return pending


def add_manual_edit(db, action, src, dst, rel, props=None, note="", original=None, change_id=None, apply=True):
    """Store one manual edit ('add' or 'remove' a relationship, 'set' asset properties) and apply it.
    Edits made together share a change_id."""
    edit_id = str(uuid.uuid4())
    db.run("""CREATE (:ManualEdit {id: $id, changeId: $change, action: $action, src: $src, dst: $dst, type: $type,
              props: $props, original: $original, note: $note, created: $now})""",
           id=edit_id, change=change_id or edit_id, action=action, src=src, dst=dst, type=clean_rel_type(rel),
           props=json.dumps(props or {}), original=json.dumps(original) if original else None, note=note or "",
           now=datetime.now().isoformat(timespec="seconds"))
    if apply:
        apply_manual_edits(db)
    return edit_id


def relationships_of(db, uid):
    """Every relationship of one asset, both directions, with where it came from."""
    return db.run("""MATCH (a:Asset {uid: $u})-[r]-(b:Asset)
                     RETURN DISTINCT type(r) AS type, startNode(r).uid AS src, startNode(r).name AS src_name,
                            endNode(r).uid AS dst, endNode(r).name AS dst_name, properties(r) AS props
                     ORDER BY type, src_name, dst_name""", u=uid)


def change_relationship(db, rel, new_type, reverse=False, line_number=None, note=""):
    """rel: a row from relationships_of. A manual relationship is updated in place; one that came from a sheet
    is hidden and replaced by a manual copy with the changes."""
    props = {k: v for k, v in rel["props"].items() if k not in ("source", "editId", "sheet")}
    if line_number:
        props["lineNumber"] = line_number
    else:
        props.pop("lineNumber", None)
    src, dst = (rel["dst"], rel["src"]) if reverse else (rel["src"], rel["dst"])
    if rel["props"].get("source") == "manual":
        edit_id = rel["props"]["editId"]
        db.run("MATCH ()-[r {editId: $id}]->() DELETE r", id=edit_id)
        db.run("""MATCH (e:ManualEdit {id: $id})
                  SET e.src = $src, e.dst = $dst, e.type = $type, e.props = $props, e.note = $note""",
               id=edit_id, src=src, dst=dst, type=clean_rel_type(new_type), props=json.dumps(props), note=note or "")
        apply_manual_edits(db)
    else:
        change = add_manual_edit(db, "remove", rel["src"], rel["dst"], rel["type"], original=rel["props"],
                                 note=note, apply=False)
        add_manual_edit(db, "add", src, dst, new_type, props, note, change_id=change)


def delete_relationship(db, rel, note=""):
    """A manual relationship is deleted; one that came from a sheet is hidden (and stays hidden after reloads)."""
    if rel["props"].get("source") == "manual":
        undo_manual_edit(db, rel["props"]["editId"], whole_change=False)
    else:
        add_manual_edit(db, "remove", rel["src"], rel["dst"], rel["type"], original=rel["props"], note=note)


def undo_manual_edit(db, edit_id, whole_change=True):
    """Delete a manual edit (and, with whole_change, the edits made together with it) and undo its effect:
    an added relationship disappears, a hidden relationship from a sheet comes back."""
    edits = db.run("""MATCH (e:ManualEdit {id: $id}) MATCH (g:ManualEdit)
                      WHERE g.id = e.id OR ($whole AND g.changeId = e.changeId)
                      RETURN g.id AS id, g.action AS action, g.src AS src, g.dst AS dst, g.type AS type,
                             g.original AS original""", id=edit_id, whole=whole_change)
    reapply = False
    for e in edits:
        if e["action"] == "set":
            # back to the values before this edit; later asset edits are re-applied below
            _set_asset(db, e["src"], json.loads(e["original"] or "{}"))
            reapply = True
        elif e["action"] == "add":
            db.run("MATCH ()-[r {editId: $id}]->() DELETE r", id=e["id"])
        elif e["original"]:
            db.run(f"""MATCH (a:Asset {{uid: $src}}), (b:Asset {{uid: $dst}})
                       WHERE NOT EXISTS {{ MATCH (a)-[x:{clean_rel_type(e['type'])}]->(b)
                                          WHERE coalesce(x.source, '') <> 'manual' }}
                       CREATE (a)-[r:{clean_rel_type(e['type'])}]->(b) SET r = $orig""",
                   src=e["src"], dst=e["dst"], orig=json.loads(e["original"]))
        db.run("MATCH (e:ManualEdit {id: $id}) DELETE e", id=e["id"])
    if reapply:
        apply_manual_edits(db)


def manual_edits(db):
    """All manual edits, oldest first, with readable names and whether both assets are loaded."""
    return db.run("""MATCH (e:ManualEdit)
                     OPTIONAL MATCH (a:Asset {uid: e.src}) OPTIONAL MATCH (b:Asset {uid: e.dst})
                     RETURN e.id AS id, e.changeId AS change, e.action AS action, e.type AS type,
                            coalesce(a.name, e.src) AS src_name, coalesce(b.name, e.dst) AS dst_name,
                            e.note AS note, e.created AS created, a IS NOT NULL AND b IS NOT NULL AS applied,
                            e.props AS props, e.original AS original
                     ORDER BY e.created""")


# --------------------------------------------------------------------------- export

def export_adjacency(db, uids=None, view=None, candidates=False):
    """Adjacency list of all assets, or only `uids`, as a JSON-ready dict for checking the graph in other tools.

    nodes:     uid -> the asset's properties plus its labels and the sheets that show it
    adjacency: uid -> outgoing relationships [{to, toName, type, properties}]. Every exported asset is listed, with []
               when nothing leaves it. Relationships are directed (PIPE follows the flow) and only those between
               exported assets are included; hand edits are in, with properties.source = 'manual'. Every relationship
               carries source, status and pdfCheck (null when not set).
    candidates: also export the PDF check's unreviewed links (PIPE {source: 'drawing', status: 'candidate'}).
    """
    params = {"u": list(uids or []), "cand": candidates}
    where_a = "WHERE a.uid IN $u" if uids is not None else ""
    where_ab = "AND a.uid IN $u AND b.uid IN $u" if uids is not None else ""
    nodes = db.run(f"""MATCH (a:Asset) {where_a}
                       OPTIONAL MATCH (s:Sheet)-[:DEPICTS]->(a)
                       RETURN a.uid AS uid, properties(a) AS props, labels(a) AS labels, collect(s.name) AS sheets
                       ORDER BY uid""", **params)
    edges = db.run(f"""MATCH (a:Asset)-[r]->(b:Asset)
                       WHERE ($cand OR coalesce(r.status, '') <> 'candidate') {where_ab}
                       RETURN a.uid AS src, b.uid AS dst, b.name AS dst_name, type(r) AS type, properties(r) AS props
                       ORDER BY src, type, dst""", **params)
    adjacency = {n["uid"]: [] for n in nodes}
    for e in edges:
        adjacency[e["src"]].append({"to": e["dst"], "toName": e["dst_name"], "type": e["type"],
                                    "properties": {**dict.fromkeys(EXPORT_REL_PROPS), **e["props"]}})
    return {
        "format": "pid-kg-adjacency",
        "version": 1,
        "exported": datetime.now().isoformat(timespec="seconds"),
        "plant": [r["name"] for r in db.run("MATCH (p:Plant) RETURN p.name AS name ORDER BY name")],
        "view": view or {"mode": "Whole plant"},
        "directed": True,
        "counts": {"nodes": len(adjacency), "relationships": len(edges)},
        "nodes": {n["uid"]: {**n["props"], "labels": sorted(set(n["labels"]) - {"Asset"}),
                             "sheets": sorted(n["sheets"])} for n in nodes},
        "adjacency": adjacency,
    }


# --------------------------------------------------------------------------- reports

def summary(db):
    q = lambda query: db.run(query)[0]["n"]
    print(f"Sheets: {q('MATCH (s:Sheet) RETURN count(s) AS n')}  "
          f"Assets: {q('MATCH (a:Asset) RETURN count(a) AS n')}  "
          f"Equipment: {q('MATCH (a:Equipment) RETURN count(a) AS n')}  "
          f"Cross-sheet links: {q(CROSS_SHEET_COUNT)}")
    for r in db.run("""
            MATCH (ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)
            OPTIONAL MATCH (s)-[:DEPICTS]->(a:Asset)
            RETURN ar.name AS area, sy.name AS system, s.name AS sheet, s.revision AS rev, count(a) AS assets
            ORDER BY area, system, sheet"""):
        print(f"  {r['area']} / {r['system']} / sheet {r['sheet']} rev {r['rev'] or '-'}: {r['assets']} assets")


def report(db, out_dir):
    rows = []
    for r in db.run("MATCH (s:Sheet) RETURN s.name AS sheet, s.issues AS issues ORDER BY sheet"):
        for issue in r["issues"] or []:
            rows.append({"sheet": r["sheet"], "check": "load", "item": "", "detail": issue})
    for r in db.run("""
            MATCH (a:Asset) WHERE a.type IN $types AND NOT (a)-[:CONTINUES_TO]-()
            RETURN a.sheet AS sheet, a.name AS item, a.lineNumber AS line ORDER BY sheet, item""",
                    types=list(OFFPAGE_TYPES)):
        rows.append({"sheet": r["sheet"], "check": "unresolved off-page arrow", "item": r["item"],
                     "detail": f"line {r['line'] or '?'} not found on any other loaded sheet"})
    for r in db.run("""
            MATCH (s:Sheet)-[:DEPICTS]->(a:Asset {tagged: true})
            WITH a, collect(s.name) AS sheets WHERE size(sheets) > 1
            RETURN a.name AS item, sheets ORDER BY item"""):
        rows.append({"sheet": ", ".join(sorted(r["sheets"])), "check": "tag on several sheets (merged)",
                     "item": r["item"], "detail": "one asset node shared by these sheets"})
    for e in manual_edits(db):
        if not e["applied"]:
            rows.append({"sheet": "", "check": "manual edit not applied", "detail": "an asset is not loaded",
                         "item": f"{e['action']} {e['src_name']} -[{e['type']}]-> {e['dst_name']}"})
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "validation_report.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sheet", "check", "item", "detail"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} findings -> {path}")
    return rows


# --------------------------------------------------------------------------- CLI

def read_manifest(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {os.path.basename(r["file"]): r for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser(description="Load DEXPI P&ID sheets into one plant knowledge graph.")
    ap.add_argument("command", choices=["load", "remove", "summary", "report", "reset"])
    ap.add_argument("path", nargs="?", help="a DEXPI .xml file or a folder of them")
    ap.add_argument("--sheet", help="sheet name/number (default: file name)")
    ap.add_argument("--area")
    ap.add_argument("--system")
    ap.add_argument("--revision")
    ap.add_argument("--plant", default=DEFAULT_PLANT)
    ap.add_argument("--manifest", help="CSV: file,sheet,revision,area,system")
    ap.add_argument("--links", default="links.csv", help="optional manual off-page links CSV")
    ap.add_argument("--out", default="output")
    ap.add_argument("--uri")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--database")
    ap.add_argument("--test-db", help="offline testing only: path to an embedded FalkorDB file")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--keep-edits", action="store_true", help="reset: keep manual edits to re-apply after reloading")
    args = ap.parse_args()

    db = Db(args)
    db.setup()
    classify_valves(db)  # sheets loaded before the valve rule existed get it now
    try:
        if args.command == "load":
            if not args.path:
                sys.exit("Give a .xml file or a folder.")
            files = [args.path]
            if os.path.isdir(args.path):  # .xml files, plus COMOS exports, which have no .xml extension
                files = []
                for f in sorted(glob.glob(os.path.join(args.path, "*"))):
                    if os.path.isfile(f) and not f.lower().endswith((".csv", ".pdf", ".png", ".jpg", ".jpeg")):
                        with open(f, "rb") as fh:
                            if looks_like_dexpi(fh.read(4000)):
                                files.append(f)
            manifest = read_manifest(args.manifest) if args.manifest else {}
            if args.sheet and len(files) > 1:
                sys.exit("--sheet only works with a single file; use --manifest for folders.")
            print(f"Loading {len(files)} sheet(s) into plant '{args.plant}'")
            for path in files:
                m = manifest.get(os.path.basename(path), {})
                sheet = args.sheet or m.get("sheet") or sheet_name_from_file(path)
                try:
                    load_sheet(db, path, sheet, args.plant, args.area or m.get("area"),
                               args.system or m.get("system"), args.revision or m.get("revision"))
                except Exception as exc:
                    print(f"  sheet {sheet}: FAILED ({exc.__class__.__name__}: {exc}); other sheets continue")
            link_sheets(db, args.links)
            summary(db)
        elif args.command == "remove":
            if not args.sheet:
                sys.exit("Give --sheet.")
            remove_sheet(db, args.sheet)
            link_sheets(db, args.links)
            summary(db)
        elif args.command == "summary":
            summary(db)
        elif args.command == "report":
            report(db, args.out)
        elif args.command == "reset":
            if not args.yes:
                sys.exit("This deletes the whole plant graph. Re-run with --yes to confirm.")
            delete_plant(db, keep_edits=args.keep_edits)
            print("Plant graph deleted" + (" (manual edits kept)." if args.keep_edits else ", manual edits too."))
    finally:
        db.close()


if __name__ == "__main__":
    main()
