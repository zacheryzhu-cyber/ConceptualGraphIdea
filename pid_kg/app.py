"""
P&ID Knowledge Graph — web interface for plant_store.py (loading) and plant_loader.py (reading DEXPI).

Neo4j holds only the conceptual level (main equipment, off-page connectors, loops and the flows between them). The
complete graph of each sheet is kept as a file (snapshots/<sheet>.json); the Process and Complete levels are computed
from those files.

Start it with:   streamlit run app.py
It opens in your browser at http://localhost:8501
"""
import json
import os
from types import SimpleNamespace

import pandas as pd
import streamlit as st

import check_islands
import graph_levels as gl
import graph_view
import plant_loader as pl
import plant_store as ps
import ui_style as ui
import view_query as vq
from crosscheck import pdf_vs_dexpi as pdf_check
from crosscheck import store as pdf_store

UPLOAD_DIR = "uploads"
LAYOUTS = graph_view.LAYOUTS
DEFAULT_LEVEL = {"Whole plant": "Conceptual", "Around an asset": "Process", "Downstream of an asset": "Process",
                 "One sheet": "Complete"}

st.set_page_config(page_title="P&ID Knowledge Graph", page_icon="🔗", layout="wide")
ui.inject()


# --------------------------------------------------------------------------- connection

@st.cache_resource(show_spinner=False)
def get_db(uri, user, password, test_db):
    db = pl.Db(SimpleNamespace(uri=uri, user=user, password=password, database=None, test_db=test_db))
    db.setup()
    return db


db = None
conn = st.session_state.get("conn")  # (uri, user, password, test_db) once connected; kept in memory only
if conn:
    db = get_db(*conn)
else:
    # the login panel is only shown until the connection works
    with st.sidebar:
        ui.sidebar_brand()
        st.header("Neo4j connection")
        test_db = os.environ.get("PLANT_TEST_DB")  # offline testing only
        uri = st.text_input("URI", os.environ.get("NEO4J_URI", "bolt://localhost:7687"), disabled=bool(test_db))
        user = st.text_input("User", os.environ.get("NEO4J_USER", "neo4j"), disabled=bool(test_db))
        password = st.text_input("Password", os.environ.get("NEO4J_PASSWORD", ""), type="password",
                                 disabled=bool(test_db), help="Kept in memory only, never saved to a file.")
        if test_db or password:
            try:
                get_db(uri, user, password, test_db)
                st.session_state["conn"] = (uri, user, password, test_db)
                st.rerun()
            except Exception as exc:
                st.error(f"Cannot connect: {exc}")
        else:
            st.info("Enter the Neo4j password to start.")

ui.hero("P&ID", "Knowledge Graph", "Load DEXPI P&ID sheets, and each one is added to one plant knowledge graph.",
        [("Neo4j online · " + ("offline test database" if conn[3] else conn[0]), "ok") if db
         else ("Waiting for Neo4j", "wait")])
if not db:
    st.stop()
with st.container(key="pk_signout"):
    if st.button("Disconnect", icon=":material/logout:", type="tertiary", help="Back to the Neo4j login panel"):
        del st.session_state["conn"]
        st.rerun()

store = ps.PlantStore(db)


def q(query, **params):
    return db.run(query, **params)


def show_stats():
    ui.stat_cards(store.stats())


def node_options(view):
    """{uid: label} of a level graph's nodes, equipment first."""
    order = {"Equipment": 0, "Instrumentation": 1, "Piping": 2}
    rows = sorted(view["nodes"].items(), key=lambda kv: (order.get(kv[1].get("category"), 3), str(kv[1].get("name"))))
    return {u: (n.get("name") or u) + (f"  (sheet {n['sheet']})" if n.get("sheet") else "") for u, n in rows}


def pick_node(label, key, view, where=None):
    opts = {u: t for u, t in node_options(view).items() if where is None or where(view["nodes"][u])}
    if not opts:
        st.info("No matching items loaded yet.")
        return None
    return st.selectbox(label, list(opts), format_func=opts.get, key=key)


@st.cache_data(show_spinner="Drawing the overlay…", max_entries=8)
def pdf_overlay(pdf_path, dexpi_path, checked):
    """PNG of the PDF with the cross-check drawn on it (checked: when the check ran, so a re-run redraws)."""
    return pdf_check.overlay_png(pdf_path, pdf_check.check(pdf_path, dexpi_path))


def view_rows(view):
    """graph_view's nodes and edges from an adjacency export (any detail level)."""
    nodes = [{"uid": u, "name": n.get("name", u), "cat": n.get("category"), "type": n.get("type"),
              "desc": n.get("description"), "line": n.get("lineNumber"), "sheets": n.get("sheets", []),
              "folded": n.get("folded"), "instruments": n.get("instruments")} for u, n in view["nodes"].items()]
    edges = [{"s": u, "t": r["to"], "rel": r["type"], "sheet": r["properties"].get("sheet"),
              "source": r["properties"].get("source"), "status": r["properties"].get("status"),
              **{k: r["properties"].get(k) for k in ("crossSheet", "loopLink", "via", "lines", "loops")}}
             for u, rels in view["adjacency"].items() for r in rels]
    return nodes, edges


