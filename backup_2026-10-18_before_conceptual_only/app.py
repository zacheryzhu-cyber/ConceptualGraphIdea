"""
P&ID Knowledge Graph — web interface for plant_loader.py.

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
from crosscheck import pdf_vs_dexpi as pdf_check
from crosscheck import store as pdf_store
import graph_view
import plant_loader as pl
import ui_style as ui

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
    pl.classify_valves(db)  # sheets loaded before the valve rule existed get it now
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


def q(query, **params):
    return db.run(query, **params)


def stats():
    one = lambda s: q(s)[0]["n"]
    return {
        "Sheets": one("MATCH (s:Sheet) RETURN count(s) AS n"),
        "Assets": one("MATCH (a:Asset) RETURN count(a) AS n"),
        "Equipment": one("MATCH (a:Equipment) RETURN count(a) AS n"),
        "Cross-sheet links": one(pl.CROSS_SHEET_COUNT),
    }


def show_stats():
    ui.stat_cards(stats())


def asset_options(where=""):
    rows = q(f"""MATCH (a:Asset) {where}
                 RETURN a.uid AS uid, a.name AS name, a.category AS cat, a.sheet AS sheet
                 ORDER BY a.category, a.name""")
    order = {"Equipment": 0, "Instrumentation": 1, "Piping": 2}
    rows.sort(key=lambda r: (order.get(r["cat"], 3), r["name"]))
    return {r["uid"]: (r["name"] if not r["sheet"] else f"{r['name']}  (sheet {r['sheet']})") for r in rows}


def pick_asset(label, key, where=""):
    opts = asset_options(where)
    if not opts:
        st.info("No matching assets loaded yet.")
        return None
    return st.selectbox(label, list(opts), format_func=opts.get, key=key)


def graph_edit_options():
    """Choices offered by the graph view's edit form and Connect bar."""
    rels = {r["t"] for r in q("MATCH (:Asset)-[r]->(:Asset) RETURN DISTINCT type(r) AS t")} | {"PIPE"}
    types = {r["t"] for r in q("MATCH (a:Asset) RETURN DISTINCT a.type AS t") if r["t"]}
    cats = {r["c"] for r in q("MATCH (a:Asset) RETURN DISTINCT a.category AS c") if r["c"]}
    return {"relTypes": sorted(rels), "assetTypes": sorted(types),
            "categories": sorted(cats | {"Equipment", "Instrumentation", "Piping"})}


def apply_graph_edit(ev):
    """Save a change made on the graph view as a manual edit (kept across reloads, undo in tab 7).
    Returns (message, "ok" | "error")."""
    note = "edited on the graph view"
    action = ev.get("action")
    uids = [u for u in (ev.get("uid"), ev.get("src"), ev.get("dst")) if u]
    names = {r["uid"]: r["name"] for r in q("MATCH (a:Asset) WHERE a.uid IN $u RETURN a.uid AS uid, a.name AS name",
                                            u=uids)}
    try:
        if action == "set_asset":
            old = names.get(ev["uid"], "the asset")
            if pl.set_asset_props(db, ev["uid"], {k: ev.get(k) for k in pl.ASSET_FIELDS}, note) is None:
                return "Nothing to change.", "ok"
            return f"Saved changes to {old}" + (f" (now {ev['name']})." if ev.get("name") != old else "."), "ok"
        if action == "add_rel":
            if ev["src"] == ev["dst"]:
                return "Pick a different item to connect to.", "error"
            pl.add_manual_edit(db, "add", ev["src"], ev["dst"], ev["rel"], {}, note)
            return f"Connected {names.get(ev['src'])} -[{pl.clean_rel_type(ev['rel'])}]-> {names.get(ev['dst'])}.", "ok"
        if action in ("delete_rel", "reverse_rel"):
            rel = next((r for r in pl.relationships_of(db, ev["src"])
                        if r["src"] == ev["src"] and r["dst"] == ev["dst"] and r["type"] == ev["rel"]), None)
            if not rel:
                return "That connection is no longer there.", "error"
            what = f"{rel['src_name']} -[{rel['type']}]-> {rel['dst_name']}"
            if action == "delete_rel":
                pl.delete_relationship(db, rel, note)
                return f"Deleted {what}.", "ok"
            pl.change_relationship(db, rel, rel["type"], reverse=True, line_number=rel["props"].get("lineNumber"),
                                   note=note)
            return f"Reversed {what}.", "ok"
    except ValueError as exc:
        return str(exc), "error"
    return f"Unknown change: {action}", "error"


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


