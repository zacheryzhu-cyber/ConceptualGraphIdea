# ConceptualGraphIdea

**From a COMOS P&ID to a plant knowledge graph you can look at, query and reason about.**

A proof of concept (PoC): load P&ID sheets exported from COMOS as DEXPI XML, turn them into one connected graph of the
whole plant, and show it at the level of detail a person actually wants, starting with the simplest one.

```
COMOS P&ID  ──►  DEXPI XML  ──►  complete graph per sheet  ──►  sheets joined into one plant  ──►  Conceptual graph in Neo4j
 (drawing)       (export)         (every nozzle, pipe, valve,        (same tag = same asset,           (equipment + flows,
                                   instrument, loop)                  off-page arrows linked)           shown in the web app)
```

---

## 1. The idea

### Why a knowledge graph?
A P&ID is a drawing. People can read it, but software cannot ask it questions such as *"what is downstream of this
pump?"*, *"if this control valve closes, which equipment loses supply?"* or *"which sheets show this tank?"*. A
**knowledge graph** stores the same information as **things** (nodes) and **relationships** (edges). Once a plant is a
graph, those questions become queries, and the graph can feed dashboards, impact analysis, or an AI assistant that
answers from facts instead of guessing from pictures.

### The whole process, step by step
1. **Source:** a P&ID drawn in COMOS. COMOS exports each sheet as DEXPI (Proteus XML), the open exchange standard
   for P&IDs.
2. **Read:** the open-source library pyDEXPI reads the XML. Because it leaves things out, the project adds its own
   passes to complete the graph (see section 3).
3. **Complete graph per sheet:** every object on the sheet (equipment, nozzles, pipe pieces, valves, instruments,
   instrument loops) and how they connect. This is kept in memory and saved as a file (`snapshots/<sheet>.json`).
4. **Join the sheets:** the same equipment tag on several sheets becomes one asset, and off-page arrows with the same
   line number link sheets to each other. The result is **one plant graph**, not one graph per drawing.
5. **Simplify to levels:** the complete graph is too detailed to read. Three levels are computed from it:

   | Level | Shows | Example (2 sheets, H3 + H4) |
   |---|---|---|
   | **Complete** | every object | 166 nodes, 198 links |
   | **Process** | equipment, valves, off-page connectors, one node per instrument loop | 39 flow nodes, 43 flow links |
   | **Conceptual** | main equipment and off-page connectors; valves and fittings folded into the flow links | 16 nodes, 18 flow links |

6. **Store only the conceptual level in Neo4j:** equipment, off-page connectors, the flows between them (each link
   remembers the valves and pipe lines it passes) and instrument loops. Nozzles, tees and reducers stay in the
   per-sheet files. Neo4j stays small and readable; the detail is one click away.
7. **Look at it:** the web app opens straight on the whole-plant conceptual graph, left to right. A dropdown changes
   what is shown, the detail level and the layout; one button adds more sheets.

The three levels follow the idea in *ChatP&ID* (TU Delft, arXiv 2603.22528): one source, several views, so that people
and AI assistants can each use the level that fits their question.

---

## 2. What it proves, and what it does

**What the PoC shows**
- A COMOS P&ID can be turned into a connected, queryable plant graph **without redrawing or manual tagging**, using
  the standard export.
- Several sheets merge into one plant graph automatically (shared tags, matching off-page line numbers).
- A graph that is readable at plant level (16 nodes for two sheets) can still be opened down to every nozzle when
  needed.
- The graph can be checked against the drawing: the COMOS vector PDF of a sheet confirms the pipe links.

**Functions in the app**
| Function | Description |
|---|---|
| Graph view | Opens on the whole plant, Conceptual level, flow left → right, simple view (dots and plain lines) |
| View options | Show: whole plant, around an asset, downstream of an asset, one sheet. Detail level and layout |
| Add / load DEXPI / PDF | Adds sheets (`.xml` or COMOS exports without extension, found by content); optional matching PDF |
| Inspector | Click an item for its type, sheets and what was folded into it; click a link for the valves and lines it passes, and **Show detail** for that path |
| Neo4j | Conceptual graph stored in Neo4j (Aura or local); example queries in `pid_kg/queries_conceptual.cypher` |
| PDF drawing check | Pipe links confirmed (or flagged) against the vector PDF of the same sheet |
| Command line | `plant_loader.py`: load, remove, rebuild, summary, validation report |