def draw(view, uids, focus=None, height=650, key="graph_main", level="Complete", origin="", cypher=""):
    """Draw `uids` of a level graph. A FLOWS_TO link's inspector offers "Show detail" (its Process path below)."""
    uids = list(uids)
    if not uids:
        st.info("Nothing to show.")
        return
    nodes, edges = view_rows(gl.subset(view, uids))
    layout = st.session_state.get("layout", "Flow (left → right)")
    event = graph_view.show(nodes, edges, focus, layout, height, pl.OFFPAGE_TYPES, key=key, level=level,
                            flash=st.session_state.pop(f"{key}_flash", None), detail=level == "Conceptual",
                            origin=origin, cypher=cypher)
    # the page sends each request once, but Streamlit returns it again on every rerun: act on a new nonce only
    if event and event.get("nonce") != st.session_state.get(f"{key}_nonce"):
        st.session_state[f"{key}_nonce"] = event["nonce"]
        if event.get("action") == "show_detail":
            st.session_state["link_detail"] = (event["src"], event["dst"])


def show_link_detail(view):
    """The Process view of the FLOWS_TO link picked with "Show detail", from the snapshots."""
    picked = st.session_state.get("link_detail")
    if not picked:
        return
    src, dst = picked
    link = next((r for r in view["adjacency"].get(src, []) if r["to"] == dst and r["type"] == "FLOWS_TO"), None)
    if not link:
        st.session_state.pop("link_detail", None)
        return
    names = {u: n.get("name", u) for u, n in view["nodes"].items()}
    p = link["properties"]
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**Detail: {names.get(src, src)} → {names.get(dst, dst)}** · Process level, {ps.FROM_SNAPSHOT}  \n"
                    f"via {' → '.join(p.get('via') or []) or 'nothing (direct)'}"
                    + (f" · lines {', '.join(p.get('lines') or [])}" if p.get("lines") else ""))
        if c2.button("Close detail", key="close_detail"):
            st.session_state.pop("link_detail", None)
            st.rerun()
        detail = store.path_detail(src, dst, p.get("viaUids") or [])
        nodes, edges = view_rows(detail)
        query = vq.embedded(detail, {"mode": f"Detail {names.get(src, src)} → {names.get(dst, dst)}"}, "Process",
                            ps.FROM_SNAPSHOT)
        graph_view.show(nodes, edges, src, st.session_state.get("layout", "Flow (left → right)"), 300,
                        pl.OFFPAGE_TYPES, key="graph_detail", level="Process", origin=ps.FROM_SNAPSHOT, cypher=query)


def load_report(results, pdf_lines):
    """What a load (or a rebuild) did, per sheet: the drawing check line and the unconnected equipment."""
    for r in results:
        if r.get("pdf") is None:
            loose = r.get("unconnected") or []
            st.warning(f"**{r['sheet']}** — Loaded without drawing check. {len(loose)} equipment item"
                       f"{'s' if len(loose) != 1 else ''} with no connection"
                       + (f": {', '.join(loose)}." if loose else ".")
                       + (" Add the sheet's PDF to check them." if loose else ""))
    for kind, text in pdf_lines:
        (st.info if kind == "ok" else st.error)(text)


tab_load, tab_pdf, tab_plant, tab_graph, tab_ask, tab_check = st.tabs(
    ["1 · Load DEXPI", "2 · Load PDF / image", "3 · Plant", "4 · Graph", "5 · Ask", "6 · Validation"])

# --------------------------------------------------------------------------- 1 load

