# Change log and working notes

What changed on 2026-10-15, why, and what was learned along the way. The README says how to use the tool;
this file explains how the newer parts work and the things that are easy to trip over.

---

## 1. New look for the app and the graph view

**Goal:** a dark, "control room" look for the whole app and a much richer graph view. Every query, tab, picker
and edit flow works as before.

| File | What it does |
|---|---|
| `graph_view.py` (new) | Draws the interactive graph. `render(nodes, edges, focus, layout, height, offpage_types)` returns one self-contained HTML page. Replaces the pyvis drawing that was in `app.py`. |
| `ui_style.py` (new) | Page CSS, header (`hero`), stat cards (`stat_cards`), sidebar logo (`sidebar_brand`). |
| `.streamlit/config.toml` (new) | Dark theme: colours, fonts (Inter / Space Grotesk / JetBrains Mono from Google Fonts, system fonts when offline), corner radius, sidebar colours. |
| `app.py` | Uses the three above. Only presentation lines changed, plus the login behaviour (section 2) and the upload box (section 3). |

**Graph view features:**
- **Look:** dark grid canvas with glowing nodes by category. Equipment shows as boxes, instruments as balloons, valves as diamonds, other piping items as dots and off-page arrows as triangles.
- **Line types:** pipes are blue, signal / measuring lines amber dashed, links to another sheet pink dashed, and links added by hand green.
- **Flow animation:** moving dots show the direction along pipes.
- **Focus asset:** the asset you start from has a pulsing ring.
- **Hover and inspect:** a hover card shows type, sheets and line number. Clicking a node opens an inspector with its upstream and downstream items (click one to jump to it) and fades everything not connected to it.
- **Controls:** search (`/`), fit (`F`), zoom, flow animation on/off, valve label on/off, save as PNG, fullscreen and `Esc` to clear. Clicking a legend item shows or hides it.
- **Simple view** (button with three linked dots, or `S`):
  - Every item is drawn as a dot in its category colour: larger for equipment, smallest for fittings.
  - Every link is the same plain line (`PLAIN` colour). Flow dots use that colour too.
  - The legend switches to dots and plain lines. Its link entries keep their names, so filtering still works.
  - The choice is kept in the browser (`localStorage` key `pidGraphSimple`).
  - It only changes the look (`nodeStyle` / `edgeStyle` check `simple`). Selection, filters and editing work the same.
- **Offline:** the vis-network library is read from the pyvis package (`pyvis/templates/lib/vis-9.1.2`), so the graph needs no internet. pyvis stays in `requirements.txt` for that reason.
- **Debugging:** `window.pidGraph` (network, goTo, clearSel, fit) is exposed in the browser console.

**Flow layout columns** (`graph_view.flow_levels`):
- Columns follow pipe links only (`PIPE`, `DIRECT_PIPING_CONNECTION`, `CONTINUES_TO`), counted from items with no incoming pipe, or from the focus asset.
- Instruments, actuators and motors are placed one column before what they act on and one column after what they read, repeated until chains line up.
- **Why:** earlier, columns followed every link, so signal-only items all became start points and piled up in the first columns.

**Things learned about Streamlit 1.65 (the installed version):**
- Tabs and radios use react-aria, not baseweb. Style them through `[role="tab"]`, `[aria-selected]`, `[data-testid="stRadioOption"][data-selected="true"]` and `.react-aria-SelectionIndicator`.
- `st.html` sanitises inline `<svg>`. Icons are therefore data-URI `<img>` tags (`ui_style._img`).
- `st.components.v1.html` is deprecated. The graph first moved to `st.iframe(html, height=...)`; it is now a custom component so it can send edits back (section 5). Both iframes allow fullscreen and downloads, which the PNG snapshot needs.
- `st.container(key="x")` gets the CSS class `st-key-x`, but its wrapper `stLayoutWrapper` still takes a row in the layout. Use `display: contents` on it when the container is absolutely positioned (the Disconnect button does this).

---

## 2. Login panel closes after connecting

- **While not connected:** the sidebar shows URI, user and password as before.
- **Once connected:** the connection details are kept in `st.session_state["conn"]` (memory only, never written to a file), the page reruns, and the sidebar is not drawn at all, so the page uses the full width.
- **Disconnect:** a **Disconnect** button in the header's top-right corner clears `conn` and brings the login panel back with an empty password field.
- **Automatic login:** `NEO4J_PASSWORD` set in the environment still connects automatically.

---

## 3. COMOS DEXPI exports load

COMOS 10.4 exports DEXPI 1.3 / Proteus 4.0.1 XML. Three things stopped them loading:

1. **No `.xml` extension.** COMOS names the file after the drawing object, e.g. `=P002.A13.FB.001 (A4S1A2Y1M7)`, and Windows treats `.001 (A4S1A2Y1M7)` as the extension.
   - The upload box used to accept `.xml` only. It now accepts any file and checks the content with `plant_loader.looks_like_dexpi` (looks for `<PlantModel`).
   - Command-line folder loading picks up these files too.
   - The default sheet name comes from `sheet_name_from_file`. It drops `.xml` and the COMOS ID in brackets, so the example becomes `=P002.A13.FB.001`.
