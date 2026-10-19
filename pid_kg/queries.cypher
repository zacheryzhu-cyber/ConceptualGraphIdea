// Demo queries for the P&ID knowledge graph (DEXPI reference P&ID C01).
// Run them one at a time in Neo4j Browser.

// 1. Show the whole graph
MATCH (n:Asset)-[r]->(m:Asset) RETURN n, r, m;

// 2. List all equipment on the P&ID
MATCH (e:Equipment) RETURN e.name AS tag, e.type AS type ORDER BY tag;

// 3. Which equipment is downstream of pump P4711? (follows pipes in flow direction)
MATCH (p:Asset {name: 'P4711'})-[:PIPE|DIRECT_PIPING_CONNECTION*1..]->(e:Equipment)
RETURN DISTINCT e.name AS downstream_equipment, e.type AS type;

// 4. Show the actual flow path from P4711 to tank T4750
MATCH path = shortestPath((p:Asset {name: 'P4711'})-[:PIPE|DIRECT_PIPING_CONNECTION*]->(t:Asset {name: 'T4750'}))
RETURN path;

// 5. Control loops: sensor -> controller function -> actuator -> valve
MATCH (s:ProcessSignalGeneratingFunction)-[:MEASURING_LINE_FUNCTION]->(f:ProcessInstrumentationFunction)
      -[:SIGNAL_CONVEYING_FUNCTION]->(a:ActuatingFunction)-[:OPERATED_VALVE_REFERENCE]->(v:Asset)
RETURN s.name AS sensor, f.name AS loop, a.name AS actuator, v.name AS valve;

// 6. Impact question: if the valve driven by PV4712.02 closes, which equipment loses supply?
//    Expected: H1008, P4712, T4750
MATCH (:Asset {name: 'PV4712.02'})-[:OPERATED_VALVE_REFERENCE]->(v:Asset)
MATCH (v)-[:PIPE|DIRECT_PIPING_CONNECTION*1..]->(e:Equipment)
RETURN v.name AS valve, collect(DISTINCT e.name) AS affected_equipment;