def draw(uids, focus=None, height=650, key="graph_main", view=None, level="Complete"):
    """Draw `uids` of a level graph (graph_levels.build), or of the stored graph when view is None.
    Editing works on the stored graph, so it is offered at the Complete level only."""
    uids = list(uids)
    if not uids:
        st.info("Nothing to show.")
        return
    view = gl.subset(view, uids) if view is not None else pl.export_adjacency(db, uids)
    nodes, edges = view_rows(view)
    layout = st.session_state.get("layout", "Flow (left → right)")
    # legend, hover details, search, inspector and editing are all inside the graph view itself
    event = graph_view.show(nodes, edges, focus, layout, height, pl.OFFPAGE_TYPES, key=key,
                            edit=graph_edit_options() if level == "Complete" else None,
                            flash=st.session_state.pop(f"{key}_flash", None), level=level)
    # the page sends each change once, but Streamlit returns it again on every rerun: act on a new nonce only
    if event and event.get("nonce") != st.session_state.get(f"{key}_nonce"):
        st.session_state[f"{key}_nonce"] = event["nonce"]
        text, kind = apply_graph_edit(event)
        st.session_state[f"{key}_flash"] = {"text": text, "kind": kind, "id": event["nonce"]}
        st.rerun()


tab_load, tab_pdf, tab_plant, tab_graph, tab_ask, tab_check, tab_edit = st.tabs(
    ["1 · Load DEXPI", "2 · Load PDF / image", "3 · Plant", "4 · Graph", "5 · Ask", "6 · Validation", "7 · Edit"])

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
                                 "number or label; otherwise pick it in the table.")
    links_file = st.file_uploader("Manual off-page links (optional links.csv)", type=["csv"],
                                  help="Columns: from_sheet,from_id,to_sheet,to_id — only for arrows "
                                       "whose line numbers don't match.")
    if files:
        loaded = {r["name"] for r in q("MATCH (s:Sheet) RETURN s.name AS name")}
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
            links_path = None
            if links_file:
                links_path = os.path.join(UPLOAD_DIR, "links.csv")
                with open(links_path, "wb") as fh:
                    fh.write(links_file.getvalue())
            pdf_by_name = {p.name: p for p in pdfs or []}
            results, pdf_lines = [], []
            bar = st.progress(0.0)
            for i, row in enumerate(edited.to_dict("records")):
                path = os.path.join(UPLOAD_DIR, row["file"])
                with open(path, "wb") as fh:
                    fh.write(by_name[row["file"]].getvalue())
                sheet = str(row["sheet"]).strip()
                try:
                    r = pl.load_sheet(db, path, sheet, plant.strip() or pl.DEFAULT_PLANT,
                                      row["area"] or None, row["system"] or None, row["revision"] or None)
                    results.append({"sheet": sheet, "result": "loaded", "assets": r["assets"],
                                    "connections": r["connections"], "issues": len(r["issues"]), "pdf check": ""})
                except Exception as exc:
                    results.append({"sheet": sheet, "result": f"failed: {exc}", "assets": 0,
                                    "connections": 0, "issues": 0, "pdf check": ""})
                if results[-1]["result"] == "loaded" and row.get("pdf") in pdf_by_name:
                    pdf_path = os.path.join(UPLOAD_DIR, row["pdf"])
                    with open(pdf_path, "wb") as fh:
                        fh.write(pdf_by_name[row["pdf"]].getvalue())
                    try:
                        line = pdf_store.summary_line(pdf_store.run(db, sheet, path, pdf_path, row["pdf"]))
                        pdf_lines.append(("ok", f"**{sheet}** — {line}"))
                    except pdf_check.PdfMismatch as exc:
                        line = f"not run: {exc}"
                        pdf_lines.append(("error", f"**{sheet}** with {row['pdf']} — {exc}"))
                    except Exception as exc:
                        line = f"failed: {exc}"
                        pdf_lines.append(("error", f"**{sheet}** — PDF check failed: {exc}"))
                    results[-1]["pdf check"] = line
                bar.progress((i + 1) / len(edited))
            links = pl.link_sheets(db, links_path)
            st.success(f"Done. {sum(r['result'] == 'loaded' for r in results)} sheet(s) loaded, "
                       f"{links} cross-sheet link(s) in the plant.")
            for kind, text in pdf_lines:
                (st.info if kind == "ok" else st.error)(text)
            st.dataframe(pd.DataFrame(results), hide_index=True, use_container_width=True)
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
            r = pl.write_sheet(db, res["sheet"], assets, edges, issues,
                               (existing_plants[0] if existing_plants else pl.DEFAULT_PLANT),
                               p_area or None, p_system or None, p_rev or None, res["file"], source="pdf")
            pl.link_sheets(db, os.path.join(UPLOAD_DIR, "links.csv"))
            st.success(f"Sheet {res['sheet']} added: {r['assets']} assets, {r['connections']} connections. "
                       "See tabs 3 to 5.")