2. **pyDEXPI's file loader adds `.xml`** to any file name that lacks it, so it couldn't find the file.
3. **Byte-order mark (BOM).** COMOS writes UTF-8 with a BOM. pyDEXPI opens files with Windows' default encoding (cp1252), so the BOM becomes junk and the XML parser fails at line 1. Non-English characters would also be garbled.

   **Fix for 2 and 3:** `plant_loader.read_xml_text` reads the bytes itself. It handles a BOM, UTF-16, or whatever encoding the XML header declares, and passes the text to `ProteusSerializer().load_from_string`.

**Tags.** COMOS keeps tags outside the DEXPI attributes, as a `TagName="..."` attribute plus a `ComosProperties` set (`Label`, `FullLabel`, `Description`). pyDEXPI reads neither, so every item came out untagged (`XMP_117` etc.). Now:
- **Identity:** the asset's identity is `TAG:<FullLabel>`, e.g. `TAG:=P002.A13.A11SE001`. The full label is unique across the plant, so the same COMOS object on several sheets merges into one asset.
- **Why not the short tag:** short tags repeat. Both pump motors are `M001`, and instrument functions are only their letters (`YS`, `UV`).
- **Display name:** `comos_name` gives the short tag, e.g. `SE001`. Instrument functions whose label is only letters get their loop in front, e.g. `Y001.YS`.
- **Repeated short tags:** when two items on a sheet share a short name, each gets where it sits, taken from the full label minus the prefix every tag on the sheet shares: `VE001 (A10)` / `VE001 (A12)` for the two tanks, `M001 (A11.N003.EU.A10)` for a motor. (COMOS writes area and tag with no separator, `=P002.A13.A10VE001`; the first version showed `A10VE001`.)
- **Extra properties:** the asset also gets `fullTag` and `description`.

---

## 4. Completing the graph (`proteus_topology.py`)

**Problem.** On the COMOS sheet, 30 of 85 items had no connection, including all 4 pumps. The links were in the XML; pyDEXPI's simplified graph loses them:
- **Missing items:** pyDEXPI leaves out items it has no DEXPI class for (shut-off and control valves, tanks, motors, some instrument functions), about 45 on that sheet.
- **Pumps:** COMOS pumps have their own `ConnectionPoints` instead of nozzles. Pipes are drawn to them, but the segment's `Connection` has an empty from/to ID.
- **Scrambled segments:** some segments list their contents out of order. One (`XMP_537`) records `FromID == ToID`, the same tee for both ends.
- **Instrument links:** signal lines go instrument function → actuator (`ActuatingSystemComponent`), and `OperatedValveReference` points at the valve. pyDEXPI keeps few of these.
- **Motors:** some are top-level equipment with no reference to their pump at all; they are only drawn touching it.

