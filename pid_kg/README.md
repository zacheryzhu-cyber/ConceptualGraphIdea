# P&ID → Plant Knowledge Graph (pyDEXPI + Neo4j)

## What it does
Reads DEXPI (Proteus XML) P&ID sheets with the open-source pyDEXPI library and builds **one plant knowledge graph**.
Every sheet you load is **added** to the plant; sheets are linked to each other through matching off-page arrows
(same line number), open pipe ends of the same line, and shared tags (the same equipment tag on several sheets is one
node).

**Neo4j holds only the conceptual level.** Each sheet is read into a complete graph in memory (every nozzle, pipe
piece, tee, actuator and instrument function), checked against its PDF if you give one, and kept as a file
(`snapshots/<sheet>.json`). Neo4j gets the conceptual result of all sheets together:

```
DEXPI file ──► reader + island-fix rules ──► complete graph in memory (networkx)
(optional matching PDF ──► PDF drawing check ──► pdfCheck marks and candidate links on that graph)
          ──► snapshots/<sheet>.json          (the complete graph, adjacency export format)
          ──► conceptual builder (graph_levels.yaml) over all sheets ──► Neo4j
```

Graph model in Neo4j (nothing else is stored):
```
(Plant)-[:HAS_AREA]->(Area)-[:HAS_SYSTEM]->(System)-[:HAS_SHEET]->(Sheet)-[:DEPICTS]->(Asset | Loop | Valve)
(:Asset:Equipment | :Asset:Boundary {uid, name, tag, fullTag, type, description, sheets, folded})
      main equipment and off-page connectors (groups main_equipment and boundary in graph_levels.yaml); the DEXPI
      class is also a label; folded = what was folded in (motor, agitator)
(Asset)-[:FLOWS_TO {via, viaUids, lines, crossSheet, source, status, pdfCheck, loops, kind}]->(Asset)
      via: the collapsed valves and fittings, in order; lines: pipe line FullLabels; source 'dexpi' or 'drawing'
(:Loop {uid, name, tag, functions, alarms, sheets})        one per InstrumentationLoopFunction (e.g. =P1.H4.T1.L002)
(Loop)-[:MEASURES]->(Asset)                                 a loop measuring a pipe is in FLOWS_TO.loops instead
(Loop)-[:CONTROLS]->(Valve | Asset)                         the valve it drives (a small :Valve node, only for such
                                                            valves; other valves stay in `via`), or the equipment
                                                            whose drive it switches (e.g. a pump motor, loop E011 EU)
```
Not stored: nozzles, pipe segments, tees, reducers, flow arrows, actuating systems and functions, single instrument
functions (YS+, GO+-), alarms (they are on their loop) and COMOS export artefacts.

Files:
- `plant_loader.py` — reading DEXPI (`parse_sheet`) and the command line (load / remove / rebuild / summary / report / reset)
- `plant_store.py` — the plant: source files, snapshots, review decisions, the conceptual writer for Neo4j, rebuild
- `plant_model.py` — the complete graph in memory (networkx), snapshot format, joining sheets
- `proteus_topology.py`, `dexpi_links.py` — fill the gaps pyDEXPI leaves (missing items and connections), from the XML
- `graph_levels.py`, `graph_levels.yaml` — the Conceptual / Process / Complete levels
- `crosscheck/` — the PDF drawing check
- `app.py`, `graph_view.py`, `ui_style.py`, `.streamlit/config.toml` — the web app, its graph view and its look
- `CHANGES.md` — change log and working notes (COMOS exports, graph completion, UI, things to watch for)
- `queries_conceptual.cypher` — Neo4j Browser queries on the conceptual level (counts by label / type, the graph,
  unconnected equipment, loops); `queries_plant.cypher` — demo questions on the plant
- `test_sheets/` — 3 test sheets (`sheet_A.xml` = DEXPI sample, B and C made by `make_test_sheets.py`) + `manifest.csv`
- `pid_to_graph_OLD.py`, `queries.cypher`, `check_graph.py` — the original single-sheet PoC.
  **Do not run `pid_to_graph_OLD.py` against the plant database: it wipes all `:Asset` nodes.**

Folders the app writes (next to `app.py`):
- `sources/sheets.json` — every loaded sheet (plant, area, system, revision) and its source files;
  `sources/<sheet>/` — the DEXPI file and PDF as loaded;
  `sources/decisions.json` — approved / rejected drawing candidates, keyed by the two assets' uids;
- `snapshots/<sheet>.json` — the complete graph of each sheet

Sample input: `C01V04-VER.EX01.xml` — DEXPI reference P&ID (© DEXPI e.V., shipped with pyDEXPI).

Categories: every asset has a `category` (Equipment, Instrumentation, Piping) and a `type` (the DEXPI / COMOS class,
e.g. `GlobeValve`). **Valves:** DEXPI files every valve under piping, but control valves, on/off valves with an
actuator and safety / relief valves are maintained, tagged plant assets, so the loader files them as **Equipment**;
manual valves (hand, check) stay Piping. The property `valveRole` (`actuated`, `safety`, `manual`) keeps the
distinction. Which items are *main equipment* at the conceptual level is set in `graph_levels.yaml`, not by category.

**COMOS exports** (e.g. `=P002.A13.FB.001 (A4S1A2Y1M7)`) are DEXPI XML too; COMOS names the file after the
drawing object, so it has no `.xml` extension. They load as they are — files are recognised by content, not by
name, and the default sheet name drops the COMOS ID in brackets. COMOS keeps tags in its own properties, so the
loader reads them from there: the full COMOS tag (e.g. `=P002.A13.A11SE001`) is the asset's identity, so the same
object on several sheets becomes one asset, and the short tag (`SE001`, or `Y001.YS` for an instrument function)
is its name. When two items on a sheet share a short tag (e.g. both pump motors are `M001`), the longer form is
used for both.