with tab_load:
    st.subheader("Add P&ID sheets")
    # no type filter: COMOS exports are DEXPI XML but have no .xml extension, so files are checked by content
    files = st.file_uploader("DEXPI P&ID files (Proteus XML, DEXPI 1.3 — .xml files or COMOS exports)",
                             accept_multiple_files=True)
    if files:
        rejected = [f.name for f in files if not pl.looks_like_dexpi(f.getvalue()[:4000])]
        if rejected:
            st.error(f"Not a DEXPI / Proteus XML file, skipped: {', '.join(rejected)}")
        files = [f for f in files if f.name not in rejected]
    c1, c2, c3 = st.columns(3)
    existing_plants = [r["name"] for r in q("MATCH (p:Plant) RETURN p.name AS name")]
    plant = c1.text_input("Plant", existing_plants[0] if existing_plants else pl.DEFAULT_PLANT)
    default_area = c2.text_input("Default area", "")
    default_system = c3.text_input("Default system", "")
    pdfs = st.file_uploader("Matching PDF (same sheet) — optional, the vector PDF COMOS exports for a sheet",
                            type=["pdf"], accept_multiple_files=True, key="dexpi_pdfs",
                            help="Checks the sheet's pipe links against the pipes drawn in the PDF: confirms links, "
                                 "proposes missing ones (to review in tab 6) and flags links that are not drawn. "
                                 "A PDF is paired with a sheet when its file name contains the sheet's drawing "
                                 "number or label; otherwise pick it in the table. Without a PDF the sheet loads "
                                 "normally, without the check.")
    links_file = st.file_uploader("Manual off-page links (optional links.csv)", type=["csv"],
                                  help="Columns: from_sheet,from_id,to_sheet,to_id — only for arrows "
                                       "whose line numbers don't match.")
    if files:
        loaded = set(store.registry()) | {r["name"] for r in q("MATCH (s:Sheet) RETURN s.name AS name")}
        sheets_in = [{"sheet": pl.sheet_name_from_file(f.name), "file": f.name,
                      "drawing": pdf_store.drawing_name(f.getvalue()[:20000])} for f in files]
        paired = pdf_store.pair_pdfs(sheets_in, [p.name for p in pdfs or []])
        table = pd.DataFrame([{
            "file": s["file"],
            "sheet": s["sheet"],
            "revision": "",
            "area": default_area,
            "system": default_system,
            "pdf": paired[s["sheet"]],
        } for s in sheets_in])
        st.caption("Check the sheet name, revision, area, system and matching PDF for each file. "
                   "A sheet name that is already loaded will be replaced.")
        edited = st.data_editor(table, hide_index=True, use_container_width=True,
                                column_config={"file": st.column_config.TextColumn(disabled=True),
                                               "pdf": st.column_config.SelectboxColumn(
                                                   "matching PDF", options=[""] + [p.name for p in pdfs or []],
                                                   help="The vector PDF of the same sheet (optional)")})
        replace = sorted(set(edited["sheet"]) & loaded)
        if replace:
            st.warning(f"These sheets are already loaded and will be replaced: {', '.join(replace)}")
        if edited["sheet"].duplicated().any():
            st.error("Two files have the same sheet name. Give each file its own sheet name.")
        elif st.button("Add to knowledge graph", type="primary"):
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            by_name = {f.name: f for f in files}
            if links_file:
                store.set_links_csv(links_file.getvalue())
            pdf_by_name = {p.name: p for p in pdfs or []}
            results, pdf_lines, table_rows = [], [], []
            bar = st.progress(0.0)
            for i, row in enumerate(edited.to_dict("records")):
                path = os.path.join(UPLOAD_DIR, row["file"])
                with open(path, "wb") as fh:
                    fh.write(by_name[row["file"]].getvalue())
                pdf_path = None
                if row.get("pdf") in pdf_by_name:
                    pdf_path = os.path.join(UPLOAD_DIR, row["pdf"])
                    with open(pdf_path, "wb") as fh:
                        fh.write(pdf_by_name[row["pdf"]].getvalue())
                sheet = str(row["sheet"]).strip()
                try:
                    r = store.load_sheet(path, sheet, plant.strip() or pl.DEFAULT_PLANT, row["area"] or None,
                                         row["system"] or None, row["revision"] or None, pdf_path, row.get("pdf"),
                                         sync=False)
                    results.append(r)
                    if r["pdf"]:
                        kind = "ok" if r["pdf"].get("pdfStatus") == "ok" else "error"
                        pdf_lines.append((kind, f"**{sheet}** with {row['pdf']} — {r['pdfLine']}"))
                    table_rows.append({"sheet": sheet, "result": "loaded", "assets": r["assets"],
                                       "connections": r["connections"], "issues": len(r["issues"]),
                                       "drawing check": r["pdfLine"]})
                except Exception as exc:
                    table_rows.append({"sheet": sheet, "result": f"failed: {exc}", "assets": 0, "connections": 0,
                                       "issues": 0, "drawing check": ""})
                bar.progress((i + 1) / len(edited))
            with st.spinner("Writing the conceptual level to Neo4j…"):
                store.sync()
            for r in results:
                r["unconnected"] = store.unconnected(r["sheet"])
            for t in table_rows:
                t["unconnected equipment"] = next((len(r["unconnected"]) for r in results if r["sheet"] == t["sheet"]),
                                                  "")
            st.success(f"Done. {len(results)} sheet(s) loaded; Neo4j now holds the conceptual level of the plant.")
            load_report(results, pdf_lines)
            st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)
    show_stats()

# --------------------------------------------------------------------------- 2 pdf / image

