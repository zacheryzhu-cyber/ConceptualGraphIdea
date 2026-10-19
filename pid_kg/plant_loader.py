"""
Multi-sheet P&ID -> plant knowledge graph: reading DEXPI (Proteus XML) sheets, and the command line.

Reading a sheet (parse_sheet) gives its complete graph: every DEXPI object and link, plus what proteus_topology.py and
dexpi_links.py add. That graph is kept as a file per sheet (snapshots/<sheet>.json); Neo4j holds only the conceptual
level of the plant (plant_store.py).

Connection (set once in your terminal, so the password is never written to a file):
    Windows PowerShell:  $env:NEO4J_URI="bolt://localhost:7687"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="..."
    Mac/Linux:           export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=...

Commands:
    python plant_loader.py load  sheet.xml --sheet 101 --area "Area 1" --system "Cooling Water" --revision A [--pdf x.pdf]
    python plant_loader.py load  pids_folder/                      (every DEXPI file in the folder: .xml or COMOS export)
    python plant_loader.py load  pids_folder/ --manifest manifest.csv
    python plant_loader.py remove --sheet 101
    python plant_loader.py rebuild                                 (reset Neo4j, load every sheet again from sources/)
    python plant_loader.py summary                                 (with node / relationship counts by label / type)
    python plant_loader.py report                                  (writes output/validation_report.csv)
    python plant_loader.py reset --yes                             (deletes the plant: Neo4j, snapshots and sources;
                                                                    review decisions are kept unless --all)

manifest.csv columns: file,sheet,revision,area,system[,pdf]
links.csv (optional, for off-page arrows that line numbers can't match; kept as sources/links.csv):
    from_sheet,from_id,to_sheet,to_id     (ids are the DEXPI IDs, e.g. FlowOutPipeOffPageConnector-1)

Asset identity:
    Tagged items (equipment tag, instrument tag) are plant-wide: the same tag on two sheets is ONE node.
    Untagged items (valves without tag, tees, reducers, off-page arrows) belong to their sheet.
"""
import argparse
import codecs
import csv
import glob
import os
import re
import sys
from collections import defaultdict

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
    details: optional dict, filled with 'open_ends' (pipe ends that leave the sheet, for plant_model.plant_graph) and 'rules'
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
            opts = {}
            if uri.startswith(("neo4j+s://", "bolt+s://")):
                # Aura (TLS): use certifi's CA bundle, the Windows store can lack the SSL.com root
                try:
                    import certifi
                    from neo4j import TrustCustomCAs
                    uri = uri.replace("+s://", "://")
                    opts = dict(encrypted=True, trusted_certificates=TrustCustomCAs(certifi.where()))
                except ImportError:
                    pass
            self.driver = GraphDatabase.driver(uri, auth=(user, password), **opts)
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
            self.run("CREATE CONSTRAINT loop_uid IF NOT EXISTS FOR (l:Loop) REQUIRE l.uid IS UNIQUE")
            self.run("CREATE CONSTRAINT sheet_name IF NOT EXISTS FOR (s:Sheet) REQUIRE s.name IS UNIQUE")
            self.run("CREATE INDEX asset_name IF NOT EXISTS FOR (a:Asset) ON (a.name)")
        else:
            for q in ("CREATE INDEX FOR (a:Asset) ON (a.uid)", "CREATE INDEX FOR (s:Sheet) ON (s.name)"):
                try:
                    self.run(q)
                except Exception:
                    pass

    def close(self):
        if self.kind == "neo4j":
            self.driver.close()


# --------------------------------------------------------------------------- CLI

