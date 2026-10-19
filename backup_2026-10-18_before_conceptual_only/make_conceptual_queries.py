"""Write queries_conceptual.cypher (Neo4j Browser queries for the Conceptual view) with the type lists taken from
graph_levels.yaml. Run it again after changing graph_levels.yaml:   python make_conceptual_queries.py
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
import graph_levels as gl  # noqa: E402

SHEET = "=P002.A13.FB.001"
cfg = gl.load_config()
by_group = {}
for t, g in cfg["groups"].items():
    by_group.setdefault(g, []).append(t)
main = sorted(by_group.get("main_equipment", []))
boundary = sorted(by_group.get("boundary", []))
other = sorted(t for g, ts in by_group.items() if g not in ("main_equipment", "boundary") for t in ts)
desc = list(cfg["descriptions"])


def cy(items):
    return "[" + ", ".join("'" + str(x).replace("'", "\\'") + "'" for x in items) + "]"


KEPT = f"""WITH {cy(main)} AS mainTypes,
     {cy(boundary)} AS boundaryTypes,
     {cy(other)} AS collapsedTypes,
     {cy(desc)} AS motorDescriptions
// conceptual nodes: main equipment and off-page connectors. Equipment-category items not listed in
// graph_levels.yaml count as main equipment, except motors (by description), as in the app.
MATCH (a:Asset)
WHERE a.type IN mainTypes OR a.type IN boundaryTypes
   OR (a.category = 'Equipment' AND NOT a.type IN collapsedTypes
       AND none(d IN motorDescriptions WHERE coalesce(a.description, '') STARTS WITH d))
WITH collect(a) AS kept"""


def paths(start, ends):
    return f"""{start}
// follow the pipes (in flow direction) through anything that isn't a conceptual node, to the next one
MATCH p = (a)-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*1..25]->(b:Asset)
WHERE b <> a AND b IN {ends}
  AND none(n IN nodes(p)[1..-1] WHERE n IN kept)
  AND all(r IN relationships(p) WHERE coalesce(r.status, '') <> 'candidate')   // skip unreviewed PDF-check links
WITH a, b, p ORDER BY length(p)
WITH a, b, head(collect(p)) AS p                                             // shortest run between each pair
WITH a, b, p,
     [n IN nodes(p)[1..-1] | n.name] AS via,
     reduce(acc = [], r IN relationships(p) |
            CASE WHEN r.lineNumber IS NULL OR r.lineNumber IN acc THEN acc ELSE acc + r.lineNumber END) AS lines,
     any(r IN relationships(p) WHERE type(r) = 'CONTINUES_TO' OR r.crossSheet) AS crossSheet"""


PATHS_ALL = paths("UNWIND kept AS a", "kept")
PATHS_SHEET = paths(f"MATCH (:Sheet {{name: '{SHEET}'}})-[:DEPICTS]->(x:Asset)   // <- your sheet name\n"
                    "WITH kept, collect(x) AS onSheet\n"
                    "UNWIND [k IN kept WHERE k IN onSheet] AS a",
                    "kept AND b IN onSheet")
VREL = """RETURN a, b, apoc.create.vRelationship(a, 'FLOWS_TO',
         {via: via, lines: lines, steps: length(p), crossSheet: crossSheet}, b) AS flow"""

text = f"""// Conceptual view of the plant graph, as in the app's Graph tab (Detail level: Conceptual).
//
// Shows main equipment and off-page connectors only. Valves, fittings, motors, actuators and instruments are
// collapsed, and each conceptual node is linked to the next one reached along the pipes (FLOWS_TO, carrying the
// items passed in `via` and the pipe lines in `lines`). Nothing is written to the database: the links are
// computed by the query.
//
// The type lists come from graph_levels.yaml (after changing it, run: python make_conceptual_queries.py). Run one query at a time in
// Neo4j Browser. Tip: turn off Browser Settings -> "Connect result nodes", otherwise Browser also draws the stored
// relationships between the nodes it shows, next to the conceptual ones.

// 1. Conceptual graph of the whole plant (needs the APOC plugin for the virtual FLOWS_TO links).
//    Check APOC with:  RETURN apoc.version();
{KEPT}
{PATHS_ALL}
{VREL}
UNION
// conceptual nodes with no flow link to another one still appear
{KEPT}
UNWIND kept AS a
RETURN a, a AS b, null AS flow;


// 2. The same for one sheet: change the sheet name on the marked line. A pipe run still stops at equipment on
//    other sheets; only the start and end points must be on this sheet.
{KEPT}
{PATHS_SHEET}
{VREL};


// 3. Without APOC: the conceptual links as a table (from, to, the items passed and the pipe lines).
{KEPT}
{PATHS_ALL}
RETURN a.name AS from, a.type AS fromType, b.name AS to, b.type AS toType, via, lines, crossSheet
ORDER BY from, to;
"""
open("queries_conceptual.cypher", "w", encoding="utf-8", newline="\n").write(text)
print(f"main {len(main)}, boundary {len(boundary)}, collapsed {len(other)}, motor descriptions {desc}")
