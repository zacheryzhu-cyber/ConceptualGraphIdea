# Claude Code prompt: store only the conceptual level in Neo4j

**Prerequisite:** the island-fix, detail-level and PDF cross-check prompts are already implemented (my last export showed `pdfCheck` on links). This prompt changes **what gets written to Neo4j**, not how DEXPI is read.

Paste everything below the line into Claude Code, in the PoC project folder.

---

My Neo4j graph currently stores every DEXPI object: nozzles, pipe pieces, tees, actuating systems, instrument functions. That makes the database confusing. From now on **Neo4j holds only the conceptual level**. The full detail is still built during loading and is kept as a file per sheet, not in Neo4j.

## Ground rule (same as before)
- Generic code only: no hard-coded tags, IDs or sheet names.
- Classification comes from `graph_levels.yaml`.
- Expected results for my files live in pytest files only.

## 1. Loading pipeline
```
DEXPI file ──► existing reader + island-fix rules ──► complete graph in memory (networkx)
(optional matching PDF ──► existing PDF cross-check ──► candidate links on the in-memory graph)
          ──► existing conceptual builder (graph_levels.yaml)
          ──► write ONLY the conceptual result to Neo4j
          ──► save the complete graph as snapshots/<sheet>.json
```
- **Reading, fixing, validation and the PDF check** stay exactly as they are. They run on the in-memory graph during loading.
- **The PDF is optional.** Without a PDF the sheet loads normally. The Load tab says "Loaded without drawing check" and the summary shows the number of equipment items with no connection, so I can decide whether to add the PDF.

## 2. What goes into Neo4j (and nothing else)
- **Hierarchy:** `Plant`, `Area`, `Unit`, `Sheet` nodes and `PART_OF` / `DEPICTS` relationships, as today.
- **Main equipment and off-page connectors:** the `main_equipment` and `boundary` groups in `graph_levels.yaml`.
  - Keep uid, tag, fullTag, type (DEXPI class name exactly as in the file), description and sheets.
  - Store what was folded in (motor, agitator) as a list property `folded`.
- **Instrument loops:** one node per InstrumentationLoopFunction (e.g. `=P1.H4.T1.L002`), with the function letters (`LICSA`) and its alarms as properties.
- **Relationships:**
  - `FLOWS_TO` between equipment and boundaries, with properties:
    - `via`: collapsed valves and fittings, in order
    - `lines`: line FullLabels
    - `crossSheet`
    - `source`: `dexpi` / `drawing`
    - `status`: for drawing candidates
    - `pdfCheck`
  - `MEASURES`: loop → the equipment it measures. If it measures a pipe, attach it to the `FLOWS_TO` link's `loops` property instead.
  - `CONTROLS`: loop → the control valve it drives, kept as a small node only for valves that a loop controls (e.g. H001). Otherwise valves stay inside `via`.
- **Not stored:** nozzles, pipe segments, tees, reducers, flow arrows, actuating systems and functions, individual instrument functions (YS+, GO+-), and dummy or export artefacts.

## 3. Snapshots for detail
- `snapshots/<sheet>.json` holds the complete in-memory graph for that sheet, in the same format as the adjacency export.
- **Graph tab, detail level:** Conceptual reads Neo4j. Process and Complete read the snapshots of the sheets in view (computed with the existing level code). Label them "from file snapshot".
- **Clicking a `FLOWS_TO` link** shows its `via` and offers "Show detail", which opens the Process view of just that path from the snapshot.
- **Removing a sheet** deletes its Neo4j nodes and links and its snapshot.

## 4. Migration
- Add a "Rebuild graph" button in the Plant tab. It resets Neo4j and re-loads every sheet from the saved source files (keep each loaded DEXPI and PDF in `sources/`), so the conceptual-only database is built cleanly.
- Keep approved and rejected drawing candidates across rebuilds. Store the decisions in `sources/decisions.json`, keyed by the two assets' uids.

## 5. Acceptance test (expected results on my files; check with pytest, never hard-code)
Rebuild with H3 and H4:
- **H4 without its PDF:**
  - Neo4j has 16 equipment/boundary nodes and 18 `FLOWS_TO`.
  - Pump `P002 · H4.T1` has no `FLOWS_TO`.
  - The Load tab reports 1 unconnected equipment item.
- **H4 with `p1_h4.pdf`, after approving the 2 candidates:** 20 `FLOWS_TO`, including F001 → P002 · H4.T1 → k001.
- **Clean database:** Neo4j contains no nodes with the types Nozzle, PipeTee, PipeReducer, ActuatingSystem, ActuatingFunction, or ProcessInstrumentationFunction.
- **Loops:** the L002 loop node `MEASURES` B003 and `CONTROLS` H001.
- **Detail views:** the Process and Complete levels still work from the snapshots, and show the same content as before this change.
- **Hierarchy:** the Plant tab counts and sheet list are unchanged apart from the lower node and relationship numbers.

Show me Neo4j Browser's node and relationship counts by label and type after the rebuild, and the Conceptual graph.
