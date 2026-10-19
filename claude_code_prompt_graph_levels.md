# Claude Code prompt: three detail levels for the graph view (Complete / Process / Conceptual)

**Prerequisite:** apply `claude_code_prompt_fix_islands.md` first. The Conceptual level depends on the pipe-through-nozzle links. Without them, almost every equipment-to-equipment link is missing (I tested this: 4 links instead of 18).

Paste everything below the line into Claude Code, in the PoC project folder.

---

My whole-plant graph shows every DEXPI object (the "complete" level), which is too messy to read. Add two simpler views, following the three graph levels in the ChatP&ID paper (TU Delft, arXiv 2603.22528):
- **Complete:** every object, exactly as stored (today's view)
- **Process:** pipe fittings collapsed. Equipment, valves, off-page connectors and instrument loops remain.
- **Conceptual:** valves collapsed too. Only main equipment and off-page connectors remain, with flow links between them.

## Ground rule: generic code only
Every fix must be a general rule that works on **any** DEXPI file (COMOS or not), driven only by the XML structure and DEXPI class/association names.
- **Never hard-code tags, IDs, sheet names or specific links** (no `if tag == "F001"`, no lists of expected connections in the code).
- The tags below are only examples that explain each rule, and expected results for my two test files.
- Put the expected results in `tests/expected_<sheetset>.json` and check them with an automated test (`pytest`), not in the loader.
- **Generality test:** also run the loader on every other DEXPI file in the project (e.g. the DEXPI example C01V04-VER.EX01.xml and any files in `test_sheets/`). It must not crash, and the number of links it produces must be the same or higher than before the change. Report any file where a rule never triggered or failed.

## Rules for building this

1. **Do not change what's stored in Neo4j.** The complete graph stays the single source of truth. Compute Process and Conceptual in Python (networkx) from the stored graph when the view is drawn, so they update automatically when sheets are loaded or removed. Don't create new relationship types in the database.

2. **Put the classification in a config file, `graph_levels.yaml`**, keyed by the DEXPI class name exactly as it appears in the node `type` (don't rename classes). Start with:

   ```yaml
   main_equipment:   [RotaryPump, Pump, VesselWithDishedHeadsWithOptionalAgitator, HeatExchanger,
                      Separator, CustomEquipment, Tank, Vessel, BubbleTrayColumn]
   valve:            [GlobeValve, CheckValve, TightShutOffValve, ControlValve, BallValve, GateValve, ButterflyValve]
   boundary:         [FlowInPipeOffPageConnector, FlowOutPipeOffPageConnector]
   pass_through:     [PipeTee, PipeReducer, PipeFlowArrow, Nozzle]          # collapsed at Process and Conceptual
   fold_into_equipment: [Motor, Agitator]                                    # plus 'Equipment' whose description starts with "Three-phase motor" / "Electric motor"
   fold_into_valve:  [ActuatingSystem, ActuatingFunction]
   fold_into_instrument: [Alarm]
   instrument:       [ProcessInstrumentationFunction]
   loop:             [InstrumentationLoopFunction]
   ```

   Any type not listed: if its category is Equipment, treat it as main_equipment; otherwise treat it as pass_through. Either way, list it under "Unclassified types" in the Validation tab so I can put it in the right group.

3. **Flow links (both levels):** start from every node that is kept at that level. Follow PIPE relationships in their direction through nodes that aren't kept, and create one link to each kept node reached. On each link, record `via` (the names of the collapsed items, in order) and `lines` (the line FullLabels passed, e.g. `=P1.H3.T2.P006`). A tee therefore turns into its branches: V002→R004→B001 and V004→R004→B001 become V002→B001 and V004→B001. Keep cross-sheet PIPE links (`crossSheet: true`) and draw them dashed.

4. **Process level:**
   - **Nodes:** main_equipment, valve, boundary, and one node per InstrumentationLoopFunction (named by its loop tag, e.g. `=P1.H3.T1.Y024`), in place of its individual ProcessInstrumentationFunctions (YS+, GO+-, …).
   - **Folding:**
     - Motors and agitators fold into their equipment, shown in the tooltip ("motor M1 (+0.L001…MO02)").
     - ActuatingSystem/ActuatingFunction fold into the valve they operate (valve tooltip: "actuated, loop Y024").
     - Alarms fold into their instrument, and so into the loop node (tooltip: "LISA.AH").
   - **Loop links:** connect each loop to what its members measure or act on. Use MEASURING_LINE_FUNCTION targets (equipment, or the pipe → the nearest kept node on that line) and OPERATED_VALVE_REFERENCE / FULFILLS targets (valves). Draw these as dotted "signal" links, distinct from pipes.

5. **Conceptual level:**
   - **Nodes:** main_equipment and boundary only.
   - **Links:** flow links pass through valves as well. `via` lists the valves, e.g. P001 → B001 via V002, R001, B001-valve.
   - **Instruments:** become a list on the equipment's tooltip, grouped by loop ("L001 LISA, T001 TI, P001 PI").
   - **Loops attached to a pipe rather than equipment:** list them on the flow link's tooltip.

6. **Graph tab:** add a **Detail level** selector (Conceptual / Process / Complete).
   - Default: **Conceptual** for "Whole plant", **Process** for "Around" and "Downstream", **Complete** for "One sheet". I can change the selector in any mode.
   - The Flow layout (BFS levels) must use the flow links of the chosen level.
   - Clicking a node shows what was folded into it. Clicking a link shows `via` and `lines`.
   - The adjacency JSON export should export the chosen level, with `"level"` added to the header.

7. **Ask tab:** questions about "what is connected / upstream / downstream" should use the Process level, so answers list valves and equipment, not tees and arrows.

## Acceptance test (expected results on my H3/H4 files; check with the pytest, never hard-code)
Reset, load H3 and H4, open Graph → Whole plant. Expected counts after the island fixes:

| Level | Nodes | Links |
|---|---|---|
| Complete | ~135 + loop/line nodes | ~200 (all relationship types) |
| Process (pipe network only, before loop nodes) | 39 | 43 flow links |
| Conceptual | 16 | 18 flow links |

The Conceptual view must show these links (names as in my graph):
- F001 → P001
- F001 → P002
- P001 → B001 (P1.H3.T2)
- P002 → B001 (P1.H3.T2)
- B001 (P1.H3.T2) → B003 (cross-sheet, via line =P1.H3.T2.P006)
- B003 → F001
- B001 → P002 (P1.H4.T3)
- B001 → P003
- P002 (P1.H4.T3) → k001
- P003 → k001
- k001 → W001
- k001 → W002
- W002 → k001
- W001 → B001
- off-page connectors 002 / 003 / 004 on W001 and W002

Also:
- P002 (P1.H4.T1) appears alone, because it has no pipe in the export. That's expected.
- Switching levels doesn't change anything in Neo4j: the stored node and relationship counts in the Plant tab stay the same.

Show me a screenshot of each level for Whole plant, and of Process for "Around P001".
