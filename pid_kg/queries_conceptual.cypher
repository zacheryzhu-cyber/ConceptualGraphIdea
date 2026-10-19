// The conceptual level as Neo4j stores it (plant_store.py writes it on every load, remove and rebuild).
// No APOC needed: the FLOWS_TO links are stored, with the collapsed valves and fittings in `via`.
// Run one query at a time in Neo4j Browser. Comments give the results for =P1.H3.FB.001 + =P1.H4.FB.001
// loaded (with or without p1_h4.pdf: the PDF only confirms the links).

// 1. Node counts by label   (Loop 31, Asset 16, Valve 14, Equipment 12, Boundary 4, Sheet 2, ...)
MATCH (n) UNWIND labels(n) AS label
RETURN label, count(*) AS nodes ORDER BY nodes DESC, label;

// 2. Relationship counts by type   (DEPICTS 61, CONTROLS 20, FLOWS_TO 20, MEASURES 6, ...)
MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS relationships ORDER BY relationships DESC, type;

// 3. The conceptual graph: main equipment and off-page connectors with the flows between them
MATCH (a:Asset) OPTIONAL MATCH (a)-[f:FLOWS_TO]->(b:Asset)
RETURN a, f, b;

// 4. The same as a table, with what each flow passes
MATCH (a:Asset)-[f:FLOWS_TO]->(b:Asset)
RETURN a.name AS from, b.name AS to, f.via AS via, f.lines AS lines, f.crossSheet AS crossSheet,
       f.source AS source, f.status AS status, f.pdfCheck AS pdfCheck, f.loops AS loops
ORDER BY from, to;

// 5. With the loops: what they measure and the valves they drive
MATCH (a:Asset) OPTIONAL MATCH (a)-[f:FLOWS_TO]->(b:Asset)
RETURN a, f, b
UNION
MATCH (l:Loop)-[r:MEASURES|CONTROLS]->(t)
RETURN l AS a, r AS f, t AS b;

// 6. Main equipment with no flow (none for H3 + H4)
MATCH (a:Asset:Equipment) WHERE NOT (a)-[:FLOWS_TO]-()
RETURN a.name AS equipment, a.type AS type, a.sheets AS sheets;

// 7. Flows through links proposed by the PDF check and approved (none for H3 + H4)
MATCH (a:Asset)-[f:FLOWS_TO {source: 'drawing'}]->(b:Asset)
RETURN a.name AS from, b.name AS to, f.status AS status, f.via AS via;

// 8. Clean database check: none of the detail objects is stored (expect 0)
MATCH (n) WHERE n.type IN ['Nozzle', 'PipeTee', 'PipeReducer', 'ActuatingSystem', 'ActuatingFunction',
                           'ProcessInstrumentationFunction']
RETURN count(n) AS detail_nodes;

// 9. One loop, e.g. L002 (MEASURES B003, CONTROLS H001)
MATCH (l:Loop {name: 'L002'})-[r]->(t)
RETURN l.tag AS loop, l.functions AS functions, l.alarms AS alarms, type(r) AS rel, t.name AS target;
