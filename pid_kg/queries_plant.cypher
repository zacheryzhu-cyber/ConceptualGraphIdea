// Demo queries for the plant knowledge graph. Neo4j holds the conceptual level only: main equipment and off-page
// connectors (:Asset:Equipment / :Asset:Boundary) joined by FLOWS_TO, instrument loops (:Loop) with MEASURES /
// CONTROLS, and the valves loops drive (:Valve). The complete graph of each sheet is in snapshots/<sheet>.json.
// Run one at a time in Neo4j Browser. Comments give the results for =P1.H3.FB.001 + =P1.H4.FB.001.

// 1. Plant structure: plant -> areas -> systems -> sheets
MATCH path = (:Plant)-[:HAS_AREA]->(:Area)-[:HAS_SYSTEM]->(:System)-[:HAS_SHEET]->(:Sheet)
RETURN path;

// 2. Everything on one sheet (change the sheet name)
MATCH (s:Sheet {name: '=P1.H4.FB.001'})-[:DEPICTS]->(a:Asset)
OPTIONAL MATCH (a)-[f:FLOWS_TO]->(b:Asset)
RETURN a, f, b;

// 3. Which sheets show this asset?
MATCH (s:Sheet)-[:DEPICTS]->(a:Asset {tag: 'B001'})
RETURN a.name AS asset, s.name AS sheet, s.revision AS revision;

// 4. Equipment downstream of an asset, across sheets
MATCH (p:Asset {name: 'B001 (P1.H3.T2)'})-[:FLOWS_TO*1..]->(e:Asset:Equipment)
RETURN DISTINCT e.name AS equipment, e.type AS type, e.sheets AS on_sheets
ORDER BY equipment;

// 5. Flow path between two items, with the valves and fittings each step passes
MATCH path = shortestPath((a:Asset {tag: 'F001'})-[:FLOWS_TO*]->(b:Asset {tag: 'k001'}))
UNWIND relationships(path) AS f
RETURN startNode(f).name AS from, endNode(f).name AS to, f.via AS via, f.lines AS lines;

// 6. Sheet connectivity map: which sheets connect, and how
MATCH (a:Asset)-[f:FLOWS_TO {crossSheet: true}]->(b:Asset)
UNWIND a.sheets AS s1 UNWIND b.sheets AS s2 WITH a, b, f, s1, s2 WHERE s1 <> s2
RETURN DISTINCT s1 AS from_sheet, s2 AS to_sheet, a.name + ' -> ' + b.name AS flow, f.lines AS lines
UNION
MATCH (s1:Sheet)-[:DEPICTS]->(a:Asset)<-[:DEPICTS]-(s2:Sheet) WHERE s1.name < s2.name
RETURN s1.name AS from_sheet, s2.name AS to_sheet, 'shared tag ' + a.name AS flow, [] AS lines;

// 7. Sheets not connected to any other sheet
MATCH (s:Sheet)
WHERE NOT EXISTS { MATCH (s)-[:DEPICTS]->(:Asset)-[:FLOWS_TO {crossSheet: true}]-(:Asset)<-[:DEPICTS]-(o:Sheet) WHERE o <> s }
  AND NOT EXISTS { MATCH (s)-[:DEPICTS]->(:Asset)<-[:DEPICTS]-(o:Sheet) WHERE o <> s }
RETURN s.name AS isolated_sheet;

// 8. Control loops: what each loop measures and what it drives
MATCH (l:Loop)
OPTIONAL MATCH (l)-[:MEASURES]->(m)
OPTIONAL MATCH (l)-[:CONTROLS]->(c)
RETURN l.tag AS loop, l.functions AS functions, l.alarms AS alarms,
       collect(DISTINCT m.name) AS measures, collect(DISTINCT c.name) AS controls
ORDER BY loop;

// 9. Impact: if the valve a loop drives closes, which equipment loses supply?
//    (the valve is on the FLOWS_TO links through it, in `viaUids`)
MATCH (:Loop {name: 'L002'})-[:CONTROLS]->(v:Valve)
MATCH (a:Asset)-[f:FLOWS_TO]->(b:Asset) WHERE v.uid IN f.viaUids
MATCH (b)-[:FLOWS_TO*0..]->(e:Asset:Equipment)
RETURN v.name AS valve, collect(DISTINCT e.name) AS affected_equipment;
