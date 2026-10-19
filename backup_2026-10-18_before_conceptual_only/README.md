# P&ID → Plant Knowledge Graph (pyDEXPI + Neo4j)

## What it does
Reads DEXPI (Proteus XML) P&ID sheets with the open-source pyDEXPI library and loads them into **one plant
knowledge graph** in Neo4j: equipment, valves/piping items and instruments, connected by pipes and signal lines.
Every sheet you load is **added** to the plant; sheets are linked to each other through matching off-page
arrows (same line number) and through shared tags (the same equipment/instrument tag on several sheets is one node).

Graph model:
```
(Plant)-[:HAS_AREA]->(Area)-[:HAS_SYSTEM]->(System)-[:HAS_SHEET]->(Sheet)-[:DEPICTS]->(Asset)
(Asset)-[:PIPE | DIRECT_PIPING_CONNECTION | MEASURING_LINE_FUNCTION | ...]->(Asset)    per sheet
(off-page out arrow)-[:CONTINUES_TO]->(off-page in arrow)                             across sheets
```

Categories: every asset has a `category` (Equipment, Instrumentation, Piping, or one you add on the graph) and a
`type` (the DEXPI / COMOS class, e.g. `GlobeValve`); both are also Neo4j labels. **Valves:** DEXPI files every valve
under piping, but control valves, on/off valves with an actuator and safety / relief valves are maintained, tagged
plant assets, so the loader files them as **Equipment**; manual valves (hand, check) stay Piping. The property
`valveRole` (`actuated`, `safety`, `manual`) keeps the distinction, e.g.
`MATCH (v:Equipment {valveRole: 'actuated'}) RETURN v.name`. A hand edit of the category always wins. Valves already in the database get the same rule when the app connects, with no reload needed.

Files:
- `plant_loader.py` — the loader (load / remove / summary / report / reset)
- `proteus_topology.py` — fills the gaps pyDEXPI leaves (missing items and connections), straight from the XML
- `app.py`, `graph_view.py`, `ui_style.py`, `.streamlit/config.toml` — the web app, its graph view and its look
- `CHANGES.md` — change log and working notes (COMOS exports, graph completion, UI, things to watch for)
- `queries_plant.cypher` — demo queries for the multi-sheet graph (run in Neo4j Browser)
- `queries_conceptual.cypher` — the app's Conceptual view as Neo4j Browser queries (main equipment and off-page
  connectors joined by the pipe runs between them; needs APOC for the graph, a table version works without);
  regenerate with `python make_conceptual_queries.py` after changing `graph_levels.yaml`
- `test_sheets/` — 3 test sheets (`sheet_A.xml` = DEXPI sample, B and C made by `make_test_sheets.py`) + `manifest.csv`
- `pid_to_graph_OLD.py`, `queries.cypher`, `check_graph.py` — the original single-sheet PoC.
  **Do not run `pid_to_graph_OLD.py` against the plant database: it wipes all `:Asset` nodes.**

Sample input: `C01V04-VER.EX01.xml` — DEXPI reference P&ID (© DEXPI e.V., shipped with pyDEXPI).

**COMOS exports** (e.g. `=P002.A13.FB.001 (A4S1A2Y1M7)`) are DEXPI XML too; COMOS names the file after the
drawing object, so it has no `.xml` extension. They load as they are — files are recognised by content, not by
name, and the default sheet name drops the COMOS ID in brackets. COMOS keeps tags in its own properties, so the
loader reads them from there: the full COMOS tag (e.g. `=P002.A13.A11SE001`) is the asset's identity, so the same
object on several sheets becomes one asset, and the short tag (`SE001`, or `Y001.YS` for an instrument function)
is its name. When two items on a sheet share a short tag (e.g. both pump motors are `M001`), the longer form is
used for both.

**Completing the graph.** pyDEXPI leaves out items it has no DEXPI class for, and the links hanging off them; on a
COMOS sheet that left about a third of the items unconnected. After pyDEXPI, `proteus_topology.py` goes over the XML
again and adds what is missing:
- items that are in the file but not in the graph (shut-off and control valves, tanks, instrument functions, ...);
- pipes from the drawing itself: each pipe centre line joins the items whose connection points are drawn at its two
  ends, in the direction it is drawn (this also fixes COMOS segments whose start / end references are wrong);
- drives drawn on a piece of equipment (a pump's motor, an agitator's motor) as parts of it;
- signal and measuring lines, and actuator → valve links, where pyDEXPI left an end unconnected.

