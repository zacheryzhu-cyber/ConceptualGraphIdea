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
  `sources/<sheet>/` — the DEXPI file and PDF as loaded (`reviewed.json` for a sheet from the PDF / image route);
  `sources/decisions.json` — approved / rejected drawing candidates, keyed by the two assets' uids;
  `sources/links.csv` — manual off-page links
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

## Setup (Windows PowerShell)
```powershell
cd pid_kg
.\.venv\Scripts\Activate.ps1      # first time: python -m venv .venv; pip install -r requirements.txt
$env:NEO4J_URI="bolt://localhost:7687"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="..."
```
The password is only set in the terminal session, never written to a file.
`sources/` and `snapshots/` go next to `app.py`; set `$env:PIDKG_DATA_DIR` to keep them elsewhere (e.g. a separate
folder for a test database, so its sheets and review decisions don't mix with the real ones).

## Load a new sheet
Single file — give it a sheet name, area, system and revision, and optionally its vector PDF:
```powershell
python plant_loader.py load my_sheet.xml --sheet 101 --area "Area 1" --system "Cooling Water" --revision A --pdf my_sheet.pdf
```
A folder of sheets — every DEXPI file in the folder (`.xml` files and COMOS exports), with the details per file in a
`manifest.csv` (columns `file,sheet,revision,area,system`, optional `pdf`):
```powershell
python plant_loader.py load pids_folder/ --manifest pids_folder/manifest.csv
```
Without `--sheet` or a manifest, the sheet name is the file name and area/system are "Unassigned".

## Reload, remove, rebuild
- **Reload:** loading a sheet name that is already loaded replaces only that sheet (e.g. a new revision).
  The rest of the plant is untouched, and assets shared with other sheets stay.
- **Remove:** `python plant_loader.py remove --sheet 101` removes the sheet's Neo4j nodes and links, its snapshot and
  its saved source files. Assets also on other sheets stay.
- **Rebuild:** `python plant_loader.py rebuild` (or tab 3 · *Rebuild graph*) resets Neo4j and loads every sheet again
  from `sources/`, with its PDF. Approved and rejected drawing candidates are kept. Sheets that were loaded before
  `sources/` existed (full graph in Neo4j) are picked up from `uploads/` first, with their review decisions; the old
  manual edit records are saved to `sources/manual_edits_backup.json` before the reset. Rebuild after changing
  `graph_levels.yaml` or the loader.
- **Overview:** `python plant_loader.py summary` (sheets, unconnected equipment, node and relationship counts by
  label / type)
- **Delete everything:** `python plant_loader.py reset --yes` (Neo4j, snapshots and sources; add `--all` to delete
  the review decisions too)

## links.csv (manual off-page links)
Off-page arrows are linked automatically when the outgoing and incoming arrows carry the same line number.
For arrows that can't be matched that way, put a `links.csv` next to the loader (or pass `--links path`); it is kept
as `sources/links.csv`:
```
from_sheet,from_id,to_sheet,to_id
102,FlowOutPipeOffPageConnector-1,301,FlowInPipeOffPageConnector-1
```
IDs are the DEXPI IDs of the arrows.

## Detail levels and snapshots
Tab **4 · Graph** has a *Detail level*: **Conceptual** reads Neo4j; **Process** and **Complete** are computed with
`graph_levels.py` from the snapshots of the sheets in view (labelled *from file snapshot*). At the Conceptual level,
clicking a `FLOWS_TO` link shows its `via` and offers **Show detail**, which opens the Process view of just that path
(its two ends and the valves it passes) under the graph. *Show loops* draws the Loop and Valve nodes with their
MEASURES / CONTROLS links. *Show drawing candidates* adds the unreviewed candidates (dashed orange); they are not in
Neo4j, so that view is computed from the snapshots. Editing on the graph is switched off for now (the complete graph
is no longer in Neo4j).

The tab opens on **Whole plant**, **Conceptual**, **Flow (left → right)**.

**Same view in Neo4j Browser:** the database button in the graph's toolbar copies a Neo4j query that shows what the
graph shows. The query is also in *Neo4j query for this view* under the graph. It is built by `view_query.py`:
- **Conceptual (from Neo4j):** a live query on the stored conceptual graph for the chosen mode (whole plant, one
  sheet, around an asset with its steps out, downstream). With *Show loops* it includes `Loop` / `Valve` and
  `MEASURES` / `CONTROLS`.
- **Process / Complete, or anything from the snapshots:** these aren't in Neo4j, so the query carries the view's items
  and links and draws them as APOC virtual nodes and relationships. Nothing is written, but it needs the APOC plugin
  (`RETURN apoc.version();`).

Turn off *Connect result nodes* in Neo4j Browser settings, so Browser doesn't add other stored links.

## Exporting the graph as an adjacency list (JSON)
Tab **4 · Graph** has a **Download adjacency list (JSON)** button under the graph. It exports exactly what the view
shows, at the chosen level, with `level` and `origin` (Neo4j or file snapshot) in the header:
```json
{ "format": "pid-kg-adjacency", "version": 1, "exported": "...", "plant": ["Demo Plant"],
  "view": {"mode": "One sheet", "sheet": "101", "level": "Complete", "origin": "from file snapshot"},
  "directed": true, "counts": {"nodes": 36, "relationships": 39},
  "nodes":     { "TAG:P4711": {"name": "P4711", "type": "CentrifugalPump", "category": "Equipment", "sheets": ["101"], ...} },
  "adjacency": { "TAG:P4711": [ {"to": "TAG:H1007", "toName": "H1007", "type": "PIPE",
                                 "properties": {"sheet": "101", "lineNumber": "47122", ...}} ] } }
```
Every exported asset has an `adjacency` entry (`[]` if nothing leaves it). Relationships are directed (PIPE follows the
flow). Every relationship carries `source`, `status` and `pdfCheck` (null when not set; see *PDF drawing check*). The
snapshot files have the same format, with the sheet's details under `sheet`. In Python:
`plant_store.PlantStore(db).export()` (complete graph of all sheets, from the snapshots).

## PDF drawing check (the COMOS vector PDF of a sheet)
DEXPI stays the backbone. The vector PDF that COMOS exports for the same sheet only audits the pipe links: in tab
**1 · Load DEXPI**, add it under *Matching PDF (same sheet)*. It is paired with a DEXPI file when its name contains the
sheet's drawing number or label (pick it in the table otherwise). The PDF is optional: without it the sheet loads
normally and the Load tab says **Loaded without drawing check**, with the number of equipment items that have no
connection, so you can decide whether to add the PDF. While the sheet is loaded, its pipe links are checked against
the pipes drawn in the PDF:
- **confirmed**: in DEXPI and drawn; the PIPE gets `pdfCheck: 'confirmed'`.
- **drawn only**: drawn but on no DEXPI pipe segment. A pair the loader already links (e.g. items drawn against each
  other) is only marked `pdfCheck: 'confirmed'` (*already linked by the loader*). Otherwise a
  `PIPE {source: 'drawing', status: 'candidate'}` in the snapshot, to approve or reject in tab 6; approved, it reaches
  Neo4j as `FLOWS_TO {source: 'drawing'}`.
- **not drawn**: in DEXPI, not drawn; the PIPE gets `pdfCheck: 'not_drawn'`.

First the PDF must be this sheet: tags in its text layer have to sit where the DEXPI file puts them (at least 50%),
otherwise nothing is checked and the sheet shows *PDF does not match*. The pipe line width is found per file from the
DEXPI pipe routes. Tab **6 · Validation**, *Drawing check*, shows the scores, the candidates (Approve / Reject), the
links not drawn and an overlay picture. Decisions are kept in `sources/decisions.json` and survive reloads and
rebuilds. From the command line:
```powershell
python -m crosscheck.pdf_vs_dexpi p1_h4.pdf "=P1.H4.FB.001 (A3BQHLXCAR)" overlay.png
```

## Validation report
```powershell
python plant_loader.py report
```
Writes `output/validation_report.csv` from the snapshots: load issues (e.g. equipment without tag), off-page arrows
with no matching sheet, and tags that appear on several sheets (merged into one asset — confirm this is intended).

## Using the web interface
1. Double-click **`Start P&ID Knowledge Graph.bat`** in the `pid_kg` folder (or run `streamlit run app.py`).
   A console window opens (keep it open while you use the app) and the browser opens http://localhost:8501.
2. Type the Neo4j password in the left sidebar. Once connected the sidebar closes; **Disconnect** (top right)
   brings it back. The password is kept in memory only.
3. Tabs:
   - **1 · Load DEXPI** — drag in one or more DEXPI files (`.xml` or COMOS exports), optionally the matching vector
     PDFs (see *PDF drawing check*), fill in sheet, revision, area, system and PDF in the table, optionally add a
     `links.csv`, then click **Add to knowledge graph**. The result names sheets loaded without drawing check and
     their unconnected equipment.
   - **2 · Load PDF / image** — extract a P&ID from a PDF/PNG/JPG and review it before adding (see below).
   - **3 · Plant** — sheet list with counts (assets = the conceptual nodes Neo4j holds per sheet); remove a sheet,
     **Rebuild graph**, or delete the whole plant.
   - **4 · Graph** — draw the graph around an asset, downstream of an asset, one sheet, or the whole plant, at a
     detail level (see *Detail levels and snapshots*). Flow = left-to-right layout, Free = physics layout. Hover a
     node or line for details, click a node to open its inspector, search with `/`, click legend items to show /
     hide them, and use the toolbar to fit, zoom, toggle the flow animation, save a PNG or go fullscreen. **Simple
     view** (toolbar button with three linked dots, or `S`) draws every item as a dot in its category colour.
   - **5 · Ask** — ready-made questions: downstream equipment, impact of a closing control valve (both at the
     Process level, from the snapshots), which sheets show an asset, sheet connections, isolated sheets, control
     loops, equipment list (from Neo4j).
   - **6 · Validation** — the same report as `plant_loader.py report`, with a CSV download; islands of the complete
     graph; the drawing check and its candidates to review; types `graph_levels.yaml` does not know.
4. To stop the app, close the console window (or press Ctrl+C in it).

## PDF / image route
For P&IDs that only exist as a PDF or image. Everything runs locally; nothing is sent to a cloud service.

1. **Symbols** — the ready-trained RF-DETR P&ID model (`dimtri009/rfdetr-pid-detector`, file
   `models/rfdetr_pid.pth`) finds valves, instrument bubbles, flanges, reducers, blinds and flow arrows.
   It has no equipment classes.
2. **Text and tags** — the PDF's own text layer when there is one, otherwise local OCR (RapidOCR, offline).
3. **Equipment** — found from equipment tags (e.g. `P4711`) plus the outline drawn around or next to the tag.
4. **Connections** — Microsoft's Azure P&ID sample code (line detection + graph construction), run locally
   with no Azure services (`pdf_pipeline/azure_pid`, fetched once with `python -m pdf_pipeline.vendor_azure`).
5. **Review** — in the web interface, tab **2 · Load PDF / image**: upload, set the sheet name, click
   **Extract**, check the picture and the two tables (items, connections), untick what is wrong, then click
   **Add reviewed sheet to knowledge graph**. Nothing goes into the graph before this step.
   Tagged items (e.g. `P4711`) merge with the same tag already loaded from other sheets (DEXPI or PDF).

Command line: `python -m pdf_pipeline.extract drawing.pdf --sheet 301 --weights models/rfdetr_pid.pth`

**Optional Roboflow cloud model (uploads the drawing).** Tick *Also use a Roboflow cloud model* in tab 2 and
enter a Universe model id (default `p-id-symbols/1` = universe.roboflow.com/pid-connect/p-id-symbols) and your
Roboflow API key (kept in memory only). The drawing is sent to Roboflow in tiles (`pdf_pipeline/roboflow_cloud.py`).
Class names are mapped by keyword to valve / instrument / equipment / fitting. Cloud equipment gives tagged
equipment its type (e.g. P4711 → Centrifugal Pump) and adds untagged equipment for review; cloud valves and
instruments are only added where the local model found nothing. Command line: add
`--roboflow-model p-id-symbols/1` with `$env:ROBOFLOW_API_KEY="..."` set in the terminal.
Unticked, nothing leaves this computer.

**Measured on the DEXPI sample** (`python -m pdf_pipeline.test_on_sample --weights models/rfdetr_pid.pth [--ocr]`,
the sample drawn as a PDF and compared with the exact DEXPI data, CPU, ~40 s per run):

| Text from | Equipment found | Equipment-to-equipment links | Wrong links |
|---|---|---|---|
| PDF text layer | 5/5 | 4/5 (missed P4711–H1007) | 0 |
| OCR (simulated scan) | 5/5 | 4/5 (missed P4711–H1007) | 0 |

P4711–H1007 is always missed: that pipe crosses another pipe just before the exchanger, and line detection
can't tell a crossing from a junction. The RF-DETR model finds most symbols but often names the wrong valve
type (e.g. ball valves as globe valves); this does not affect the connections, but check types in the review.

**Training an equipment model on your own drawings (Roboflow).** Public Universe P&ID models were tested on the
DEXPI sample and found almost nothing, so equipment is trained on our own sheets in the private Roboflow project
`pid-equipment` (https://app.roboflow.com/zacherys-workspace-bhp5q/pid-equipment):
1. Put drawings (PDF/PNG/JPG) in `training/drawings/`.
2. `python -m pdf_pipeline.make_training_set --valid <one or two file names>` cuts them into the same tiles the cloud
   route sends and writes DRAFT equipment labels from tags (`training/dataset.zip`; check `training/preview/`).
   Leave out wrong drafts with `--skip drawing.pdf:TAG` (e.g. valves that carry equipment-like tags).
   Classes come from the tag letter code (`TAG_CLASSES`): pump, tank, vessel, heat-exchanger, column, compressor,
   reactor, equipment.
3. Upload the zip (Roboflow → Upload, or via the Roboflow connector), then check and fix every box in Roboflow's
   annotation editor — the drafts are a starting point, not ground truth.
4. Generate a version and train in Roboflow; put the new model id (e.g. `pid-equipment/1`) in the app's Roboflow
   model field.

**Tag convention:** the equipment tag pattern is `EQUIPMENT_TAG` in `pdf_pipeline/equipment.py`
(default `^[A-Z]{1,3}-?\d{2,5}[A-Z]?$`, e.g. P4711, T-4750, HX101A). Set it to the customer's tag convention.
Tags printed in table cells (equipment data tables, title blocks) are skipped, and the largest equipment
outline accepted is `MAX_SIZE` = 30 letter heights (big tanks drawn with small text need ~25).

**OpenCV:** the Azure code needs OpenCV 4.x with contrib (`cv2.ximgproc.thinning`). If another package pulls
in OpenCV 5, reinstall the two pinned lines from `requirements.txt` in the order given there.

## Licences
- **pyDEXPI** — **AGPL-3.0**. Free to use for this PoC / internal demos, but a **commercial licence from
  TU Delft** is needed before using it in a closed-source product or a hosted service.
- **Azure P&ID sample code** (`pdf_pipeline/azure_pid`) — MIT (see `pdf_pipeline/azure_pid/LICENSE.md`).
- **RF-DETR code** (`rfdetr` package) — Apache-2.0.
- **RF-DETR P&ID model weights** — per their model card (listed as Apache-2.0 at the time of download);
  check https://huggingface.co/dimtri009/rfdetr-pid-detector before any use beyond this PoC.