# --------------------------------------------------------------------------- 3 plant

with tab_plant:
    st.subheader("Plant overview")
    show_stats()
    sheets = q("""MATCH (p:Plant)-[:HAS_AREA]->(ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)
                  OPTIONAL MATCH (s)-[:DEPICTS]->(a:Asset)
                  RETURN p.name AS plant, ar.name AS area, sy.name AS system, s.name AS sheet,
                         s.revision AS revision, s.file AS file, count(a) AS assets, size(s.issues) AS issues
                  ORDER BY area, system, sheet""")
    checked = {c["sheet"]: c for c in pdf_store.checks(db)}
    for s in sheets:
        c = checked.get(s["sheet"], {})
        s["pdf check"] = ({"ok": f"yes ({c.get('pdfFile')})", "mismatch": "PDF does not match"}
                          .get(c.get("pdfStatus"), "no"))
        s["open candidates"] = c.get("openCandidates", 0)
    if sheets:
        st.dataframe(pd.DataFrame(sheets), hide_index=True, use_container_width=True)
        with st.expander("Remove a sheet"):
            target = st.selectbox("Sheet", [s["sheet"] for s in sheets])
            st.caption("Assets only on this sheet are removed. Assets also shown on other sheets stay.")
            if st.button(f"Remove sheet {target}"):
                pl.remove_sheet(db, target)
                pl.link_sheets(db, os.path.join(UPLOAD_DIR, "links.csv"))
                st.success(f"Sheet {target} removed.")
                st.rerun()
        with st.expander("Delete the whole plant graph"):
            n_edits = q("MATCH (e:ManualEdit) RETURN count(e) AS n")[0]["n"]
            with_edits = st.checkbox(f"Also delete my manual edits ({n_edits}: hand-added or hidden links, renames)",
                                     value=True,
                                     help="Untick to keep them: they are re-applied when the sheets are loaded again.")
            sure = st.checkbox("I understand this deletes every sheet and asset")
            if st.button("Delete everything", disabled=not sure):
                pl.delete_plant(db, keep_edits=not with_edits)
                st.success("Plant graph deleted" + (", manual edits too." if with_edits else "; manual edits kept."))
                st.rerun()
    else:
        st.info("No sheets loaded yet. Use tab 1 or 2.")

# --------------------------------------------------------------------------- 3 graph