with tab_pdf:
    st.subheader("Add a P&ID from a PDF or image")
    st.caption("Symbols are found by the RF-DETR model, tags from the PDF text layer or local OCR, equipment from "
               "its tag and outline, and connections by the Azure sample's line code — all on this computer, unless "
               "you tick the Roboflow cloud box. "
               "Nothing goes into the graph until you have reviewed it below.")
    pdf_file = st.file_uploader("P&ID drawing (PDF, PNG or JPG)", type=["pdf", "png", "jpg", "jpeg"], key="pdf_up")
    c1, c2, c3, c4 = st.columns(4)
    p_sheet = c1.text_input("Sheet", os.path.splitext(pdf_file.name)[0] if pdf_file else "", key="pdf_sheet")
    p_rev = c2.text_input("Revision", "", key="pdf_rev")
    p_area = c3.text_input("Area", "", key="pdf_area")
    p_system = c4.text_input("System", "", key="pdf_system")
    c5, c6, c7 = st.columns([2, 1, 1])
    weights = c5.text_input("RF-DETR weights file", os.environ.get("PID_RFDETR_WEIGHTS", "models/rfdetr_pid.pth"))
    p_page = c6.number_input("PDF page", min_value=1, value=1, step=1)
    force_ocr = c7.checkbox("Use OCR (scanned PDF)", help="Ignore the PDF's own text and read the picture instead.")
    use_cloud = st.checkbox("Also use a Roboflow cloud model — uploads the drawing to Roboflow", key="pdf_cloud",
                            help="Adds what the cloud model finds, including equipment such as pumps and tanks. "
                                 "Leave unticked to keep everything on this computer.")
    roboflow = None
    if use_cloud:
        from pdf_pipeline.roboflow_cloud import DEFAULT_MODEL
        c8, c9 = st.columns(2)
        rf_model = c8.text_input("Roboflow model (dataset/version)",
                                 os.environ.get("PID_ROBOFLOW_MODEL", DEFAULT_MODEL), key="pdf_rf_model",
                                 help="A Universe model id, e.g. p-id-symbols/1 for "
                                      "universe.roboflow.com/pid-connect/p-id-symbols (model version 1).")
        rf_key = c9.text_input("Roboflow API key", os.environ.get("ROBOFLOW_API_KEY", ""), type="password",
                               key="pdf_rf_key", help="Roboflow → Settings → API Keys. Kept in memory only, "
                                                      "never saved to a file.")
        if rf_key and rf_model:
            roboflow = {"model_id": rf_model.strip(), "api_key": rf_key.strip()}
        else:
            st.info("Enter the Roboflow model and API key, or untick the box.")
    if pdf_file and p_sheet and (roboflow or not use_cloud) and st.button("Extract", type="primary"):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        path = os.path.join(UPLOAD_DIR, pdf_file.name)
        with open(path, "wb") as fh:
            fh.write(pdf_file.getvalue())
        with st.spinner("Reading the drawing… on a CPU this can take a few minutes per sheet."):
            try:
                from pdf_pipeline.extract import extract_sheet
                from pdf_pipeline import review as rv
                res = extract_sheet(path, p_sheet.strip(), weights, out_dir="pdf_output",
                                    page_number=int(p_page) - 1, force_ocr=force_ocr, roboflow=roboflow)
                st.session_state["pdf_result"] = res
                st.session_state["pdf_assets"] = pd.DataFrame(rv.asset_table(res))
                st.session_state["pdf_edges"] = pd.DataFrame(rv.edge_table(res))
            except Exception as exc:
                st.error(f"Extraction failed: {exc}")
    res = st.session_state.get("pdf_result")
    if res:
        cloud_note = (f", plus {res['roboflow_symbols']} symbols from Roboflow model {res['roboflow_model']}"
                      if res.get("roboflow_model") else "")
        st.markdown(f"**Sheet {res['sheet']}** — {len(res['assets'])} items and {len(res['edges'])} connections "
                    f"found (text from {res['text_source']}{cloud_note}). "
                    "Boxes show what was found; red arrows are connections.")
        st.image(res["overview_image"], use_container_width=True)
        st.markdown("**1. Check the items.** Untick what is wrong, fix names, and tick *tag* for real plant tags "
                    "(the same tag on another sheet becomes the same asset).")
        assets_edit = st.data_editor(
            st.session_state["pdf_assets"], hide_index=True, use_container_width=True, key="pdf_assets_ed",
            column_order=["keep", "name", "category", "tag", "type"],
            column_config={"category": st.column_config.SelectboxColumn(
                options=["Equipment", "Instrumentation", "Piping"]),
                "type": st.column_config.TextColumn(disabled=True)})
        st.markdown("**2. Check the connections.** Untick connections that are wrong.")
        edges_edit = st.data_editor(
            st.session_state["pdf_edges"], hide_index=True, use_container_width=True, key="pdf_edges_ed",
            column_order=["keep", "from", "to", "direction"],
            column_config={c: st.column_config.TextColumn(disabled=True) for c in ("from", "to", "direction")})
        if st.button("Add reviewed sheet to knowledge graph", type="primary"):
            from pdf_pipeline import review as rv
            assets, edges, issues = rv.apply_review(res, assets_edit.to_dict("records"),
                                                    edges_edit.to_dict("records"), res["sheet"])
            r = store.load_reviewed(res["sheet"], assets, edges, issues,
                                    (existing_plants[0] if existing_plants else pl.DEFAULT_PLANT),
                                    p_area or None, p_system or None, p_rev or None, res["file"])
            st.success(f"Sheet {res['sheet']} added: {r['assets']} assets, {r['connections']} connections. "
                       "See tabs 3 to 5.")

# --------------------------------------------------------------------------- 3 plant