Links are only added between items that are not already connected, so a sheet pyDEXPI reads fully (like the DEXPI
sample) comes out the same. What still can't be connected goes to the validation report: pipe ends with nothing
drawn there (often a line leaving the sheet without an off-page arrow; add the arrow in the source or a
`links.csv` row) and instrument functions nothing on the drawing connects to.

## Setup (Windows PowerShell)
```powershell
cd pid_kg
.\.venv\Scripts\Activate.ps1      # first time: python -m venv .venv; pip install -r requirements.txt
$env:NEO4J_URI="bolt://localhost:7687"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="..."
```
The password is only set in the terminal session, never written to a file.

## Load a new sheet
Single file — give it a sheet name, area, system and revision:
```powershell
python plant_loader.py load my_sheet.xml --sheet 101 --area "Area 1" --system "Cooling Water" --revision A
```
A folder of sheets — every DEXPI file in the folder (`.xml` files and COMOS exports), with the details per file in a `manifest.csv`
(columns `file,sheet,revision,area,system`):
```powershell
python plant_loader.py load pids_folder/ --manifest pids_folder/manifest.csv
```
Without `--sheet` or a manifest, the sheet name is the file name and area/system are "Unassigned".

## Reload and remove
- **Reload:** loading a sheet name that is already in the graph replaces only that sheet (e.g. a new revision).
  The rest of the plant is untouched, and assets shared with other sheets stay.
- **Remove:** `python plant_loader.py remove --sheet 101` removes the sheet and the assets that appear only on it.
- **Overview:** `python plant_loader.py summary`
- **Delete everything:** `python plant_loader.py reset --yes` (manual edits too; add `--keep-edits` to keep them for re-applying after a reload)

## links.csv (manual off-page links)
Off-page arrows are linked automatically when the outgoing and incoming arrows carry the same line number.
For arrows that can't be matched that way, put a `links.csv` next to the loader (or pass `--links path`):
```
from_sheet,from_id,to_sheet,to_id
102,FlowOutPipeOffPageConnector-1,301,FlowInPipeOffPageConnector-1
```
IDs are the DEXPI IDs of the arrows. The links are rebuilt on every load/remove.

## Editing on the graph (web interface, tab 4 · Graph)
Click an item on the graph to open its inspector, then:
- **Edit** (pencil) — change its name, category, type or description. Category and type are free text with the
  existing ones suggested: type a new one (e.g. category `Electrical`, type `Motor`) and it is created; a new category
  gets its own colour and legend entry.
- Hover one of its connections — **reverse** its direction or **delete** it (click the bin twice).
- **Connect to another item** — pick the direction and relationship type (PIPE by default) in the bar at the top,
  then click the other item on the graph (or pick it in the search box). `Esc` cancels.

Each change is saved straight away as a manual edit (below): it survives reloading the sheet, shows in the edit
log in tab 7 and can be undone there. The graph keeps its zoom and selection after a save, and a short message
confirms what was saved. The small graph in tab 7 can be edited the same way.

## Exporting the graph as an adjacency list (JSON)
Tab **4 · Graph** has a **Download adjacency list (JSON)** button under the graph. It exports exactly what the view
shows (around an asset, downstream, one sheet, or the whole plant), for validation in other tools:
```json
{ "format": "pid-kg-adjacency", "version": 1, "exported": "...", "plant": ["Demo Plant"],
  "view": {"mode": "One sheet", "sheet": "101"}, "directed": true,
  "counts": {"nodes": 36, "relationships": 39},
  "nodes":     { "TAG:P4711": {"name": "P4711", "type": "CentrifugalPump", "category": "Equipment", "labels": [...], "sheets": ["101"], ...} },
  "adjacency": { "TAG:P4711": [ {"to": "TAG:H1007", "toName": "H1007", "type": "PIPE",
                                 "properties": {"sheet": "101", "lineNumber": "47122", ...}} ] } }
```
Every exported asset has an `adjacency` entry (`[]` if nothing leaves it). Relationships are directed (PIPE follows the
flow) and only those between exported assets are included; hand edits carry `properties.source = "manual"`. Every
relationship carries `source`, `status` and `pdfCheck` (null when not set; see *PDF drawing check*). Unreviewed
drawing candidates are left out unless *Show drawing candidates* is ticked.
In Python: `plant_loader.export_adjacency(db)` (whole plant) or `export_adjacency(db, uids)`
(`candidates=True` to include unreviewed candidates).

