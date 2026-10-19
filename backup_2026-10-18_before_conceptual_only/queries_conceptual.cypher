// Conceptual view of the plant graph, as in the app's Graph tab (Detail level: Conceptual).
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
WITH ['BubbleTrayColumn', 'CustomEquipment', 'HeatExchanger', 'Pump', 'RotaryPump', 'Separator', 'Tank', 'Vessel', 'VesselWithDishedHeadsWithOptionalAgitator'] AS mainTypes,
     ['FlowInPipeOffPageConnector', 'FlowOutPipeOffPageConnector'] AS boundaryTypes,
     ['ActuatingFunction', 'ActuatingSystem', 'Agitator', 'Alarm', 'BallValve', 'ButterflyValve', 'CheckValve', 'ControlValve', 'GateValve', 'GlobeValve', 'InstrumentationLoopFunction', 'Motor', 'Nozzle', 'PipeFlowArrow', 'PipeReducer', 'PipeTee', 'PipingNetworkSegment', 'PipingNetworkSystem', 'ProcessInstrumentationFunction', 'TightShutOffValve'] AS collapsedTypes,
     ['Three-phase motor', 'Electric motor'] AS motorDescriptions
// conceptual nodes: main equipment and off-page connectors. Equipment-category items not listed in
// graph_levels.yaml count as main equipment, except motors (by description), as in the app.
MATCH (a:Asset)
WHERE a.type IN mainTypes OR a.type IN boundaryTypes
   OR (a.category = 'Equipment' AND NOT a.type IN collapsedTypes
       AND none(d IN motorDescriptions WHERE coalesce(a.description, '') STARTS WITH d))
WITH collect(a) AS kept
UNWIND kept AS a
// follow the pipes (in flow direction) through anything that isn't a conceptual node, to the next one
MATCH p = (a)-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*1..25]->(b:Asset)
WHERE b <> a AND b IN kept
  AND none(n IN nodes(p)[1..-1] WHERE n IN kept)
  AND all(r IN relationships(p) WHERE coalesce(r.status, '') <> 'candidate')   // skip unreviewed PDF-check links
WITH a, b, p ORDER BY length(p)
WITH a, b, head(collect(p)) AS p                                             // shortest run between each pair
WITH a, b, p,
     [n IN nodes(p)[1..-1] | n.name] AS via,
     reduce(acc = [], r IN relationships(p) |
            CASE WHEN r.lineNumber IS NULL OR r.lineNumber IN acc THEN acc ELSE acc + r.lineNumber END) AS lines,
     any(r IN relationships(p) WHERE type(r) = 'CONTINUES_TO' OR r.crossSheet) AS crossSheet
RETURN a, b, apoc.create.vRelationship(a, 'FLOWS_TO',
         {via: via, lines: lines, steps: length(p), crossSheet: crossSheet}, b) AS flow
UNION
// conceptual nodes with no flow link to another one still appear
WITH ['BubbleTrayColumn', 'CustomEquipment', 'HeatExchanger', 'Pump', 'RotaryPump', 'Separator', 'Tank', 'Vessel', 'VesselWithDishedHeadsWithOptionalAgitator'] AS mainTypes,
     ['FlowInPipeOffPageConnector', 'FlowOutPipeOffPageConnector'] AS boundaryTypes,
     ['ActuatingFunction', 'ActuatingSystem', 'Agitator', 'Alarm', 'BallValve', 'ButterflyValve', 'CheckValve', 'ControlValve', 'GateValve', 'GlobeValve', 'InstrumentationLoopFunction', 'Motor', 'Nozzle', 'PipeFlowArrow', 'PipeReducer', 'PipeTee', 'PipingNetworkSegment', 'PipingNetworkSystem', 'ProcessInstrumentationFunction', 'TightShutOffValve'] AS collapsedTypes,
     ['Three-phase motor', 'Electric motor'] AS motorDescriptions
// conceptual nodes: main equipment and off-page connectors. Equipment-category items not listed in
// graph_levels.yaml count as main equipment, except motors (by description), as in the app.
MATCH (a:Asset)
WHERE a.type IN mainTypes OR a.type IN boundaryTypes
   OR (a.category = 'Equipment' AND NOT a.type IN collapsedTypes
       AND none(d IN motorDescriptions WHERE coalesce(a.description, '') STARTS WITH d))
WITH collect(a) AS kept
UNWIND kept AS a
RETURN a, a AS b, null AS flow;


// 2. The same for one sheet: change the sheet name on the marked line. A pipe run still stops at equipment on
//    other sheets; only the start and end points must be on this sheet.
WITH ['BubbleTrayColumn', 'CustomEquipment', 'HeatExchanger', 'Pump', 'RotaryPump', 'Separator', 'Tank', 'Vessel', 'VesselWithDishedHeadsWithOptionalAgitator'] AS mainTypes,
     ['FlowInPipeOffPageConnector', 'FlowOutPipeOffPageConnector'] AS boundaryTypes,
     ['ActuatingFunction', 'ActuatingSystem', 'Agitator', 'Alarm', 'BallValve', 'ButterflyValve', 'CheckValve', 'ControlValve', 'GateValve', 'GlobeValve', 'InstrumentationLoopFunction', 'Motor', 'Nozzle', 'PipeFlowArrow', 'PipeReducer', 'PipeTee', 'PipingNetworkSegment', 'PipingNetworkSystem', 'ProcessInstrumentationFunction', 'TightShutOffValve'] AS collapsedTypes,
     ['Three-phase motor', 'Electric motor'] AS motorDescriptions