**Fix.** `parse_sheet` builds pyDEXPI's graph first, then calls `proteus_topology.complete(...)`. It works in this order:
1. **Missing items:** adds top-level equipment, piping components and instrument functions pyDEXPI didn't produce.
   - **Sub-equipment with its own tag** (`has_tag`) is added as part of its parent (`COMPOSITION`), e.g. agitator `AG001` inside tank `VE001`.
   - **Not added unless a link needs them:** structural parts (`STRUCTURAL_PARTS`: shell, chamber, tube bundle, jacket, ...), untagged internals (the DEXPI sample's impeller and displacer) and actuating systems.
2. **Pipes from the drawing:**
   - Each pipe centre line joins the items whose connection points are drawn at its two ends (within 1 mm, `SNAP_MM`), in the direction the line is drawn.
   - Loose line ends that meet join their lines into one run.
   - The segment's recorded from/to ID is only a fallback for a loose end, and is ignored when from and to are the same item.
   - Nozzle → its equipment; a connection point → the item that owns it.
3. **Drives:** equipment with no pipes whose drawn position lies on another piece of equipment becomes part of it (`COMPOSITION`, property `inferred: "drawn attached"`). The smallest item it is drawn on wins, sub-equipment included, so the pump motors go to their pumps and motor `M1` goes to agitator `AG001`, not to the tank.
4. **Actuator → valve:** `OPERATED_VALVE_REFERENCE` links are added only for valves that have no actuator link yet.
5. **Signal and measuring lines** (`InformationFlow`): added only where pyDEXPI left an end unconnected, or an end is an item this pass added. When COMOS leaves out the line's class, process side → instrument counts as a measuring line; anything else is a signal line.
6. **Validation report entries:** pipe ends with nothing drawn there, and instrument functions nothing connects to.

**Rules that keep it safe:**
- A pipe link is only added between two items not already joined by a pipe. Other links are only added between two items not already joined by any non-pipe link.
- pyDEXPI models instrument loops differently: sensor → function → **actuating function** → valve. That is why actuating systems are not added up front and why instrument links need an unconnected end. Without these rules, the DEXPI sample got a second, parallel set of loop links.
- Empty placeholder nodes pyDEXPI makes for open pipe ends are skipped. Pipe-line nodes (`Pipe`, `PipingNetworkSegment`, `PipingNetworkSystem`) left with no connection are removed, because the pipe links already show the line.
- When an item's tag repeats on a sheet, the first one is kept and only missing details are filled in. Earlier, the last one overwrote the first.

**Results** (`parse_sheet`, checked offline):

| Sheet | Before | After |
|---|---|---|
| COMOS `=P002.A13.FB.001` | 85 items, 47 links, 30 unconnected | 94 items, 100 links, 1 unconnected (`U001.US`, which has no link in the export) |
| DEXPI sample `C01V04` | 36 items, 39 links | 36 items, 39 links, plus 4 new "open pipe end" warnings |

The sample's 4 warnings are real: pipes run from tank nozzles to the top edge of the drawing with nothing attached. pyDEXPI never reported them.

**Still open on the COMOS sheet** (shown in the Validation tab):
- 4 pipes leave the right edge (x = 1135) without off-page arrows.
- Line `XMP_501` starts with nothing drawn at x = 70.
- `U001.US` is connected only to its own loop.
- The 9 valve actuators / 4 actuating functions have no COMOS tag, so they don't merge across sheets.

---

## 5. Editing on the graph view

Click an item, then **Edit** (name, category, type, description), hover a connection to **reverse** or
**delete** it, or **Connect to another item** (choose direction and type, then click the target or pick it in
search). Works in tab 4 and in the small graph in tab 7.

**How it works**
- **The graph as a component.** `graph_view.show(...)` mounts the graph page as a Streamlit custom component (v1
  `components.declare_component`, served from `%TEMP%/pid_kg_graph_component/index.html`, rewritten at start-up).
  The page speaks the component protocol itself (`streamlit:componentReady` / `render` / `setFrameHeight` /
  `setComponentValue`), so no npm build is needed. `graph_view.render(...)` still makes the read-only stand-alone page.
- **Events.** The page sends one event per change: `{"action": "set_asset" | "add_rel" | "reverse_rel" |
  "delete_rel", ..., "nonce": ...}`. Streamlit returns the last value on every rerun, so `app.draw` acts only on a
  nonce it hasn't seen (`st.session_state[f"{key}_nonce"]`). `app.apply_graph_edit` saves the change and the page
  reruns. The result comes back as a `flash` message shown once as a toast; a refused change (e.g. an invalid type)
  shows in red and leaves the graph as it was.
- **Keeping the view.** New data arrives with a `version` hash. The page rebuilds only when the version changes, and
  if the `context` (view key, layout, focus asset) is the same, it keeps zoom, position, the selected item and (in
  Free layout) node positions. vis-network fits a new network once more after its first draw, so the saved view is
  applied again on the next frames.
- **Saving.** Connections use the existing manual edits (`add_manual_edit`, `change_relationship`,
  `delete_relationship`).
- **Asset changes.** These are a new `ManualEdit` action, `set` (`plant_loader.set_asset_props`):
  - The record keeps the new values (`props`) and the values before (`original`).
  - `_set_asset` also swaps the category / type Neo4j labels, so `MATCH (a:Equipment)` stays right.
  - `apply_manual_edits` re-applies `set` edits after every load, after the relationship edits, in the order they
    were made.
  - Undo restores the values from before that edit, then re-applies the remaining edits, so undoing in any order
    ends up right.
  - Empty name / type and unknown categories are refused, and a save that changes nothing makes no record.
- **Tab 7 edit log.** It shows `set` edits as `name 'P1' → 'P1-FEED'`, etc.

**Checked:**
- **Component round trip:** checked in a scratch Streamlit app with in-memory data (rename, connect, reverse,
  delete, refused type, zoom kept).
- **`set` edit logic:** checked with a stand-in database (save, reload + re-apply, undo in either order, refused
  input).
- **Not yet tried:** the real Neo4j database.

**Deleting the plant.**
- **Before:** "Delete everything" (tab 3) and `plant_loader.py reset` used to keep `ManualEdit` records on purpose, so hand-added links came back when sheets were reloaded. That surprised people.
- **Now:** both delete the edits too (`plant_loader.delete_plant`). To keep them, untick *Also delete my manual edits* in the app, or pass `--keep-edits` on the command line.

**Things to know**
- Two graphs on one page need different `key`s (`graph_main` in tab 4, `graph_edit` in tab 7).
- A background browser tab slows vis-network's animations a lot; that matters only when testing.

## 6. Valves as equipment, and categories of your own

**Valves.** DEXPI / ISO 10628 file every valve as a piping component. For asset management, though, a control valve,
an on/off valve with an actuator, or a safety / relief valve is a tagged, maintained piece of equipment; a hand
valve is a line item.
- **The rule:** `plant_loader.valve_role(type, actuated)` decides it after the graph is built (in `parse_sheet`):
  - `safety`: the type mentions safety, relief or breather;
  - `actuated`: the valve has an `OPERATED_VALVE_REFERENCE` from an actuator, or its type says control / operated /
    motor / solenoid;
  - `manual`: any other valve.
- **What is stored:** safety and actuated valves get category Equipment. All valves get `valveRole`.
- **Results:** COMOS sheet 15 of 26 valves → Equipment; DEXPI sample 4 of 11 (3 actuated globe valves, 1 safety valve).
- **Graph:** these valves are large cyan diamonds ("Control / safety valve" in the legend); manual valves stay
  small white diamonds.
- **Ask tab:** "If this control valve closes, which equipment loses supply?" leaves valves out of the answer
  (`e.valveRole IS NULL`), since valves don't consume anything. "Which equipment is downstream?" still lists them.
- **Reloads:** `write_sheet` removes the old category label from an asset that is shared with another sheet when
  its category changes, so a re-classified valve doesn't keep `:Piping` as well as `:Equipment`.
- **Existing data:** `classify_valves(db)` runs when the app connects (and in the command-line loader). It gives valves already in Neo4j the same rule (only those without a `valveRole`, so it is a one-off), then re-applies manual edits. The first version only applied the rule when a sheet was loaded, so a database loaded earlier showed no change.
- **PDF-route types:** these are written like `Checkvalve` or `Rotaryvalve`, so valves are recognised whatever the case of "valve" (the graph used to miss these and drew them as plain piping dots).

**Categories of your own.**
- The Category box in the graph's edit form is free text. It suggests the categories already in the plant, plus
  the three defaults.
- `plant_loader.clean_category` accepts any name of up to 40 characters with a letter in it.
- A new category becomes a Neo4j label like any other.
- In the graph it gets a colour picked from its name, a hexagon shape (a diamond if the type is a valve) and its
  own legend entry.
- **Type** was free text already: a new type is created on save and suggested from then on.
- **Not changed:** the PDF review table still offers only the three default categories. The "Equipment" stat card
  and Ask questions use the `:Equipment` label, so items moved to a new category drop out of those.

## 6b. Conceptual view in Neo4j Browser

`queries_conceptual.cypher` gives the Graph tab's Conceptual level in Neo4j Browser. It is generated by
`make_conceptual_queries.py` from `graph_levels.yaml`, so the type lists are the app's.

**What the queries do**
- **Kept nodes:** main equipment and off-page connectors. As in the app, this includes Equipment-category items
  `graph_levels.yaml` doesn't list, except motors identified by their description.
- **Links:** each kept node links to the next kept node reached along `PIPE | DIRECT_PIPING_CONNECTION |
  CONTINUES_TO`. A run may pass only through items that aren't kept, and unreviewed candidate links are skipped.
- **Shortest run wins:** for each pair, the shortest run is used.
- **Link properties:** the links are APOC virtual relationships `FLOWS_TO {via, lines, steps, crossSheet}`.
  Nothing is written to the database.

**The three queries**
1. **Whole plant** (needs APOC).
2. **One sheet:** runs still stop at equipment on other sheets; only the start and end must be on the sheet.
3. **Table:** a version without APOC.

**Checked:** the query's rules were replayed in Python on the COMOS sheet and the DEXPI sample. They give the same
nodes and links as `graph_levels.build(..., "Conceptual")` (10 nodes / 13 links, and 8 / 9). Not yet run on Neo4j.

**Browser setting:** turn off *Connect result nodes* in Neo4j Browser settings. Otherwise Browser also draws the
stored relationships between the nodes it returns.

## 7. Operating notes

- **Restart the app after changing loader code.** Streamlit reruns `app.py` on every interaction but keeps the modules it imported at start-up. After changing `plant_loader.py`, `proteus_topology.py`, `graph_view.py`, `ui_style.py`, `pdf_pipeline/*` or `.streamlit/config.toml`, close the console window and start `Start P&ID Knowledge Graph.bat` again. Skipping this caused `AttributeError: module 'plant_loader' has no attribute 'looks_like_dexpi'` once.
- **Reload sheets to get the improvements.** Sheets already in Neo4j don't change by themselves. Load the file again with the same sheet name to replace it.
- **Testing without Neo4j.** `plant_loader.parse_sheet(path, sheet)` needs no database. `load_sheet` can be run against a stand-in object whose `run()` returns `[]` to check the write path.
- **Snap distance.** `SNAP_MM = 1.0` is in drawing units (mm for these exports). COMOS line ends land exactly (0.0 mm) on connection points. Raise it only if a source tool draws less precisely.

## 8. Possible next steps

- Load the neighbouring COMOS sheets and link the 4 lines leaving `=P002.A13.FB.001` (off-page arrows in COMOS, or `links.csv`).
- Add instrument loops (`InstrumentationLoopFunction`) as nodes, if loop-level questions are needed.
- Try `proteus_topology` on exports from other tools (AVEVA, Smart P&ID). The rules are Proteus-generic, but have only been checked on COMOS and the DEXPI sample.
- Add new items (not only edit existing ones) from the graph, e.g. an instrument the P&ID is missing.

---

## 9. Stand-alone islands closed (2026-10-17)

**Problem.** After loading `=P1.H3.FB.001` and `=P1.H4.FB.001`, the whole-plant export had 135 nodes in 33 islands,
13 of them single nodes. The links were in the XML; neither pyDEXPI nor `proteus_topology.py` read them.

**Fix.** New `dexpi_links.py`, run by `parse_sheet` after the other two passes. It reads the XML by DEXPI association
and class names only (no tags, IDs or sheet names in the code). Relationship names are the DEXPI names in upper case.
`owner_uid(xml_id)` maps any XML ID to an existing node: `<sheet>::<ID>` / the node made from that ID, else the node
whose `fullTag` is the element's `FullLabel`, else the parent of a `Nozzle`, COMOS `<Nozzles>`,
`ActuatingSystemComponent` or `CenterLine`.

| Rule | Adds | H3 + H4 |
|---|---|---|
| `Association Type="fulfills"` | `(ActuatingSystem)-[:FULFILLS]->(ActuatingFunction)` | 14 |
| `InstrumentationLoopFunction` | loop node (`<sheet>::<ID>`, name = FullLabel) `-[:IS_A_COLLECTION_INCLUDING]->` members | 46 |
| `InformationFlow` logical start / end | measuring / signal line; nozzle end → its equipment, property `nozzle` | 6 to nozzles, +1 signal line |
| ... end on a pipe `CenterLine` | `LINE:<FullLabel>` PipingNetworkSystem node, `-[:CONTAINS]->` the segment's components | 4 (+2 CONTAINS) |
| `PipingNetworkSegment` | PIPE along FromID + inline items + ToID, property `line` | 11 |
| Equipment without class, FullLabel = instrument + one part | type `Alarm`, category Instrumentation, `HAS_ALARM` | 3 |
| `OrphanInformationFlowCollectorDummy` | left out (COMOS artefact) | 1 |
| empty FromID / ToID | `(:OpenEnd {line, dir, sheet, asset})`; `link_sheets` joins out → in of the same line on two sheets with `PIPE {crossSheet: true}` | 2 |

**Guards.** A segment's chain only fills gaps: two neighbours that the existing pipes on that segment already join
get no second PIPE, so a segment listed out of order (the COMOS issue in section 4) adds nothing wrong. A segment
recording the same item at both ends uses neither end. Removing a sheet removes its OpenEnds and cross-sheet PIPEs.

**Result.** 166 nodes in 3 islands, 0 single, in either load order. Island 3 is pump `P002 (P1.H4.T1)` + motor: the
pump has no nozzles and no pipe in the COMOS export (drawing gap, not guessed).

**Update (2026-10-17): island 3 closed, now 1 island.** The pump does have connection points (`0.DI1` / `0.DO1`), and
reducers R002 (line P008) and R001 (an orphan segment) are drawn with their connection points exactly on them, with no
pipe between. Step 2b of `proteus_topology.complete` now joins a piping component to any item whose connection point
sits on the same spot (`inferred: "drawn touching"`); direction from DI / DO, else from the pipes already on either
side. On H4 it adds R002 → P002 → R001, so the conceptual view reads B003 → F001 → P002 (T1) → k001. No new links on
the other test files.

**Also:** `check_islands.py` (new), Validation tab section *Islands* (groups of ≤ 3 nodes), cross-sheet PIPEs counted
in "Cross-sheet links" and in the Ask tab's sheet-connection questions. The "instrument function not connected"
check now runs after every pass (`proteus_topology.report_unconnected`).

**Tests** (`python -m pytest tests -q`): `tests/expected_P1_H3_H4.json` holds the expected results;
`test_islands.py` checks them, loading in both orders, on a throwaway Neo4j (`PIDKG_TEST_NEO4J_URI`, default
`bolt://localhost:7688`; it wipes that database and skips one holding manual edits or other sheets).
`test_generality.py` parses every DEXPI 1.3 file in the project and checks links ≥ `tests/baseline_links.json`.

---

## 10. Detail levels in the graph view (2026-10-17)

After the three graph levels of ChatP&ID (TU Delft, arXiv 2603.22528). `graph_levels.py` computes them in Python from
the stored graph (`export_adjacency`) each time a view is drawn. Nothing is written to Neo4j and no relationship
types are added, so the levels follow every sheet loaded or removed.

| Level | Shows | H3 + H4, whole plant |
|---|---|---|
| Complete | every stored object | 166 nodes, 198 links |
| Process | equipment, valves, off-page connectors, one node per instrument loop; fittings collapsed | 39 flow nodes, 43 flow links (+ 31 loops, 30 loop links) |
| Conceptual | main equipment and off-page connectors; valves collapsed too | 16 nodes, 18 flow links |

- **Classes:** `graph_levels.yaml`, keyed by the DEXPI class in `type`. A type not listed counts as main equipment
  (category Equipment) or as a fitting; Validation tab → *Unclassified types* lists them.
- **Flow links:** from every kept node along PIPE / DIRECT_PIPING_CONNECTION / CONTINUES_TO, in their direction,
  through what isn't kept, to each kept node reached. Each link has `via` (collapsed items, in order), `lines` (pipe
  line FullLabels) and `crossSheet` (drawn dashed).