## PDF drawing check (the COMOS vector PDF of a sheet)
DEXPI stays the backbone. The vector PDF that COMOS exports for the same sheet only audits the pipe links: in tab
**1 · Load DEXPI**, add it under *Matching PDF (same sheet)*. It is paired with a DEXPI file when its name contains the
sheet's drawing number or label (pick it in the table otherwise). After loading, the sheet's pipe links are checked
against the pipes drawn in the PDF:
- **confirmed**: in DEXPI and drawn; the PIPE gets `pdfCheck: 'confirmed'`.
- **drawn only**: drawn but on no DEXPI pipe segment. A new `PIPE {source: 'drawing', status: 'candidate'}` (dashed
  orange in tab 4 with *Show drawing candidates*), to approve or reject in tab 6. A pair the loader already links (from
  the drawing geometry) is only marked confirmed.
- **not drawn**: in DEXPI, not drawn; the PIPE gets `pdfCheck: 'not_drawn'`.

First the PDF must be this sheet: tags in its text layer have to sit where the DEXPI file puts them (at least 50%),
otherwise nothing is checked and the sheet shows *PDF does not match*. The pipe line width is found per file from the
DEXPI pipe routes. Tab **6 · Validation**, *Drawing check*, shows the scores, the candidates (Approve / Reject), the
links not drawn and an overlay picture. Approvals and rejections are kept when the sheet is reloaded. From the command
line:
```powershell
python -m crosscheck.pdf_vs_dexpi p1_h4.pdf "=P1.H4.FB.001 (A3BQHLXCAR)" overlay.png
```

## Editing relationships by hand (web interface, tab 7 · Edit)
For connections the P&IDs don't show, or show wrongly:
- **Add** — pick From, type (an existing one such as PIPE, or a new one) and To; optional line number and note.
- **Change / delete** — pick an asset, then one of its relationships: change its type, reverse its direction, set
  its line number, or delete it. The asset's neighbourhood is drawn underneath; hand edits are green.
- **Edit log** — every edit with time, note and status, and **Undo**.

How it works: each edit is stored in Neo4j as a `(:ManualEdit)` record (action `add` / `remove` for a
relationship, `set` for an asset's name, category, type or description) and re-applied after every load
(`apply_manual_edits`, called from `link_sheets`), so edits survive reloading a sheet. An added relationship carries
`source: 'manual'`. Changing or deleting a relationship that came from a sheet *hides* the original (Undo brings it
back). An edit whose asset is not loaded waits, is listed in the validation report, and applies when the sheet is
loaded again. Deleting the whole plant graph (tab 3) deletes the edit records too, unless you untick *Also delete my
manual edits*; kept edits are re-applied when the sheets are loaded again.

## Validation report
```powershell
python plant_loader.py report
```
Writes `output/validation_report.csv`: load issues (e.g. equipment without tag), off-page arrows with no
matching sheet, and tags that appear on several sheets (merged into one asset — confirm this is intended).

## Using the web interface
1. Double-click **`Start P&ID Knowledge Graph.bat`** in the `pid_kg` folder (or run `streamlit run app.py`).
   A console window opens (keep it open while you use the app) and the browser opens http://localhost:8501.
2. Type the Neo4j password in the left sidebar. Once connected the sidebar closes; **Disconnect** (top right)
   brings it back. The password is kept in memory only.
3. Tabs:
   - **1 · Load DEXPI** — drag in one or more DEXPI files (`.xml` or COMOS exports), optionally the matching vector
     PDFs (see *PDF drawing check*), fill in sheet, revision, area, system and PDF in the table, optionally add a
     `links.csv`, then click **Add to knowledge graph**.
   - **2 · Load PDF / image** — extract a P&ID from a PDF/PNG/JPG and review it before adding (see below).
   - **3 · Plant** — sheet list with counts; remove a sheet or delete the whole plant graph.
   - **4 · Graph** — draw the graph around an asset, downstream of an asset, one sheet, or the whole plant
     (Flow = left-to-right layout, Free = physics layout). Hover a node or line for details, click a node to
     open its inspector (upstream / downstream items, click one to jump to it), search with `/`, click legend
     items to show / hide them, and use the toolbar to fit, zoom, toggle the flow animation, save a PNG or go
     fullscreen. **Simple view** (toolbar button with three linked dots, or `S`) draws every item as a dot in its
     category colour and every link as the same plain line, to see just what connects to what; press it again for
     the full symbols. Items and connections can be edited right on the graph (see *Editing on the graph* below).
     The look is set in `graph_view.py`, `ui_style.py` and `.streamlit/config.toml`.
   - **5 · Ask** — ready-made questions: downstream equipment, impact of a closing control valve,
     which sheets show an asset, sheet connections, isolated sheets, control loops, equipment list.
   - **6 · Validation** — the same report as `plant_loader.py report`, with a CSV download; islands; the PDF
     drawing check with its candidates to review.
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