**Test sheets in `sourcefile/`:** COMOS exports `=P1.H2.FB.001`, `=P1.H2.FB.002`, `=P1.H3.FB.001`, `=P1.H4.FB.001`
with their PDFs, for trying the app and for demos.

---

## 3. Problems met, and how they were handled

| Problem | What was done |
|---|---|
| COMOS files have no `.xml` extension and carry a byte-order mark | Files are recognised by content; the loader reads the bytes itself |
| COMOS keeps tags outside the DEXPI attributes, so everything came out untagged | Tags are read from COMOS properties; the full tag is the identity, so the same object on several sheets merges |
| pyDEXPI drops items it has no class for and many links: about a third of one sheet's items were unconnected | Extra passes read the XML again and add missing items and links (pumps, valves, drives, signal lines). One test sheet went from 30 unconnected items to 1 |
| Sheets showed as separate islands (33 islands on two sheets) | Added link rules from the XML (segments, loops, nozzles, open pipe ends); two sheets now form one connected group |
| Full graph in Neo4j was unreadable (175 nodes for two sheets) | Neo4j holds only the conceptual level (66 nodes including hierarchy and loops); detail comes from the saved snapshots |
| A drawing may disagree with its data | The vector PDF is used to check the links, never as the source |

---

## 4. Known limits (be honest about these)
- **Small test set.** The behaviour has been checked on a handful of sheets (COMOS H2 to H4 and the DEXPI sample), not
  on a whole plant. The rules follow the DEXPI standard, but they are only proven on COMOS and the DEXPI sample.
- **Some links are inferred.** Items drawn touching each other with no pipe are joined from the drawing geometry
  (marked `inferred`). Pipe ends that leave a sheet without an off-page arrow cannot be linked and go to the
  validation report.
- **Untagged parts do not merge across sheets.** Actuators and some instrument parts have no COMOS tag, so each
  sheet keeps its own copy.
- **Every load re-writes the whole conceptual layer in Neo4j.** Fine for a PoC; a large plant needs per-sheet updates.
- **Whole-plant view is capped at 1,500 items.**
- **Editing on the graph is switched off.** The levels are computed, so changes would have to be made at the source.
- **The vision route was dropped.** Reading a P&ID from a PDF or image alone (symbol detection and OCR) was tried and
  removed; the PDF is now only an optional cross-check. The old code is in the Git history (first commit).
- **Shared demo database.** The demo connects to a shared Neo4j Aura Free instance, and its credentials are in the
  repository. Anyone with them can read or overwrite the data. Replace them before using anything real.
- **No licence, and third-party licences apply.** This is a proof of concept with no licence of its own (see
  section 7). Two libraries it uses, pyDEXPI and PyMuPDF, are AGPL-3.0, which matters before any product or hosted
  use.

---

## 5. What to work on next
1. **Load the whole COMOS area.** Link the lines that leave a sheet and see how the graph behaves at plant scale.
2. **Update per sheet** instead of rewriting the conceptual layer on every load.
3. **Ask questions in plain language.** Put an assistant on top of the graph: generate the Cypher, or answer from the
   Conceptual and Process levels (the ChatP&ID idea). The ready-made questions that used to be in the app are in the
   Git history.
4. **Bring in more context.** Link assets to maintenance, process data, alarms or documents, so the graph answers
   operational questions, not only connectivity ones.
5. **Try other tools' exports** (AVEVA, Smart P&ID) to confirm the rules are really generic.
6. **Add and correct items from the graph** with a proper write-back to the source, instead of editing the copy.
7. **Tests on more drawings** and a cleaner tag convention, so merging across sheets is reliable.
8. **Security:** per-user credentials and a private repository before real plant data is used.

---

## 6. Run it

1. Install Python 3.12. In the `pid_kg` folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. Connection: `pid_kg/neo4j_demo.env` holds the Neo4j URI, user and password (template:
   `neo4j_demo.env.example`).
3. Start the app: double-click `pid_kg/Start P&ID Knowledge Graph.bat` (or `streamlit run app.py` in `pid_kg`). It
   opens http://localhost:8501.
4. Click **Add / load DEXPI / PDF** and pick files from `sourcefile/`.

Technical details (data model, how the loader completes the graph, command line, tests) are in
[`pid_kg/README.md`](pid_kg/README.md) and [`pid_kg/CHANGES.md`](pid_kg/CHANGES.md).

## 7. Technology, projects and credits