with tab_graph:
    st.subheader("Graph view")
    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        mode = c1.radio("Show", ["Around an asset", "Downstream of an asset", "One sheet", "Whole plant"],
                        horizontal=True)
        c2.radio("Layout", list(LAYOUTS), horizontal=True, key="layout")
        c3, c4 = st.columns([3, 2])
        level = c3.radio("Detail level", gl.LEVELS, horizontal=True, key=f"graph_level_{mode}",
                         index=gl.LEVELS.index(DEFAULT_LEVEL[mode]),
                         help="Conceptual: main equipment and off-page connectors, flow through valves and fittings. "
                              "Process: equipment, valves, off-page connectors and instrument loops, fittings "
                              "collapsed. Complete: every stored object. Only the view changes, never the database.")
        show_candidates = c4.checkbox("Show drawing candidates", key="graph_candidates",
                                      help="Pipes the PDF drawing check found that are not reviewed yet (dashed "
                                           "orange). Approved ones are always shown. Review them in tab 6.")
    if level == "Complete":
        st.caption("Click an item to inspect it. **Edit** changes its name, category, type or description; hover a "
                   "connection to reverse or delete it; **Connect** links it to another item. Changes are saved as "
                   "manual edits: they survive reloading the sheet and can be undone in tab 7.")
    else:
        st.caption(f"{level} level, computed from the stored graph. Click an item to see what was folded into it, and "
                   "a link to see the items (via) and pipe lines it passes. Editing works at the Complete level.")
    plant_view = gl.build(pl.export_adjacency(db, candidates=show_candidates), level)
    rep = plant_view["representative"]
    uids, focus, view = None, None, {"mode": mode}
    if mode == "Whole plant":
        uids = sorted(plant_view["nodes"])[:1500]
        if len(plant_view["nodes"]) > 1500:
            st.warning("Large plant: showing the first 1,500 items. Use the other views to focus.")
    elif mode == "One sheet":
        names = [r["name"] for r in q("MATCH (s:Sheet) RETURN s.name AS name ORDER BY name")]
        if names:
            sheet = st.selectbox("Sheet", names, key="graph_sheet")   # tab 3 has a "Sheet" box with the same list
            uids = [u for u, n in plant_view["nodes"].items() if sheet in n.get("sheets", [])]
            view["sheet"] = sheet
    else:
        c1, c2 = st.columns([3, 1])
        with c1:
            picked = pick_asset("Asset", "graph_asset")
        focus = rep.get(picked) if picked else None
        if picked and not focus:
            st.info(f"{asset_options().get(picked, picked)} is not part of the {level} level (nothing it folds into "
                    "is on the pipes). Pick another asset, or the Complete level.")
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
        draw(uids, focus, view=plant_view, level=level)
        if uids:
            # exactly what this view shows, as a directed adjacency list, for validation in other tools
            export = {k: v for k, v in gl.subset(plant_view, uids).items() if k != "representative"}
            export["view"] = {**view, "level": level}
            slug = "-".join(str(view[k]) for k in ("mode", "sheet", "asset", "steps") if k in view) + "-" + level
            slug = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in slug.replace(" ", "-"))
            st.download_button(
                f"Download adjacency list (JSON, {level}) — {export['counts']['nodes']} items, "
                f"{export['counts']['relationships']} relationships",
                json.dumps(export, indent=2, ensure_ascii=False, default=str),
                file_name=f"adjacency_{slug}_{export['exported'][:16].replace(':', '')}.json",
                mime="application/json", key="graph_export",
                help="The items and links in the view above, at the chosen detail level ('level' in the header). "
                     "nodes: uid → properties; adjacency: uid → outgoing links (to, type, properties: via, lines, "
                     "…). Hand edits included.")

