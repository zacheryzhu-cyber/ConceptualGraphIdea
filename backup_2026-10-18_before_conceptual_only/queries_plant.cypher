// Demo queries for the multi-sheet plant knowledge graph.
// Run one at a time in Neo4j Browser / Query. Expected results are for the 3 test sheets
// (101 = sample A, 102 = sheet B, 201 = sheet C) loaded with test_sheets/manifest.csv.

// 1. Plant structure: plant -> areas -> systems -> sheets
MATCH path = (:Plant)-[:HAS_AREA]->(:Area)-[:HAS_SYSTEM]->(:System)-[:HAS_SHEET]->(:Sheet)
RETURN path;

// 2. Everything on one sheet (change '101' to any sheet name)
MATCH (s:Sheet {name: '101'})-[:DEPICTS]->(a:Asset)
OPTIONAL MATCH (a)-[r]->(b:Asset) WHERE r.sheet = s.name
RETURN a, r, b;

// 3. Which sheets show this asset?   Expected for H1008: 101 and 102
MATCH (s:Sheet)-[:DEPICTS]->(a:Asset {name: 'H1008'})
RETURN a.name AS asset, s.name AS sheet, s.revision AS revision;

// 4. Equipment downstream of P4711, following pipes ACROSS sheets
//    Expected: H1007, H1008, H2007, P4712, P5711, P5712, T4750, T5750
MATCH (p:Asset {name: 'P4711'})-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*1..]->(e:Equipment)
OPTIONAL MATCH (s:Sheet)-[:DEPICTS]->(e)
RETURN DISTINCT e.name AS equipment, e.type AS type, collect(DISTINCT s.name) AS on_sheets
ORDER BY equipment;

// 5. Draw the flow path from P4711 (sheet 101) to tank T5750 (sheet 102)
//    Expected: 16 steps, crossing to sheet 102 through H1008 (the exchanger drawn on both sheets)
MATCH path = shortestPath((p:Asset {name: 'P4711'})-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*]->(t:Asset {name: 'T5750'}))
RETURN path;

// 6. Sheet connectivity map: which sheets connect, and how
//    Expected: 101 -> 102 via line 47126, and 101 + 102 share tag H1008. Sheet 201 does not appear.
MATCH (s1:Sheet)-[:DEPICTS]->(o:Asset)-[:CONTINUES_TO]->(i:Asset)<-[:DEPICTS]-(s2:Sheet)
RETURN s1.name AS from_sheet, s2.name AS to_sheet, 'off-page line ' + o.lineNumber AS connection
UNION
MATCH (s1:Sheet)-[:DEPICTS]->(a:Asset {tagged: true})<-[:DEPICTS]-(s2:Sheet)
WHERE s1.name < s2.name
RETURN s1.name AS from_sheet, s2.name AS to_sheet, 'shared tag ' + a.name AS connection;

// 7. Sheets not connected to any other sheet (islands)   Expected: 201
MATCH (s:Sheet)
WHERE NOT EXISTS { MATCH (s)-[:DEPICTS]->(:Asset)-[:CONTINUES_TO]-(:Asset)<-[:DEPICTS]-(other:Sheet) WHERE other <> s }
  AND NOT EXISTS { MATCH (s)-[:DEPICTS]->(:Asset {tagged: true})<-[:DEPICTS]-(other:Sheet) WHERE other <> s }
RETURN s.name AS isolated_sheet;

// 8. Control loops across the plant, with their sheet
MATCH (sen:ProcessSignalGeneratingFunction)-[:MEASURING_LINE_FUNCTION]->(f:ProcessInstrumentationFunction)
      -[:SIGNAL_CONVEYING_FUNCTION]->(act:ActuatingFunction)-[:OPERATED_VALVE_REFERENCE]->(v:Asset),
      (s:Sheet)-[:DEPICTS]->(sen)
RETURN s.name AS sheet, sen.name AS sensor, f.name AS loop, act.name AS actuator, v.name AS valve
ORDER BY sheet, loop;

// 9. Impact: if the valve driven by PV4712.02 closes, which equipment (on any sheet) loses supply?
//    Expected: H1008, H2007, P4712, P5711, P5712, T4750, T5750
MATCH (:Asset {name: 'PV4712.02'})-[:OPERATED_VALVE_REFERENCE]->(v:Asset)
MATCH (v)-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*1..]->(e:Equipment)
RETURN v.name AS valve, collect(DISTINCT e.name) AS affected_equipment;