**This project has no licence.** It is an unlicensed proof of concept written to test an idea. No licence file is
included, so no one is granted any right to reuse or redistribute the original code in this repository beyond viewing
it. All the technology below belongs to its respective owners and is used under their own licences. Licences are listed
as I understand them and should be checked at the source before any reuse; this is not legal advice.

### What the project builds on (used by the current app)
| Project / technology | Used for | Licence |
|---|---|---|
| [COMOS](https://www.siemens.com/comos) (Siemens) | Source of the P&ID drawings and their DEXPI exports. Product and trademark of Siemens; no COMOS code is used | Commercial product |
| [DEXPI](https://dexpi.org) (Proteus XML, DEXPI 1.3) | The open data exchange standard for P&IDs that COMOS exports | Standard by DEXPI e.V. |
| [pyDEXPI](https://github.com/process-intelligence-research/pyDEXPI) (TU Delft) | Reads DEXPI / Proteus XML into a Python graph. The project adds its own passes on top | AGPL-3.0 |
| DEXPI sample P&ID `C01V04-VER.EX01.xml` | Reference sheet for tests, shipped with pyDEXPI | © DEXPI e.V. |
| [Neo4j](https://neo4j.com) and [Neo4j Aura Free](https://neo4j.com/cloud/platform/aura-graph-database/) | Graph database for the conceptual level, hosted for the demo | Neo4j Community / Aura terms |
| [neo4j Python driver](https://github.com/neo4j/neo4j-python-driver) | Connects the app to Neo4j | Apache-2.0 |
| [Streamlit](https://streamlit.io) | The web app | Apache-2.0 |
| [vis-network](https://visjs.github.io/vis-network/) via [pyvis](https://github.com/WestHealth/pyvis) | Interactive graph drawing (the library file is read from the pyvis package, so it works offline) | MIT / Apache-2.0 (vis-network), BSD (pyvis) |
| [NetworkX](https://networkx.org) | The complete graph in memory, joining sheets, the detail levels | BSD-3-Clause |
| [pandas](https://pandas.pydata.org) | Tables in the load dialog | BSD-3-Clause |
| [PyYAML](https://pyyaml.org) | `graph_levels.yaml`, the rules for the detail levels | MIT |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (Artifex) | Reads vector PDFs for the drawing check | AGPL-3.0 or Artifex commercial licence |
| [Shapely](https://github.com/shapely/shapely) | Geometry for the drawing check | BSD-3-Clause |
| [certifi](https://github.com/certifi/python-certifi) | CA certificates for the TLS connection to Aura | MPL-2.0 |
| [pytest](https://pytest.org) | Tests | MIT |
| [Python](https://www.python.org) 3.12 | Runtime | PSF licence |
| Inter, Space Grotesk, JetBrains Mono (Google Fonts) | Fonts, loaded from Google Fonts, system fonts when offline | SIL Open Font License |

### Ideas and earlier code in the Git history
The first commit also contained an experiment that read a P&ID straight from a PDF or image. It was removed from the app
but remains in the history (commit `c6ee8d2`), and it used:
- [Microsoft Azure P&ID digitization sample](https://github.com/Azure-Samples) (line detection and graph
  construction, run locally), MIT;
- [RF-DETR](https://github.com/roboflow/rf-detr) (Roboflow) and the community P&ID weights
  `dimtri009/rfdetr-pid-detector` on Hugging Face, with a [Roboflow](https://roboflow.com) cloud option. The
  weights were never part of the repository;
- [RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0), [OpenCV](https://opencv.org) (Apache-2.0),
  [supervision](https://github.com/roboflow/supervision) (MIT), FastAPI and Matplotlib.

The three detail levels (Complete, Process, Conceptual) follow the idea in the paper *ChatP&ID* (TU Delft,
arXiv 2603.22528). Only the idea is used; no code from it.

### AI assistance
The code and this documentation were written with the help of Claude Code (Anthropic), under the author's direction
and review.

### Third-party licence note
pyDEXPI and PyMuPDF are AGPL-3.0. Using them in this internal proof of concept is fine; offering a modified version as
a hosted service or shipping it in a closed-source product would need either AGPL compliance or commercial licences
(TU Delft for pyDEXPI, Artifex for PyMuPDF).

## Repository layout
| Path | Content |
|---|---|
| `pid_kg/` | the app, loader, graph levels, PDF check, tests |
| `sourcefile/` | demo COMOS DEXPI exports and PDFs |
| `claude_code_prompt*.md` | the working prompts used to build each stage |
| `sync_github.ps1` | one-command commit and push |