with tab_plant:
    st.subheader("Plant overview")
    show_stats()
    if st.session_state.get("plant_flash"):
        kind, text = st.session_state.pop("plant_flash")
        (st.success if kind == "ok" else st.warning)(text)
    sheets = q("""MATCH (p:Plant)-[:HAS_AREA]->(ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)
                  OPTIONAL MATCH (s)-[:DEPICTS]->(a:Asset)
                  RETURN p.name AS plant, ar.name AS area, sy.name AS system, s.name AS sheet,
                         s.revision AS revision, s.file AS file, count(a) AS assets, size(s.issues) AS issues,
                         s.pdfStatus AS pdfStatus, s.pdfFile AS pdfFile, s.pdfOpenCandidates AS cands,
                         s.unconnectedEquipment AS loose
                  ORDER BY area, system, sheet""")
    for s in sheets:
        s["pdf check"] = ({"ok": f"yes ({s['pdfFile']})", "mismatch": "PDF does not match"}
                          .get(s.pop("pdfStatus"), "no"))
        s.pop("pdfFile")
        s["open candidates"] = s.pop("cands") or 0
        s["unconnected equipment"] = s.pop("loose") or 0
    registered = store.registry()
    if sheets:
        st.dataframe(pd.DataFrame(sheets), hide_index=True, use_container_width=True)
        st.caption("assets: the main equipment and off-page connectors Neo4j holds for the sheet (conceptual level). "
                   "The complete graph of each sheet is kept as a file in snapshots/.")
        with st.expander("Remove a sheet"):
            target = st.selectbox("Sheet", [s["sheet"] for s in sheets])
            st.caption("Removes the sheet's nodes and links from Neo4j, its snapshot and its saved source files. "
                       "Assets also shown on other sheets stay.")
            if st.button(f"Remove sheet {target}"):
                store.remove_sheet(target)
                st.session_state["plant_flash"] = ("ok", f"Sheet {target} removed.")
                st.rerun()
    elif not registered:
        st.info("No sheets loaded yet. Use tab 1 or 2.")

    old_db = q("""MATCH (n) WHERE (n:Asset AND NOT n:Equipment AND NOT n:Boundary) OR n:ManualEdit OR n:OpenEnd
                  RETURN count(n) AS n""")[0]["n"]
    with st.expander("Rebuild graph", expanded=bool(old_db) or (bool(registered) and not sheets)):
        if old_db:
            st.warning(f"Neo4j still holds {old_db} nodes of the former complete graph (nozzles, pipe items, "
                       "instruments, manual edits). Rebuild to keep only the conceptual level.")
        st.caption(f"Resets Neo4j and loads every sheet again from its saved source files in sources/ "
                   f"({len(registered)} sheet{'s' if len(registered) != 1 else ''}; sheets loaded before sources/ "
                   "existed are picked up from the uploads folder). Approved and rejected drawing candidates are "
                   "kept (sources/decisions.json).")
        if st.button("Rebuild graph", type="primary", icon=":material/refresh:"):
            bar = st.progress(0.0)
            with st.spinner("Rebuilding…"):
                st.session_state["rebuilt"] = store.rebuild(progress=bar.progress)
            st.rerun()   # so the counts and the sheet list above show the rebuilt database
        if st.session_state.get("rebuilt"):
            results, problems = st.session_state["rebuilt"]
            st.success(f"Rebuilt {len(results)} sheet(s). Neo4j holds the conceptual level only.")
            load_report(results, [("ok" if r["pdf"].get("pdfStatus") == "ok" else "error",
                                   f"**{r['sheet']}** — {r['pdfLine']}") for r in results if r.get("pdf")])
            for p in problems:
                st.error(p)
        labels, rels = ps.label_counts(db)
        st.caption("Now in Neo4j: nodes by label and relationships by type (as Neo4j Browser counts them).")
        c1, c2 = st.columns(2)
        c1.dataframe(pd.DataFrame(labels, columns=["label", "n"]), hide_index=True, use_container_width=True)
        c2.dataframe(pd.DataFrame(rels, columns=["type", "n"]), hide_index=True, use_container_width=True)
    if sheets or registered:
        with st.expander("Delete the whole plant graph"):
            keep = st.checkbox("Keep my review decisions (approved / rejected drawing candidates)", value=True,
                               help="Kept in sources/decisions.json and applied when the sheets are loaded again.")
            sure = st.checkbox("I understand this deletes every sheet: Neo4j, snapshots and saved source files")
            if st.button("Delete everything", disabled=not sure):
                store.delete_all(keep_decisions=keep)
                st.session_state["plant_flash"] = ("ok", "Plant graph deleted" +
                                                   ("; review decisions kept." if keep else ", review decisions too."))
                st.rerun()

# --------------------------------------------------------------------------- 4 graph