# --------------------------------------------------------------------------- 4 ask

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
    # connection questions use the Process level: answers list equipment and valves, not tees and arrows
    if question in QUESTIONS[:2]:
        process = gl.build(pl.export_adjacency(db), "Process")
        names = {u: n["name"] for u, n in process["nodes"].items()}
    if question == QUESTIONS[0]:
        a = pick_asset("Asset", "ask_down")
        start = process["representative"].get(a) if a else None
        if a and not start:
            st.info("This asset is not on the pipe network (Process level).")
        elif start:
            rows = [{"item": names[u], "type": process["nodes"][u]["type"],
                     "kind": process["nodes"][u]["group"].replace("_", " "),
                     "on_sheets": process["nodes"][u].get("sheets", [])}
                    for u in sorted(gl.downstream(process, start) - {start}, key=names.get)]
    elif question == QUESTIONS[1]:
        a = pick_asset("Control valve", "ask_valve", "WHERE a.valveRole = 'actuated'")
        start = process["representative"].get(a) if a else None
        if start:
            hit = sorted(names[u] for u in gl.downstream(process, start) - {start}
                         if process["nodes"][u]["group"] == "main_equipment")
            rows = [{"valve": names[start], "affected_equipment": hit}] if hit else []
    elif question == QUESTIONS[2]:
        a = pick_asset("Asset", "ask_sheets")
        if a:
            rows = q("""MATCH (s:Sheet)-[:DEPICTS]->(a:Asset {uid: $u})
                        MATCH (sy:System)-[:HAS_SHEET]->(s) MATCH (ar:Area)-[:HAS_SYSTEM]->(sy)
                        RETURN a.name AS asset, s.name AS sheet, s.revision AS revision,
                               ar.name AS area, sy.name AS system ORDER BY sheet""", u=a)
    elif question == QUESTIONS[3]:
        rows = q("""MATCH (s1:Sheet)-[:DEPICTS]->(o:Asset)-[:CONTINUES_TO]->(i:Asset)<-[:DEPICTS]-(s2:Sheet)
                    RETURN s1.name AS from_sheet, s2.name AS to_sheet, 'off-page line ' + o.lineNumber AS connection
                    UNION
                    MATCH ()-[p:PIPE {crossSheet: true}]->()
                    RETURN p.fromSheet AS from_sheet, p.toSheet AS to_sheet, 'pipe line ' + p.line AS connection
                    UNION
                    MATCH (s1:Sheet)-[:DEPICTS]->(a:Asset {tagged: true})<-[:DEPICTS]-(s2:Sheet)
                    WHERE s1.name < s2.name
                    RETURN s1.name AS from_sheet, s2.name AS to_sheet, 'shared tag ' + a.name AS connection""")
    elif question == QUESTIONS[4]:
        rows = q("""MATCH (s:Sheet)
                    OPTIONAL MATCH (s)-[:DEPICTS]->(:Asset)-[:CONTINUES_TO]-(:Asset)<-[:DEPICTS]-(o1:Sheet)
                    WHERE o1 <> s
                    OPTIONAL MATCH (s)-[:DEPICTS]->(:Asset {tagged: true})<-[:DEPICTS]-(o2:Sheet)
                    WHERE o2 <> s
                    OPTIONAL MATCH ()-[p:PIPE {crossSheet: true}]->() WHERE s.name IN [p.fromSheet, p.toSheet]
                    WITH s, count(o1) + count(o2) + count(p) AS links WHERE links = 0
                    RETURN s.name AS isolated_sheet ORDER BY isolated_sheet""")
    elif question == QUESTIONS[5]:
        rows = q("""MATCH (sen:ProcessSignalGeneratingFunction)-[:MEASURING_LINE_FUNCTION]->(f:ProcessInstrumentationFunction)
                          -[:SIGNAL_CONVEYING_FUNCTION]->(act:ActuatingFunction)-[:OPERATED_VALVE_REFERENCE]->(v:Asset)
                    MATCH (s:Sheet)-[:DEPICTS]->(sen)
                    RETURN s.name AS sheet, sen.name AS sensor, f.name AS loop, act.name AS actuator, v.name AS valve
                    ORDER BY sheet, loop""")
    elif question == QUESTIONS[6]:
        rows = q("""MATCH (ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s:Sheet)-[:DEPICTS]->(e:Equipment)
                    RETURN ar.name AS area, sy.name AS system, e.name AS equipment, e.type AS type,
                           collect(DISTINCT s.name) AS sheets ORDER BY area, system, equipment""")
    if rows is not None:
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.info("No results.")

