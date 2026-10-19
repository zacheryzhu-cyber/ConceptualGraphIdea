# Claude Code prompt: fix the stand-alone islands in the COMOS DEXPI mapping

Paste everything below the line into Claude Code, in the PoC project folder.

---

My knowledge graph has stand-alone islands after loading the two COMOS DEXPI sheets =P1.H3.FB.001 and =P1.H4.FB.001. My adjacency export currently shows **135 nodes in 33 islands, 13 of them single nodes**. The cause is not missing data: every link below exists in the DEXPI XML, but the loader does not read it.

Read the XML directly with `xml.etree.ElementTree` alongside the existing pyDEXPI route. Do not change the existing asset identity (uids such as `TAG:=P1.H3.T1-V002` and `<sheet>::<proteusId>`). Use the DEXPI association and class names as the relationship and label names, in upper case, the same way the graph already uses `OPERATED_VALVE_REFERENCE` and `MEASURING_LINE_FUNCTION`.

Build a helper `owner_uid(xml_id)` that resolves any XML ID to an existing graph node:
1. use `<sheet>::<ID>` if that node exists
2. otherwise, use the node whose fullTag equals the element's `FullLabel` GenericAttribute
3. otherwise, walk to the parent element when the element is a `Nozzle`, a `Nozzles` (COMOS writes this element name, which the XSD doesn't have), an `ActuatingSystemComponent`, or a `CenterLine`

## Ground rule: generic code only
Every fix must be a general rule that works on **any** DEXPI file (COMOS or not), driven only by the XML structure and DEXPI class/association names.
- **Never hard-code tags, IDs, sheet names or specific links** (no `if tag == "F001"`, no lists of expected connections in the code).
- The tags below are only examples that explain each rule, and expected results for my two test files.
- Put the expected results in `tests/expected_<sheetset>.json` and check them with an automated test (`pytest`), not in the loader.
- **Generality test:** also run the loader on every other DEXPI file in the project (e.g. the DEXPI example C01V04-VER.EX01.xml and any files in `test_sheets/`). It must not crash, and the number of links it produces must be the same or higher than before the change. Report any file where a rule never triggered or failed.

## The eight fixes

1. **Actuator → actuating function.** `<ActuatingSystem>` has `<Association Type="fulfills" ItemID="ActuatingFunctionN"/>`. Create `(ActuatingSystem)-[:FULFILLS]->(ActuatingFunction)`. This links the 8 ActuatingSystem↔"GO+-" pairs to their valve and loop. There are 14 such links.

2. **Instrument loops.** Each `<InstrumentationLoopFunction>` lists its members with `Association Type="is a collection including"`. Create an `InstrumentationLoopFunction` node (uid `<sheet>::<ID>`, name = its TagName/FullLabel, e.g. `=P1.H3.T1.Y024`), with `-[:IS_A_COLLECTION_INCLUDING]->` to each ProcessInstrumentationFunction. This joins "YS+ (P1.H3.T1.Y024)" and "GO+- (P1.H3.T1.Y024)", which belong to the same loop. There are 46 links.

3. **Measuring lines that end on a nozzle.** `<InformationFlow>` "has logical start/end" often points at a COMOS `<Nozzles>` element, e.g. TI → nozzle N04 on vessel B001. Resolve it to the equipment that owns the nozzle with `owner_uid`, and keep the nozzle tag as the property `nozzle`. There are 6 links (T001.TI, P001.PI on H3; P002.PISA, T002.TI on H4, …).

4. **Measuring lines that end on a pipe.** Some InformationFlows end on a `<CenterLine>` inside a `<PipingNetworkSegment>`, e.g. F001.FI, T001.TI, P002.PI, P003.PI on H4 → line =P1.H4.T1.P010. Create a `PipingNetworkSystem` node keyed by the line's FullLabel (uid `LINE:<FullLabel>`). Link the instrument to it with MEASURING_LINE_FUNCTION, and link the line to the components on that segment with `-[:CONTAINS]->`.

5. **Pipes from nozzle to nozzle.** For every `<PipingNetworkSegment>`, build the chain `[owner_uid(FromID)] + [owner_uid(item) for each inline item] + [owner_uid(ToID)]`. Drop None values and consecutive duplicates, then create PIPE between neighbours (keep the existing `r.sheet`, and add `line = FullLabel` of the parent PipingNetworkSystem). Today, segments without valves are dropped. That loses k001→W002, W002→k001, k001→W001 and W001→B001 on H4, and V003→k001 at nozzle R001. There are 10 links.

6. **Alarms.** COMOS exports alarms (e.g. `=P1.H3.T2.L001.LISA.AH`, "Alarm H") as top-level `<Equipment>` with no ComponentClass. Do not label these Equipment. Give them type `Alarm` and category `Instrumentation`, and link `(ProcessInstrumentationFunction)-[:HAS_ALARM]->(Alarm)`. Find the instrument by the FullLabel with the last part removed (`=P1.H3.T2.L001.LISA`). There are 3 links (LISA.AH, PI.AH, PISA.AH).

7. **Dummy node.** Skip ProcessInstrumentationFunctions with `ComponentClass="OrphanInformationFlowCollectorDummy"` (XMP_618 on H4). It's a COMOS export artefact, not a plant item.

8. **Open pipe ends between sheets.** COMOS writes the same pipe in both files, with an empty FromID or ToID where it leaves the sheet:
   - `=P1.H3.T2.P006`: H3 vessel B001 → open end … H4 open end → control valve H001
   - `=P1.H4.T3.P027`: H4 separator F001 → open end … H3 open end → tee R002 → pumps P001/P002

   When a sheet is loaded, store each open end as `(:OpenEnd {line: <FullLabel>, dir: 'out'|'in', sheet, asset: <uid of last/first item>})`. In `link_sheets`, connect an 'out' and an 'in' with the same line on different sheets using `PIPE {line, crossSheet: true}`. It must work in either load order, and removing a sheet must remove its OpenEnds and cross-sheet PIPEs.

## Also add to the Validation tab
A section **"Islands"** that lists every connected group of 3 nodes or fewer, by type and name, so mapping gaps stay visible. The logic is the same as `check_islands.py` in the project folder.

## Acceptance test (expected results on my H3/H4 files; check with the pytest, never hard-code)
Reset, load H3 and H4 (in both orders), export the whole-plant adjacency, and run `python check_islands.py <export>.json`. Expected:
- **3 islands, 0 single nodes** (down from 33 and 13)
- island 1: H3 + the H4 T1 section joined, with B001 (H3), P001, P002, B003, F001 and H001, through P006 and P027
- island 2: the H4 T3 section (column k001, W001, W002, B001 (H4), pumps P002 (T3) and P003). It only leaves the sheet through off-page connectors 001–004, to sheets not loaded yet
- island 3: `Pump P002 (P1.H4.T1)` + its motor. **Leave this as it is:** the pump has no nozzles and no pipe in the COMOS export. It's an export/drawing gap to report, not to guess
- the existing equipment and valve uids are unchanged

Show me the before/after island counts and the Islands section in the app.