def read_manifest(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {os.path.basename(r["file"]): r for r in csv.DictReader(f)}


def print_summary(store):
    import plant_store
    print("  ".join(f"{k}: {v}" for k, v in store.stats().items()))
    for r in store.db.run("""
            MATCH (ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)
            OPTIONAL MATCH (s)-[:DEPICTS]->(a:Asset)
            RETURN ar.name AS area, sy.name AS system, s.name AS sheet, s.revision AS rev, count(a) AS assets,
                   s.unconnectedEquipment AS loose ORDER BY area, system, sheet"""):
        print(f"  {r['area']} / {r['system']} / sheet {r['sheet']} rev {r['rev'] or '-'}: {r['assets']} assets, "
              f"{r['loose'] or 0} equipment unconnected")
    labels, rels = plant_store.label_counts(store.db)
    print("Nodes by label: " + ", ".join(f"{r['label']} {r['n']}" for r in labels))
    print("Relationships by type: " + ", ".join(f"{r['type']} {r['n']}" for r in rels))


def main():
    import plant_store
    ap = argparse.ArgumentParser(description="Load DEXPI P&ID sheets into one plant knowledge graph.")
    ap.add_argument("command", choices=["load", "remove", "rebuild", "summary", "report", "reset"])
    ap.add_argument("path", nargs="?", help="a DEXPI .xml file or a folder of them")
    ap.add_argument("--sheet", help="sheet name/number (default: file name)")
    ap.add_argument("--area")
    ap.add_argument("--system")
    ap.add_argument("--revision")
    ap.add_argument("--pdf", help="the matching vector PDF of the sheet (single file only), for the drawing check")
    ap.add_argument("--plant", default=DEFAULT_PLANT)
    ap.add_argument("--manifest", help="CSV: file,sheet,revision,area,system[,pdf]")
    ap.add_argument("--links", default="links.csv", help="optional manual off-page links CSV")
    ap.add_argument("--out", default="output")
    ap.add_argument("--uri")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--database")
    ap.add_argument("--test-db", help="offline testing only: path to an embedded FalkorDB file")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--all", action="store_true", help="reset: also delete the review decisions")
    args = ap.parse_args()

    db = Db(args)
    db.setup()
    store = plant_store.PlantStore(db)
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
            if (args.sheet or args.pdf) and len(files) > 1:
                sys.exit("--sheet and --pdf only work with a single file; use --manifest for folders.")
            if args.links and os.path.exists(args.links):
                with open(args.links, "rb") as fh:
                    store.set_links_csv(fh.read())
            print(f"Loading {len(files)} sheet(s) into plant '{args.plant}'")
            for path in files:
                m = manifest.get(os.path.basename(path), {})
                sheet = args.sheet or m.get("sheet") or sheet_name_from_file(path)
                pdf = args.pdf or (os.path.join(os.path.dirname(path), m["pdf"]) if m.get("pdf") else None)
                try:
                    store.load_sheet(path, sheet, args.plant, args.area or m.get("area"),
                                     args.system or m.get("system"), args.revision or m.get("revision"), pdf,
                                     sync=False)
                except Exception as exc:
                    print(f"  sheet {sheet}: FAILED ({exc.__class__.__name__}: {exc}); other sheets continue")
            store.sync()
            print_summary(store)
        elif args.command == "remove":
            if not args.sheet:
                sys.exit("Give --sheet.")
            store.remove_sheet(args.sheet)
            print_summary(store)
        elif args.command == "rebuild":
            results, problems = store.rebuild()
            for p in problems:
                print(f"  problem: {p}")
            print(f"Rebuilt {len(results)} sheet(s).")
            print_summary(store)
        elif args.command == "summary":
            print_summary(store)
        elif args.command == "report":
            rows = store.report(args.out)
            print(f"{len(rows)} findings -> {os.path.join(args.out, 'validation_report.csv')}")
        elif args.command == "reset":
            if not args.yes:
                sys.exit("This deletes the whole plant (Neo4j, snapshots and sources). Re-run with --yes to confirm.")
            store.delete_all(keep_decisions=not args.all)
            print("Plant deleted" + (", review decisions too." if args.all else "; review decisions kept."))
    finally:
        db.close()


if __name__ == "__main__":
    main()