with tab_graph:
    st.subheader("Graph view")
    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        mode = c1.radio("Show", ["Around an asset", "Downstream of an asset", "One sheet", "Whole plant"],
                        horizontal=True, index=3)                                 # opens on the whole plant
        c2.radio("Layout", list(LAYOUTS), horizontal=True, key="layout",
                 index=list(LAYOUTS).index("Flow (left → right)"))
        c3, c4 = st.columns([3, 2])
        level = c3.radio("Detail level", gl.LEVELS, horizontal=True, key=f"graph_level_{mode}",
                         index=gl.LEVELS.index(DEFAULT_LEVEL[mode]),
                         help="Conceptual (Neo4j): main equipment and off-page connectors, flow through valves and "
                              "fittings. Process and Complete are computed from the sheets' file snapshots: Process "
                              "shows equipment, valves, off-page connectors and instrument loops; Complete every "
                              "object.")
        show_candidates = c4.checkbox("Show drawing candidates", key="graph_candidates",
                                      help="Links the drawing check proposes that are not reviewed yet (dashed "
                                           "orange). They are not in Neo4j, so the view is computed from the file "
                                           "snapshots. Review them in tab 6.")
        show_loops = level == "Conceptual" and not show_candidates and c4.checkbox(
            "Show loops", key="graph_loops", help="The Loop nodes and the valves they drive, with their MEASURES and "
                                                  "CONTROLS links (dotted). Otherwise loops are listed on the equipment.")
    sheet = None
    if mode == "One sheet":
        names = list(store.registry())
        if names:
            sheet = st.selectbox("Sheet", names, key="graph_sheet")   # tab 3 has a "Sheet" box with the same list
    open_cands = [c for c in store.drawing_links() if c["status"] == "candidate"]
    if open_cands and not show_candidates:
        loose = store.unconnected()
        st.warning(f"{len(open_cands)} drawing candidate{'s' if len(open_cands) != 1 else ''} not reviewed yet "
                   f"({', '.join(sorted({c['from_name'] + ' – ' + c['to_name'] for c in open_cands}))}). Until you "
                   "approve them in tab 6 they are not in the graph, so it can look split"
                   + (f"; equipment with no flow now: {', '.join(loose)}" if loose else "")
                   + ". Tick *Show drawing candidates* to see them dashed.")
    if level == "Conceptual" and not show_candidates:
        plant_view, origin = ps.conceptual_view(db, loops=show_loops), "Neo4j"
        st.caption("Conceptual level, from Neo4j. Click an item to see what was folded into it, and a link to see the "
                   "items (via) and pipe lines it passes; **Show detail** opens the Process view of that path.")
    else:
        plant_view = store.level_view(level, [sheet] if sheet else None, candidates=show_candidates)
        origin = ps.FROM_SNAPSHOT
        st.caption(f"{level} level, {ps.FROM_SNAPSHOT}" + (" (sheet " + sheet + " only)" if sheet else "") +
                   ". Click an item to see what was folded into it, and a link to see what it passes.")
    rep = plant_view["representative"]
    uids, focus, view = None, None, {"mode": mode}
    if mode == "Whole plant":
        uids = sorted(plant_view["nodes"])[:1500]
        if len(plant_view["nodes"]) > 1500:
            st.warning("Large plant: showing the first 1,500 items. Use the other views to focus.")
    elif mode == "One sheet":
        if sheet:
            uids = [u for u, n in plant_view["nodes"].items() if sheet in n.get("sheets", [])]
            view["sheet"] = sheet
    else:
        c1, c2 = st.columns([3, 1])
        with c1:
            picked = pick_node("Asset", f"graph_asset_{level}", plant_view)
        focus = rep.get(picked) if picked else None
        if picked and not focus:
            st.info(f"This item is not part of the {level} level. Pick another one, or the Complete level.")
        elif picked and focus != picked:
            st.caption(f"Shown at this level as **{plant_view['nodes'][focus]['name']}**.")
        if focus and mode == "Around an asset":
            hops = c2.slider("Steps out", 1, 6, 2)
            uids = gl.neighbourhood(plant_view, focus, hops)
            view["steps"] = hops
        elif focus:
            uids = gl.downstream(plant_view, focus)
        if focus:
            view["asset"] = plant_view["nodes"][focus]["name"]
            view["assetUid"] = focus
    if uids is not None:
        query = vq.for_view(plant_view, gl.subset(plant_view, uids), view, level, origin, loops=show_loops) if uids else ""
        draw(plant_view, uids, focus, level=level, origin=origin, cypher=query)
        if query:
            with st.expander("Neo4j query for this view (also the database button in the graph's toolbar)"):
                st.caption("Paste into Neo4j Browser to see the same thing there. " + (
                    "A live query on the conceptual graph stored in Neo4j." if level == "Conceptual" and origin == "Neo4j"
                    else "Process / Complete aren't stored in Neo4j, so the query carries this view and draws it "
                         "with APOC virtual nodes; nothing is written.")
                    + " Turn off *Connect result nodes* in Browser settings for a clean picture.")
                st.code(query, language="cypher")
        if level == "Conceptual" and origin == "Neo4j":
            show_link_detail(plant_view)
        if uids:
            # exactly what this view shows, as a directed adjacency list, for validation in other tools
            export = {k: v for k, v in gl.subset(plant_view, uids).items() if k not in ("representative", "loopTargets")}
            export["view"] = {**view, "level": level, "origin": origin}
            slug = "-".join(str(view[k]) for k in ("mode", "sheet", "asset", "steps") if k in view) + "-" + level
            slug = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in slug.replace(" ", "-"))
            st.download_button(
                f"Download adjacency list (JSON, {level}) — {export['counts']['nodes']} items, "
                f"{export['counts']['relationships']} relationships",
                json.dumps(export, indent=2, ensure_ascii=False, default=str),
                file_name=f"adjacency_{slug}_{export['exported'][:16].replace(':', '')}.json",
                mime="application/json", key="graph_export",
                help="The items and links in the view above, at the chosen detail level ('level' and 'origin' in "
                     "the header). nodes: uid → properties; adjacency: uid → outgoing links (to, type, properties: "
                     "via, lines, …).")

# --------------------------------------------------------------------------- 5 ask

QUESTIONS = [
    "Which equipment and valves are downstream of this asset (across sheets)?",
    "If this control valve closes, which equipment loses supply?",
    "Which sheets show this asset?",
    "How are the sheets connected to each other?",
    "Which sheets are not connected to any other sheet?",
    "Which control loops are in the plant?",
    "List all equipment by area and system",
]