**Completing the graph.** pyDEXPI leaves out items it has no DEXPI class for, and the links hanging off them; on a
COMOS sheet that left about a third of the items unconnected. After pyDEXPI, `proteus_topology.py` and
`dexpi_links.py` go over the XML again and add what is missing:
- items that are in the file but not in the graph (shut-off and control valves, tanks, instrument functions, ...);
- pipes from the drawing itself: each pipe centre line joins the items whose connection points are drawn at its two
  ends, in the direction it is drawn (this also fixes COMOS segments whose start / end references are wrong);
- drives drawn on a piece of equipment (a pump's motor, an agitator's motor) as parts of it;
- signal and measuring lines, and actuator → valve links, where pyDEXPI left an end unconnected;
- links in DEXPI associations, loops, information flows and piping segments (`dexpi_links.py`).

Links are only added between items that are not already connected. **Items drawn against each other with no pipe
between** (a reducer drawn straight onto a pump) are joined too (`inferred: "drawn touching"`); the sheet's PDF, if you
add it, only confirms these links. What still can't be connected goes to the validation report.


## Setup (Windows)
1. Install Python 3.12, then in the `pid_kg` folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. Neo4j connection: copy `neo4j_demo.env.example` to `neo4j_demo.env` and fill in the URI, user and password
   (e.g. a free Neo4j Aura instance, `neo4j+s://<id>.databases.neo4j.io`). The start script reads this file, so the
   app connects without a login. Without it the app shows a login panel in the sidebar.
   `sources/` and `snapshots/` go next to `app.py`; set `$env:PIDKG_DATA_DIR` to keep them elsewhere.

## Using the web interface
Double-click **`Start P&ID Knowledge Graph.bat`** (or run `streamlit run app.py`). The browser opens
http://localhost:8501 on the graph of the whole plant: **Conceptual** level, **Flow (left → right)** layout, in
**simple view** (every item a dot, every link a plain line). Keep the console window open while you use the app.

- **View options** (dropdown) — *Show* (whole plant, around an asset, downstream of an asset, one sheet), *Detail
  level* (Conceptual, Process, Complete) and *Layout* (Flow, Free). Nothing changes until you change it here.
- **Add / load DEXPI / PDF** — drag in DEXPI files (`.xml` or COMOS exports), optionally the matching vector PDFs
  (see *PDF drawing check*), check the sheet names and pairing in the table, then **Add to knowledge graph**. Loading
  a sheet name that is already loaded replaces that sheet. *Loaded sheets* in the same dialog removes a sheet.
- **In the graph** — hover or click an item for its inspector, search with `/`, `S` switches between simple and
  detailed view, the toolbar fits, zooms, toggles the flow animation, saves a PNG or goes fullscreen. At the Conceptual
  level, clicking a `FLOWS_TO` link shows its `via` and offers **Show detail** (the Process view of that path).
- **Disconnect** (top right) returns to the login panel. To stop the app, close the console window (or Ctrl+C).

Process and Complete levels are computed with `graph_levels.py` from the sheets' file snapshots; Conceptual reads
Neo4j. Everything shown for an item is also available as an adjacency export:
`plant_store.PlantStore(db).export()` (complete graph of all sheets, format `pid-kg-adjacency`, directed, with
`source`, `status` and `pdfCheck` on every relationship).

## Command line
`plant_loader.py` loads and manages sheets without the web interface:
```powershell
python plant_loader.py load my_sheet.xml --sheet 101 [--pdf my_sheet.pdf]
python plant_loader.py load pids_folder/                  # every DEXPI file in a folder
python plant_loader.py remove --sheet 101                 # one sheet: Neo4j, snapshot and saved sources
python plant_loader.py rebuild                            # reset Neo4j, load every sheet again from sources/
python plant_loader.py summary                            # sheets, unconnected equipment, counts by label / type
python plant_loader.py report                             # output/validation_report.csv
python plant_loader.py reset --yes                        # delete everything (Neo4j, snapshots, sources)
```
Rebuild after changing `graph_levels.yaml` or the loader. Off-page arrows are linked automatically when the outgoing
and incoming arrows carry the same line number.

## PDF drawing check (the COMOS vector PDF of a sheet)
DEXPI stays the backbone. The vector PDF that COMOS exports for the same sheet only audits the pipe links. Add it in
the *Add / load* dialog under *Matching PDF*; it is paired with a DEXPI file when its name contains the sheet's
drawing number or label. It is optional. The sheet's pipe links are then checked against the pipes drawn in the PDF:
**confirmed** (in DEXPI and drawn), **drawn only** (a candidate link, kept in the snapshot and not in Neo4j) and
**not drawn** (in DEXPI, not drawn). The PDF must be this sheet: at least 50% of the tags in its text layer have to
sit where the DEXPI file puts them. From the command line:
```powershell
python -m crosscheck.pdf_vs_dexpi p1_h4.pdf "=P1.H4.FB.001 (A3BQHLXCAR)" overlay.png
```

## Not included any more
The earlier PDF / image route (symbol detection with an RF-DETR model, OCR, Azure line detection, Roboflow cloud
model) and the web tabs *Plant*, *Ask* and *Validation*, the manual `links.csv` and *Rebuild graph* button were
removed; the CLI above still rebuilds and reports. They are in the Git history before the commit "Simplify start-up".

## Licences
- **pyDEXPI** — **AGPL-3.0**. Free to use for this PoC / internal demos, but a **commercial licence from
  TU Delft** is needed before using it in a closed-source product or a hosted service.