// conceptual nodes: main equipment and off-page connectors. Equipment-category items not listed in
// graph_levels.yaml count as main equipment, except motors (by description), as in the app.
MATCH (a:Asset)
WHERE a.type IN mainTypes OR a.type IN boundaryTypes
   OR (a.category = 'Equipment' AND NOT a.type IN collapsedTypes
       AND none(d IN motorDescriptions WHERE coalesce(a.description, '') STARTS WITH d))
WITH collect(a) AS kept
MATCH (:Sheet {name: '=P002.A13.FB.001'})-[:DEPICTS]->(x:Asset)   // <- your sheet name
WITH kept, collect(x) AS onSheet
UNWIND [k IN kept WHERE k IN onSheet] AS a
// follow the pipes (in flow direction) through anything that isn't a conceptual node, to the next one
MATCH p = (a)-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*1..25]->(b:Asset)
WHERE b <> a AND b IN kept AND b IN onSheet
  AND none(n IN nodes(p)[1..-1] WHERE n IN kept)
  AND all(r IN relationships(p) WHERE coalesce(r.status, '') <> 'candidate')   // skip unreviewed PDF-check links
WITH a, b, p ORDER BY length(p)
WITH a, b, head(collect(p)) AS p                                             // shortest run between each pair
WITH a, b, p,
     [n IN nodes(p)[1..-1] | n.name] AS via,
     reduce(acc = [], r IN relationships(p) |
            CASE WHEN r.lineNumber IS NULL OR r.lineNumber IN acc THEN acc ELSE acc + r.lineNumber END) AS lines,
     any(r IN relationships(p) WHERE type(r) = 'CONTINUES_TO' OR r.crossSheet) AS crossSheet
RETURN a, b, apoc.create.vRelationship(a, 'FLOWS_TO',
         {via: via, lines: lines, steps: length(p), crossSheet: crossSheet}, b) AS flow;


// 3. Without APOC: the conceptual links as a table (from, to, the items passed and the pipe lines).
WITH ['BubbleTrayColumn', 'CustomEquipment', 'HeatExchanger', 'Pump', 'RotaryPump', 'Separator', 'Tank', 'Vessel', 'VesselWithDishedHeadsWithOptionalAgitator'] AS mainTypes,
     ['FlowInPipeOffPageConnector', 'FlowOutPipeOffPageConnector'] AS boundaryTypes,
     ['ActuatingFunction', 'ActuatingSystem', 'Agitator', 'Alarm', 'BallValve', 'ButterflyValve', 'CheckValve', 'ControlValve', 'GateValve', 'GlobeValve', 'InstrumentationLoopFunction', 'Motor', 'Nozzle', 'PipeFlowArrow', 'PipeReducer', 'PipeTee', 'PipingNetworkSegment', 'PipingNetworkSystem', 'ProcessInstrumentationFunction', 'TightShutOffValve'] AS collapsedTypes,
     ['Three-phase motor', 'Electric motor'] AS motorDescriptions
// conceptual nodes: main equipment and off-page connectors. Equipment-category items not listed in
// graph_levels.yaml count as main equipment, except motors (by description), as in the app.
MATCH (a:Asset)
WHERE a.type IN mainTypes OR a.type IN boundaryTypes
   OR (a.category = 'Equipment' AND NOT a.type IN collapsedTypes
       AND none(d IN motorDescriptions WHERE coalesce(a.description, '') STARTS WITH d))
WITH collect(a) AS kept
UNWIND kept AS a
// follow the pipes (in flow direction) through anything that isn't a conceptual node, to the next one
MATCH p = (a)-[:PIPE|DIRECT_PIPING_CONNECTION|CONTINUES_TO*1..25]->(b:Asset)
WHERE b <> a AND b IN kept
  AND none(n IN nodes(p)[1..-1] WHERE n IN kept)
  AND all(r IN relationships(p) WHERE coalesce(r.status, '') <> 'candidate')   // skip unreviewed PDF-check links
WITH a, b, p ORDER BY length(p)
WITH a, b, head(collect(p)) AS p                                             // shortest run between each pair
WITH a, b, p,
     [n IN nodes(p)[1..-1] | n.name] AS via,
     reduce(acc = [], r IN relationships(p) |
            CASE WHEN r.lineNumber IS NULL OR r.lineNumber IN acc THEN acc ELSE acc + r.lineNumber END) AS lines,
     any(r IN relationships(p) WHERE type(r) = 'CONTINUES_TO' OR r.crossSheet) AS crossSheet
RETURN a.name AS from, a.type AS fromType, b.name AS to, b.type AS toType, via, lines, crossSheet
ORDER BY from, to;