with tab_ask:
    st.subheader("Ask the plant")
    question = st.selectbox("Question", QUESTIONS)
    rows = None
    # connection questions use the Process level (from the snapshots): answers list equipment and valves
    if question in QUESTIONS[:2]:
        process = store.level_view("Process")
        names = {u: n["name"] for u, n in process["nodes"].items()}
    if question == QUESTIONS[0]:
        start = pick_node("Asset", "ask_down", process, lambda n: n.get("group") in gl.FLOW_GROUPS)
        if start:
            rows = [{"item": names[u], "type": process["nodes"][u]["type"],
                     "kind": process["nodes"][u]["group"].replace("_", " "),
                     "on_sheets": process["nodes"][u].get("sheets", [])}
                    for u in sorted(gl.downstream(process, start) - {start}, key=names.get)]
    elif question == QUESTIONS[1]:
        start = pick_node("Control valve", "ask_valve", process, lambda n: n.get("valveRole") == "actuated")
        if start:
            hit = sorted(names[u] for u in gl.downstream(process, start) - {start}
                         if process["nodes"][u]["group"] == "main_equipment")
            rows = [{"valve": names[start], "affected_equipment": hit}] if hit else []
    elif question == QUESTIONS[2]:
        concept = ps.conceptual_view(db, loops=True)
        a = pick_node("Asset", "ask_sheets", concept)
        if a:
            rows = q("""MATCH (s:Sheet)-[:DEPICTS]->(a {uid: $u})
                        MATCH (sy:System)-[:HAS_SHEET]->(s) MATCH (ar:Area)-[:HAS_SYSTEM]->(sy)
                        RETURN a.name AS asset, s.name AS sheet, s.revision AS revision,
                               ar.name AS area, sy.name AS system ORDER BY sheet""", u=a)
    elif question == QUESTIONS[3]:
        rows = q("""MATCH (a:Asset)-[f:FLOWS_TO {crossSheet: true}]->(b:Asset)
                    UNWIND a.sheets AS s1 UNWIND b.sheets AS s2 WITH a, b, f, s1, s2 WHERE s1 <> s2
                    RETURN DISTINCT s1 AS from_sheet, s2 AS to_sheet,
                           'flow ' + a.name + ' → ' + b.name + coalesce(' (line ' + f.lines[0] + ')', '') AS connection
                    UNION
                    MATCH (s1:Sheet)-[:DEPICTS]->(a:Asset)<-[:DEPICTS]-(s2:Sheet)
                    WHERE s1.name < s2.name
                    RETURN s1.name AS from_sheet, s2.name AS to_sheet, 'shared tag ' + a.name AS connection""")
    elif question == QUESTIONS[4]:
        rows = q("""MATCH (s:Sheet)
                    OPTIONAL MATCH (s)-[:DEPICTS]->(:Asset)-[:FLOWS_TO {crossSheet: true}]-(:Asset)<-[:DEPICTS]-(o1:Sheet)
                    WHERE o1 <> s
                    OPTIONAL MATCH (s)-[:DEPICTS]->(:Asset)<-[:DEPICTS]-(o2:Sheet) WHERE o2 <> s
                    WITH s, count(o1) + count(o2) AS links WHERE links = 0
                    RETURN s.name AS isolated_sheet ORDER BY isolated_sheet""")
    elif question == QUESTIONS[5]:
        rows = q("""MATCH (l:Loop)
                    OPTIONAL MATCH (l)-[:MEASURES]->(m)
                    OPTIONAL MATCH (l)-[:CONTROLS]->(c)
                    RETURN l.sheets AS sheets, l.tag AS loop, l.functions AS functions, l.alarms AS alarms,
                           collect(DISTINCT m.name) AS measures, collect(DISTINCT c.name) AS controls
                    ORDER BY loop""")
    elif question == QUESTIONS[6]:
        rows = q("""MATCH (ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)-[:DEPICTS]->(e:Asset:Equipment)
                    RETURN ar.name AS area, sy.name AS system, e.name AS equipment, e.type AS type,
                           collect(DISTINCT s.name) AS sheets ORDER BY area, system, equipment""")
    if rows is not None:
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.info("No results.")

# --------------------------------------------------------------------------- 6 validation