# --------------------------------------------------------------------------- 5 validation

with tab_check:
    st.subheader("Validation report")
    st.caption("Things to fix or confirm in the P&IDs: off-page arrows with no matching sheet, "
               "untagged equipment, and tags shared by several sheets.")
    findings = pl.report(db, "output")
    if findings:
        df = pd.DataFrame(findings)
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False), "validation_report.csv", "text/csv")
    else:
        st.success("No findings.")

    st.markdown("#### Islands")
    plant_export = pl.export_adjacency(db)
    groups = check_islands.components(plant_export)
    st.caption(f"{plant_export['counts']['nodes']} assets in {len(groups)} connected groups, "
               f"{sum(len(g) == 1 for g in groups)} of them single assets. Every group of 3 assets or fewer is listed "
               "below: usually a link the loader does not read yet, or a gap in the drawing itself. "
               "Same check as `python check_islands.py <adjacency export>.json`.")
    small = check_islands.small_islands(plant_export, 3)
    if small:
        st.dataframe(pd.DataFrame(small)[["island", "size", "type", "name", "sheets"]], hide_index=True,
                     use_container_width=True)
    elif groups:
        st.success("No islands of 3 assets or fewer.")

    st.markdown("#### Drawing check")
    st.caption("Each sheet's DEXPI pipe links checked against the pipes drawn in the vector PDF of the same sheet "
               "(add it next to the DEXPI file in tab 1). DEXPI stays the source; the PDF confirms links, proposes "
               "missing ones and flags links that are not drawn. Same check as "
               "`python -m crosscheck.pdf_vs_dexpi <pdf> <dexpi>`.")
    if st.session_state.get("pdf_flash"):
        st.success(st.session_state.pop("pdf_flash"))
    pdf_rows = [c for c in pdf_store.checks(db) if c["pdfStatus"]]
    if not pdf_rows:
        st.info("No sheet has a PDF check yet.")
    else:
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
        with st.expander("Pipe line width calibration"):
            st.caption("Segment ends per stroke width that lie on a DEXPI pipe route. Every width with at least 25% of "
                       "the best score is read as pipe.")
            for c in ok_rows:
                st.markdown(f"**{c['sheet']}**: " + ", ".join(
                    f"{w} pt: {n}" for w, n in json.loads(c["pdfCalibration"] or "{}").items()))
        open_links = {pdf_store.pair_key(r["src"], r["dst"]): r for r in pdf_store.drawing_links(db)}

        st.markdown("**Drawn only: in the PDF, not on a DEXPI pipe segment**")
        st.caption("A candidate stays out of the views until you approve it (tab 4 shows it dashed orange with "
                   "*Show drawing candidates*). Reject deletes it, and it isn't proposed again for that sheet. "
                   "A pair the loader already links from the drawing geometry is only marked as confirmed.")
        for c in ok_rows:
            for i, p in enumerate(pdf_store.describe_pairs(db, c["pdfDrawnOnlyPairs"])):
                key = pdf_store.pair_key(p["src"], p["dst"])
                link = open_links.get(key)
                state = (link["status"] if link else
                         "rejected" if key in (c["pdfRejected"] or []) else "already linked by the loader")
                cols = st.columns([4, 4, 2, 1, 1])
                cols[0].markdown(f"**{p['item A']}**  \n{p['class A']} · `{p['full label A'] or '-'}`")
                cols[1].markdown(f"**{p['item B']}**  \n{p['class B']} · `{p['full label B'] or '-'}`")
                cols[2].markdown(f"{c['sheet']}  \n*{state}*")
                if link:
                    if cols[3].button("Approve", key=f"pdf_ok_{key}", disabled=state == "approved", type="primary"):
                        pdf_store.approve(db, c["sheet"], link["src"], link["dst"])
                        st.session_state["pdf_flash"] = f"Approved {p['item A']} — {p['item B']}."
                        st.rerun()
                    if cols[4].button("Reject", key=f"pdf_no_{key}"):
                        pdf_store.reject(db, c["sheet"], link["src"], link["dst"])
                        st.session_state["pdf_flash"] = f"Rejected {p['item A']} — {p['item B']}."
                        st.rerun()
        if not any(c["pdfDrawnOnly"] for c in ok_rows):
            st.success("Nothing drawn that DEXPI misses.")

        st.markdown("**In DEXPI but not drawn**")
        not_drawn = [{"sheet": c["sheet"], **{k: v for k, v in p.items() if k not in ("src", "dst")}}
                     for c in ok_rows for p in pdf_store.describe_pairs(db, c["pdfNotDrawnPairs"])]
        if not_drawn:
            st.dataframe(pd.DataFrame(not_drawn), hide_index=True, use_container_width=True)
        else:
            st.success("Every DEXPI pipe link is drawn.")

        if ok_rows:
            pick = st.selectbox("Overlay for sheet", [c["sheet"] for c in ok_rows], key="pdf_overlay_sheet")
            c = next(c for c in ok_rows if c["sheet"] == pick)
            if os.path.exists(c["pdfPath"] or "") and os.path.exists(c["pdfDexpiPath"] or ""):
                st.image(pdf_overlay(c["pdfPath"], c["pdfDexpiPath"], c["pdfChecked"]), use_container_width=True,
                         caption="Green: in DEXPI and drawn. Red: drawn only. Orange: in DEXPI, not drawn. "
                                 "Boxes: the DEXPI objects these links join.")
            else:
                st.warning("The PDF or DEXPI file of this check is no longer in the uploads folder; load them again "
                           "in tab 1 to see the overlay.")

    st.markdown("#### Unclassified types")
    st.caption("Types the graph view's detail levels don't know yet (graph_levels.yaml). Category Equipment counts as "
               "main equipment, anything else as a pipe fitting that is collapsed at the Process and Conceptual "
               "levels. Add each type to the right group in graph_levels.yaml.")
    loose = gl.unclassified(plant_export)
    if loose:
        st.dataframe(pd.DataFrame(loose), hide_index=True, use_container_width=True)
    else:
        st.success("Every type in the graph is listed in graph_levels.yaml.")