- **Folding:** motors and agitators into their equipment (COMPOSITION), actuator parts into the valve they operate,
  alarms into their instrument, instruments into their loop. Process: loops link (dotted) to what their members
  measure or act on. Conceptual: loops are listed on the equipment, or on the flow links through their valve / pipe.
- **Graph tab:** *Detail level* selector (default Conceptual for Whole plant, Process for Around / Downstream,
  Complete for One sheet). The Flow layout uses the level's flow links. Clicking a node shows what was folded into
  it, clicking a link its via / lines / loops. The JSON export is the chosen level, with `level` in the header.
  Editing is offered at the Complete level only, since the other levels' links are computed.
- **Ask tab:** the downstream and control-valve questions use the Process level.
- **Loader:** every pipe now carries `line` (its PipingNetworkSystem's FullLabel), so `lines` is complete. Reload
  sheets to get it.
- **Tests:** `tests/test_levels.py` (expected counts and links in `tests/expected_P1_H3_H4.json`; all three levels on
  every DEXPI 1.3 file).

---

## 11. PDF drawing check: the COMOS vector PDF audits the DEXPI pipe links (2026-10-17)

**Goal.** DEXPI stays the backbone. The vector PDF COMOS exports for the same sheet only checks pipe connectivity:
it confirms links, proposes missing ones and flags undrawn ones. Made from the prototype `pdf_crosscheck.py` (same
results on H4).

| File | What it does |
|---|---|
| `crosscheck/pdf_vs_dexpi.py` (new) | The check, one function per step: `coordinate_map` + `tag_match` (sanity check), `objects`, `line_segments` + `calibrate` (pipe width per file), `drawn_connections` (Shapely STRtree + NetworkX components), `dexpi_connections`, `check`, `overlay_png`. CLI: `python -m crosscheck.pdf_vs_dexpi <pdf> <dexpi> [overlay.png]`. |
| `crosscheck/store.py` (new) | Writes a check into the graph (`run`), review (`approve`, `reject`), tables for the app, PDF ↔ DEXPI pairing by file name (`pair_pdfs`). |
| `dexpi_links.py` | Rule 5's chain (FromID → inline items → ToID) is now `segment_chain(seg, owner)`, used by both. |
| `plant_loader.py` | `export_adjacency(..., candidates=False)` leaves out unreviewed candidates and puts `source`, `status`, `pdfCheck` on every relationship; `write_sheet` keeps `Sheet.pdfApproved` / `pdfRejected` across a reload. |
| `graph_levels.py` | `link_origin`: a level link through a drawing pipe keeps `source: 'drawing'` and its status. |
| `graph_view.py` | Candidates dashed orange ("Drawing candidate"); the link tooltip names the drawing as origin. |
| `app.py` | Tab 1 PDF upload + pairing column + summary line; tab 3 PDF check and open candidates per sheet; tab 4 *Show drawing candidates*; tab 6 *Drawing check* (scores, calibration, drawn-only with Approve / Reject, not drawn, overlay). |

**How the check works.**
- *Sanity check:* scale = PDF page width ÷ `Drawing/Extent/Max/X`, y flipped. Every DEXPI object whose TagName is one
  word, once, in the PDF text layer must lie within 15 mm of it. Below 50% the PDF is another sheet / revision / scale:
  `PdfMismatch`, no results. H4 PDF + H4 DEXPI: 64% (14 of 22); H4 PDF + H3 DEXPI: 0% (0 of 5).
- *Pipe width:* per stroke width, the solid segment ends that lie on DEXPI pipe route points (1.5 pt). Every width
  with ≥ 25% of the best is kept. H4: 0.96 pt 116, 0.71 pt 26, 1.42 pt 23, so only 0.96.
- *Drawn pairs:* segments join end to end or at a T (1.5 pt), not inside an object box; each run joins every object its
  ends reach (2 pt); boxes touching (1 pt) join too, unless one item has no ComponentClass (COMOS motors).
- *Result on H4:* 34 confirmed, 2 drawn only (pump `=P1.H4.T1P002` with reducers `…P008.001R002` and
  `…P009.001R001`), 0 not drawn.

**In the graph.** confirmed → `pdfCheck: 'confirmed'` on the sheet's PIPE; not drawn → `'not_drawn'`; drawn only →
`PIPE {source: 'drawing', status: 'candidate', sheet, pdfFile}`, unless a PIPE already joins the pair. Re-running
replaces the sheet's results (never duplicates); removing the sheet removes them. Approve sets `status: 'approved'`;
reject deletes the link and remembers the pair on the Sheet node.

**Interplay with section 9's step 2b.** The loader already links the two H4 pump pairs from the drawing geometry
(reducers drawn on the pump), so loading H4 as it is gives no candidates: the two drawn-only pairs are marked
`pdfCheck: 'confirmed'` with `pdfNote` "…already linked by the loader". The candidate path (approve / reject, islands,
Conceptual view) is tested by deleting those two PIPEs first.

**Tests.** `tests/test_pdf_crosscheck.py` with `tests/expected_pdf_crosscheck_h4.json`: check results, wrong pairing,
file pairing, results in the graph as loaded (H3 untouched, re-run, mismatch, removal) and the candidate review
(3 islands before approval, 1 after, F001 → P002 (P1.H4.T1) → k001 at the Conceptual level with `source: 'drawing'`,
rejection kept across a re-run and a reload). `conftest.sheet_sets()` keeps these files out of the sheet-set tests.

---

## 12. Neo4j holds only the conceptual level (2026-10-18)

**Why.** Neo4j held every DEXPI object (nozzles, pipe pieces, tees, actuators, instrument functions): 166 assets and
~370 relationships for H3 + H4, hard to read. Now the complete graph lives in memory while a sheet loads and is kept as
a file per sheet; Neo4j gets only the conceptual result.

**Pipeline.** `parse_sheet` (unchanged) → `plant_model.sheet_graph` (networkx) → optional PDF check on that graph
(`crosscheck/store.run`, now in memory) → review decisions → `snapshots/<sheet>.json` → all snapshots joined
(`plant_model.plant_graph`: shared tags merged, off-page arrows, open pipe ends, links.csv) → `graph_levels.build(...,
"Conceptual")` → `plant_store.write_conceptual` replaces the conceptual layer in Neo4j. Every load, removal, approval
and rebuild re-syncs the whole conceptual layer (small for a PoC; per-sheet sync would be the next step for a big plant).

| File | Change |
|---|---|
| `plant_store.py` (new) | `PlantStore`: sources/, snapshots/, decisions.json, load / remove / rebuild / adopt, approve / reject, reports, the Neo4j writer, `conceptual_view` (Graph tab), `label_counts` |
| `plant_model.py` (new) | complete graph in memory (networkx), snapshot format, joining sheets, geometry candidates, decisions |
| `plant_loader.py` | parsing + CLI only; the full-graph writer, `link_sheets`, `export_adjacency`, manual edits and `classify_valves` are gone. New `rebuild` command, `--pdf` on load |
| `crosscheck/store.py` | the check writes into the in-memory graph; review and tables moved to `plant_store` |
| `graph_levels.py` | `build` also returns `loopTargets` (what each loop measures or acts on, `acts` from the stored end's group) and keeps `viaUids` on links; `FLOWS_TO` counts as a flow relationship |
| `graph_view.py` | `FLOWS_TO` drawn as a pipe; link inspector "Show detail" (event `show_detail`); origin (Neo4j / file snapshot) in the status line |
| `app.py` | Load tab: "Loaded without drawing check" + unconnected equipment; Plant tab: **Rebuild graph**, counts by label / type; Graph tab: Conceptual from Neo4j (*Show loops*), Process / Complete from snapshots, Show detail; Ask / Validation on the new model; tab 7 Edit and editing on the graph removed |
| `queries_conceptual.cypher`, `queries_plant.cypher` | rewritten for the conceptual model; `make_conceptual_queries.py` removed (it built the conceptual view out of the full graph in Neo4j) |

**Neo4j model.** `(:Asset:Equipment|:Asset:Boundary)` + DEXPI class label, `FLOWS_TO {via, viaUids, lines, crossSheet,
source, status, pdfCheck, loops, kind}`, `(:Loop {tag, functions, alarms})`, `MEASURES`, `CONTROLS` → `(:Valve)` (only valves
a loop drives) or → the equipment whose drive a loop switches (e.g. E011 `EU` → pump P001: `MEASURES` would be wrong
there). Hierarchy unchanged (HAS_AREA / HAS_SYSTEM / HAS_SHEET / DEPICTS). Sheet nodes also carry `unconnectedEquipment`
and `pdfOpenCandidates`.

**Decision: links from the drawing geometry are candidates** (reverted the same day, see section 13). Step 2b of `proteus_topology.complete` (items drawn against
each other, `inferred: "drawn touching"`) still runs, but its links become `PIPE {source: 'drawing', status:
'candidate'}`: kept in the snapshot, out of Neo4j until approved. The PDF check proposes the same pairs (pdfCheck
`drawn_only`) instead of calling them "already linked by the loader". On H4: without the PDF, pump `P002 (P1.H4.T1)` has
no FLOWS_TO; with the PDF, its 2 candidates show in tab 6; approved → F001 → P002 → k001.

**Decision: editing dropped for now.** Hand edits were applied to the full graph in Neo4j. Rebuild saves any old
`ManualEdit` records to `sources/manual_edits_backup.json` before the reset. `graph_view.py` keeps its editing code,
switched off.

**Migration.** Tab 3 → *Rebuild graph* (or `python plant_loader.py rebuild`) resets Neo4j and reloads every sheet from
`sources/`. Sheets loaded before `sources/` existed are adopted from `uploads/` (DEXPI file, PDF path, hierarchy,
`pdfApproved` / `pdfRejected` → decisions.json; a PDF-route sheet keeps its items and links, read from Neo4j).

**Results, H3 + H4, rebuilt without PDFs (test database).**

| | before | after |
|---|---|---|
| nodes | 175 (166 assets + hierarchy + open ends) | 66: Loop 31, Asset 16 (Equipment 12, Boundary 4), Valve 14, Sheet 2, System 1, Area 1, Plant 1 |
| relationships | 370 | 109: DEPICTS 61, CONTROLS 20, FLOWS_TO 18, MEASURES 6, HAS_SHEET 2, HAS_AREA 1, HAS_SYSTEM 1 |

With `p1_h4.pdf` and the 2 candidates approved: FLOWS_TO 20. Loop L002 `MEASURES` B003 and `CONTROLS` H001.
After approval the Process and Complete levels from the snapshots equal the former ones exactly
(`tests/golden_views_P1_H3_H4.json`, written by the former pipeline before the change: Complete 166 / 200, Process 70 / 75).

**Tests** (`python -m pytest tests -q`, 46 pass): `tests/test_conceptual_store.py` (new: rebuild without / with PDF,
clean database, loops, hierarchy, detail views vs the golden file, Show detail); `test_islands.py`, `test_levels.py`,
`test_pdf_crosscheck.py` now load through `PlantStore` into a temporary folder, with `as_loaded` (candidates pending) and
`neo4j` sections in the expected JSON files.

**Operating notes.** Restart the app after this change (new modules). `PIDKG_DATA_DIR` moves `sources/` and
`snapshots/` (the `pid-kg-test-db` launch config uses `..\pid_kg_test_data`). Backup of every changed file:
`..\backup_2026-10-18_before_conceptual_only\`.

---

## 13. Links from the drawing geometry are connected again (2026-10-18)

**Why.** With section 12's candidate rule, H4's pump `P002 (P1.H4.T1)` had no flow until its 2 links were approved, and the
conceptual graph fell into two groups at that pump. That looked like the sheets were no longer connected (all 3
H3 ↔ H4 flows were in fact there).

**Change.** Step 2b's "drawn touching" links are ordinary pipes again, as before section 12: `plant_model.mark_geometry_candidates`
is gone, and `crosscheck/store.run` marks a drawn-only pair the loader already links as `pdfCheck: 'confirmed'` with
*already linked by the loader*. Candidates now only come from the PDF, for pairs nothing links. Tab 6 lists the PDF
candidates only. Tab 4 warns when candidates wait for review, naming them and the equipment left without a flow.

**Results, H3 + H4.** With or without `p1_h4.pdf`: 16 assets, FLOWS_TO 20, no unconnected equipment; the PDF check gives
34 confirmed · 2 drawn only (2 already linked by the loader) · 0 not drawn, so no candidates. Process and Complete equal
the former views (golden file) without any approval.

**Tests** (46 pass). Expected values: `expected_P1_H3_H4.json` loses `as_loaded`; `neo4j.flows` 20, nothing unconnected.
`expected_pdf_crosscheck_h4.json`: `drawn_only_already_linked` 2, `neo4j.flows_as_loaded` 20. The candidate review
(approve, reject, kept across reload and rebuild) is tested as before section 12: `test_pdf_crosscheck.py` loads H4
without the step 2b links (fixture `without_step_2b`), so the PDF proposes the 2 pairs (18 flows → 20 after approval).

**To do on an existing database:** restart the app, then tab 3 → *Rebuild graph*.

## 14. Graph tab defaults, and "Copy Neo4j query" for every view (2026-10-18)

- **Defaults:** the Graph tab opens on **Whole plant**. Its detail level defaults to Conceptual (`DEFAULT_LEVEL`) and
  the layout to Flow (left → right).
- **Copy button:** a new toolbar button in the graph (database icon) copies the Neo4j query for what is on screen. The
  query is also shown in an expander under the graph, with Streamlit's copy icon, in case the browser blocks the
  clipboard inside the frame. The small *Show detail* graph has the button too.
- **How the query is built** (`view_query.for_view`):
  - **Conceptual from Neo4j:** `view_query.conceptual(view, loops)` builds a live query on `Asset` / `FLOWS_TO` (+
    `Loop`, `Valve`, `MEASURES`, `CONTROLS` with *Show loops*). It picks the same items as the app:
    - whole plant: all;
    - one sheet: `sheet IN n.sheets`;
    - around an asset: within *steps out* links, either direction;
    - downstream: along `FLOWS_TO`.
    It then returns every link between them.
  - **Process / Complete / snapshot views:** `view_query.embedded(subset, ...)` carries the view as data. Items become
    `apoc.create.vNode(labels: category + type, props)`, links `apoc.create.vRelationship` (type, via, lines, loops,
    crossSheet, ...).
- **How it reaches the page:** `graph_view.show(..., cypher=...)` passes the text to the page outside the version
  hash, so a different query alone doesn't redraw the graph.
- **Checked:**
  - **Copy button:** tested in a browser with the component page (copies the query, confirms with a toast).
  - **Queries:** generated for each mode and an embedded Process view of the COMOS sheet.
  - **Not yet run against Neo4j:** neither query has been run there yet.