with tab_check:
    st.subheader("Validation report")
    st.caption("Things to fix or confirm in the P&IDs: off-page arrows with no matching sheet, "
               "untagged equipment, and tags shared by several sheets (from the sheets' file snapshots).")
    findings = store.report("output")
    if findings:
        df = pd.DataFrame(findings)
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False), "validation_report.csv", "text/csv")
    else:
        st.success("No findings.")

    st.markdown("#### Islands")
    plant_export = store.export()
    groups = check_islands.components(plant_export)
    st.caption(f"{plant_export['counts']['nodes']} items of the complete graph in {len(groups)} connected groups, "
               f"{sum(len(g) == 1 for g in groups)} of them single items (drawing candidates not counted until "
               "approved). Every group of 3 items or fewer is listed below: usually a link the loader does not read "
               "yet, or a gap in the drawing itself. Same check as `python check_islands.py <adjacency export>.json`.")
    small = check_islands.small_islands(plant_export, 3)
    if small:
        st.dataframe(pd.DataFrame(small)[["island", "size", "type", "name", "sheets"]], hide_index=True,
                     use_container_width=True)
    elif groups:
        st.success("No islands of 3 items or fewer.")

    st.markdown("#### Drawing check")
    st.caption("Each sheet's DEXPI pipe links checked against the pipes drawn in the vector PDF of the same sheet "
               "(add it next to the DEXPI file in tab 1). DEXPI stays the source; the PDF confirms links, proposes "
               "missing ones and flags links that are not drawn. Same check as "
               "`python -m crosscheck.pdf_vs_dexpi <pdf> <dexpi>`.")
    if st.session_state.get("pdf_flash"):
        st.success(st.session_state.pop("pdf_flash"))
    checks = store.pdf_checks()
    pdf_rows = [c for c in checks if c["pdfStatus"]]
    without = [c["sheet"] for c in checks if not c["pdfStatus"]]
    if without:
        st.caption("Loaded without drawing check: " + ", ".join(without))
    if pdf_rows:
        st.dataframe(pd.DataFrame([{
            "sheet": c["sheet"], "PDF": c["pdfFile"],
            "result": "checked" if c["pdfStatus"] == "ok" else "PDF does not match",
            "tag match": f"{c['pdfTagScore']:.0%} ({c['pdfTagsMatched']} of {c['pdfTagsTotal']})",
            "pipe width (pt)": ", ".join(f"{w:g}" for w in c["pdfWidths"] or []),
            "confirmed": c["pdfConfirmed"], "drawn only": c["pdfDrawnOnly"], "not drawn": c["pdfNotDrawn"],
            "open candidates": c["openCandidates"]} for c in pdf_rows]), hide_index=True, use_container_width=True)
        for c in pdf_rows:
            if c["pdfStatus"] == "mismatch":
                st.error(f"{c['sheet']} with {c['pdfFile']}: {c['pdfMessage']}")
    ok_rows = [c for c in pdf_rows if c["pdfStatus"] == "ok"]
    if ok_rows:
        with st.expander("Pipe line width calibration"):
            st.caption("Segment ends per stroke width that lie on a DEXPI pipe route. Every width with at least 25% of "
                       "the best score is read as pipe.")
            for c in ok_rows:
                st.markdown(f"**{c['sheet']}**: " + ", ".join(
                    f"{w} pt: {n}" for w, n in json.loads(c["pdfCalibration"] or "{}").items()))

    st.markdown("**Drawn only: in the PDF, not on a DEXPI pipe segment**")
    st.caption("A candidate is not in Neo4j until you approve it (tab 4 shows it dashed orange with *Show drawing "
               "candidates*). Reject removes it, and it isn't proposed again. A pair the loader already links (also "
               "from the drawing geometry: items drawn against each other) is only marked as confirmed. Decisions are "
               "kept in sources/decisions.json and survive reloads and rebuilds.")
    links = store.drawing_links()
    decisions = store.decisions()
    if not links:
        st.success("No drawing candidates to review.")
    for link in links:
        cols = st.columns([4, 4, 2, 1, 1])
        cols[0].markdown(f"**{link['from_name']}**  \n{link['from_class']} · `{link['from_label'] or '-'}`")
        cols[1].markdown(f"**{link['to_name']}**  \n{link['to_class']} · `{link['to_label'] or '-'}`")
        cols[2].markdown(f"{link['sheet']}  \n*{link['status']}*")
        key = pdf_store.pair_key(link["src"], link["dst"])
        if cols[3].button("Approve", key=f"pdf_ok_{key}", disabled=link["status"] == "approved", type="primary"):
            store.approve(link["src"], link["dst"])
            st.session_state["pdf_flash"] = f"Approved {link['from_name']} — {link['to_name']}."
            st.rerun()
        if cols[4].button("Reject", key=f"pdf_no_{key}"):
            store.reject(link["src"], link["dst"])
            st.session_state["pdf_flash"] = f"Rejected {link['from_name']} — {link['to_name']}."
            st.rerun()
    rejected = [k for k, v in decisions.items() if v.get("decision") == "rejected"]
    if rejected:
        st.caption(f"Rejected before (not proposed again): {len(rejected)}")

    if ok_rows:
        st.markdown("**In DEXPI but not drawn**")
        not_drawn = [{"sheet": c["sheet"], **{k: v for k, v in p.items() if k not in ("src", "dst")}}
                     for c in ok_rows for p in store.describe_pairs(c["pdfNotDrawnPairs"], c["sheet"])]
        if not_drawn:
            st.dataframe(pd.DataFrame(not_drawn), hide_index=True, use_container_width=True)
        else:
            st.success("Every DEXPI pipe link is drawn.")
        pick = st.selectbox("Overlay for sheet", [c["sheet"] for c in ok_rows], key="pdf_overlay_sheet")
        c = next(c for c in ok_rows if c["sheet"] == pick)
        if os.path.exists(c["pdfPath"] or "") and os.path.exists(c["pdfDexpiPath"] or ""):
            st.image(pdf_overlay(c["pdfPath"], c["pdfDexpiPath"], c["pdfChecked"]), use_container_width=True,
                     caption="Green: in DEXPI and drawn. Red: drawn only. Orange: in DEXPI, not drawn. "
                             "Boxes: the DEXPI objects these links join.")
        else:
            st.warning("The PDF or DEXPI file of this check is no longer there; load them again in tab 1 to see the "
                       "overlay.")

    st.markdown("#### Unclassified types")
    st.caption("Types the graph view's detail levels don't know yet (graph_levels.yaml). Category Equipment counts as "
               "main equipment, anything else as a pipe fitting that is collapsed at the Process and Conceptual "
               "levels. Add each type to the right group in graph_levels.yaml, then rebuild (tab 3).")
    loose = gl.unclassified(plant_export)
    if loose:
        st.dataframe(pd.DataFrame(loose), hide_index=True, use_container_width=True)
    else:
        st.success("Every type in the graph is listed in graph_levels.yaml.")