# --------------------------------------------------------------------------- 7 edit


def rel_text(r):
    origin = "added by hand" if r["props"].get("source") == "manual" else f"sheet {r['props'].get('sheet', '?')}"
    return f"{r['src_name']} -[{r['type']}]-> {r['dst_name']}   ({origin})"


def type_picker(label, key, current="PIPE"):
    """Relationship type: one already in the graph, or a new one typed in."""
    types = sorted({r["t"] for r in q("MATCH (:Asset)-[r]->(:Asset) RETURN DISTINCT type(r) AS t")} | {"PIPE", current})
    other = "Other (type a new one)…"
    choice = st.selectbox(label, types + [other], index=types.index(current), key=key)
    return st.text_input("New relationship type", key=key + "_new") if choice == other else choice


def flash(message):
    st.session_state["edit_flash"] = message
    st.rerun()


with tab_edit:
    st.subheader("Edit relationships by hand")
    st.caption("Use this for what the P&IDs don't show or show wrongly. Every edit is saved as a ManualEdit record "
               "and re-applied after each load, so it survives reloading a sheet. Changing or deleting a relationship "
               "that came from a sheet hides the original; Undo in the edit log brings it back.")
    if st.session_state.get("edit_flash"):
        st.success(st.session_state.pop("edit_flash"))

    st.markdown("#### Add a relationship")
    c1, c2, c3 = st.columns([3, 2, 3])
    with c1:
        add_src = pick_asset("From", "edit_add_src")
    with c2:
        add_type = type_picker("Type", "edit_add_type")
    with c3:
        add_dst = pick_asset("To", "edit_add_dst")
    c4, c5 = st.columns(2)
    add_line = c4.text_input("Line number (optional)", key="edit_add_line")
    add_note = c5.text_input("Note (optional)", key="edit_add_note", help="Why, e.g. 'bypass seen on site walk'.")
    if st.button("Add relationship", type="primary", disabled=not (add_src and add_dst and add_type)):
        if add_src == add_dst:
            st.error("From and To are the same asset.")
        else:
            try:
                pl.add_manual_edit(db, "add", add_src, add_dst, add_type,
                                   {"lineNumber": add_line} if add_line else {}, add_note)
                names = asset_options()
                flash(f"Added {names[add_src]} -[{pl.clean_rel_type(add_type)}]-> {names[add_dst]}.")
            except ValueError as exc:
                st.error(str(exc))

    st.markdown("#### Change or delete a relationship")
    focus = pick_asset("Asset", "edit_asset")
    if focus:
        rels = pl.relationships_of(db, focus)
        if not rels:
            st.info("This asset has no relationships.")
        else:
            i = st.selectbox("Relationship", range(len(rels)), format_func=lambda k: rel_text(rels[k]),
                             key=f"edit_rel_{focus}")
            r = rels[i]
            k = f"{focus}_{i}"
            c1, c2, c3, c4 = st.columns([2, 1, 2, 3])
            with c1:
                new_type = type_picker("Type", f"edit_type_{k}", r["type"])
            reverse = c2.checkbox("Reverse direction", key=f"edit_rev_{k}")
            new_line = c3.text_input("Line number", r["props"].get("lineNumber", ""), key=f"edit_line_{k}")
            new_note = c4.text_input("Note", key=f"edit_note_{k}")
            b1, b2, _ = st.columns([1, 1, 3])
            if b1.button("Save changes", type="primary", disabled=not new_type):
                try:
                    pl.change_relationship(db, r, new_type, reverse, new_line, new_note)
                    flash(f"Changed {rel_text(r)}.")
                except ValueError as exc:
                    st.error(str(exc))
            if b2.button("Delete relationship"):
                pl.delete_relationship(db, r, new_note)
                flash(f"Deleted {rel_text(r)}.")
            neighbours = {focus} | {x["src"] for x in rels} | {x["dst"] for x in rels}
            draw(neighbours, focus, height=420, key="graph_edit")

    st.markdown("#### Edit log")
    edits = pl.manual_edits(db)
    if not edits:
        st.info("No manual edits yet.")
    else:
        changes = {}
        for e in edits:
            changes.setdefault(e["change"], []).append(e)
        rows = []
        for change, items in changes.items():
            text = {"add": [], "remove": [], "set": []}
            for e in items:
                if e["action"] == "set":  # name / category / type / description changed by hand
                    new, old = json.loads(e["props"] or "{}"), json.loads(e["original"] or "{}")
                    text["set"].append(f"{e['src_name']}: " + ", ".join(
                        f"{k} '{old.get(k, '')}' → '{v}'" for k, v in new.items()))
                else:
                    text[e["action"]].append(f"{e['src_name']} -[{e['type']}]-> {e['dst_name']}")
            what = ("changed " + "; ".join(text["set"])) if text["set"] else \
                ("changed " + "; ".join(text["remove"]) + "  ⇒  " + "; ".join(text["add"])) \
                if text["add"] and text["remove"] else \
                ("added " + "; ".join(text["add"]) if text["add"] else "removed " + "; ".join(text["remove"]))
            rows.append({"when": items[0]["created"], "edit": what, "note": items[0]["note"],
                         "status": "applied" if all(e["applied"] for e in items) else "waiting for a sheet",
                         "id": items[0]["id"]})
        st.dataframe(pd.DataFrame(rows).drop(columns="id"), hide_index=True, use_container_width=True)
        st.caption("'waiting for a sheet': one of the assets is not loaded; the edit is applied when it is.")
        undo = st.selectbox("Undo an edit", range(len(rows)), format_func=lambda k: f"{rows[k]['when']}  {rows[k]['edit']}",
                            key="edit_undo")
        if st.button("Undo this edit"):
            pl.undo_manual_edit(db, rows[undo]["id"])
            flash(f"Undone: {rows[undo]['edit']}")
