"""
P&ID Knowledge Graph — web interface for plant_store.py (loading) and plant_loader.py (reading DEXPI).

Opens on the graph of the whole plant (conceptual level, flow left → right, simple view). "View options" changes what
is shown, the detail level and the layout; "Add / load" adds DEXPI sheets (with an optional matching PDF check).

Neo4j holds only the conceptual level (main equipment, off-page connectors, loops and the flows between them). The
complete graph of each sheet is kept as a file (snapshots/<sheet>.json); the Process and Complete levels are computed
from those files.

Start it with:   streamlit run app.py
It opens in your browser at http://localhost:8501
"""
import os
from html import escape
from types import SimpleNamespace

import pandas as pd
import streamlit as st

import graph_levels as gl
import graph_view
import plant_loader as pl
import plant_store as ps
import ui_style as ui
import view_query as vq
from crosscheck import store as pdf_store

UPLOAD_DIR = "uploads"
LAYOUTS = graph_view.LAYOUTS
DEFAULT_LEVEL = {"Whole plant": "Conceptual", "Around an asset": "Process", "Downstream of an asset": "Process",
                 "One sheet": "Complete"}
GRAPH_HEIGHT = 760

st.set_page_config(page_title="P&ID Knowledge Graph", page_icon="🔗", layout="wide")
ui.inject()
st.html("""<style>
.pk-bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:-.6rem 0 .4rem}
.pk-bar h1{margin:0;padding:0;font-size:1.5rem;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.pk-bar .pk-chips{margin:0 0 0 auto}
</style>""")


# --------------------------------------------------------------------------- connection

@st.cache_resource(show_spinner=False)
def get_db(uri, user, password, test_db):
    db = pl.Db(SimpleNamespace(uri=uri, user=user, password=password, database=None, test_db=test_db))
    db.setup()
    return db


db = None
conn = st.session_state.get("conn")  # (uri, user, password, test_db) once connected; kept in memory only
if not conn:
    # with NEO4J_PASSWORD set (neo4j_demo.env) this connects at once; otherwise the login panel is shown
    test_db = os.environ.get("PLANT_TEST_DB")  # offline testing only
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not (test_db or password):
        with st.sidebar:
            ui.sidebar_brand()
            st.header("Neo4j connection")
            uri = st.text_input("URI", uri)
            user = st.text_input("User", user)
            password = st.text_input("Password", "", type="password", help="Kept in memory only, never saved to a file.")
            if not password:
                st.info("Enter the Neo4j password to start.")
    if test_db or password:
        try:
            get_db(uri, user, password, test_db)
            st.session_state["conn"] = (uri, user, password, test_db)
            st.rerun()
        except Exception as exc:
            with st.sidebar:
                st.error(f"Cannot connect: {exc}")
    conn = st.session_state.get("conn")
if conn:
    db = get_db(*conn)


def header(chips=()):
    chip_html = "".join(f'<span class="pk-chip pk-{kind}"><i></i>{escape(text)}</span>' for text, kind in chips)
    st.html(f'<div class="pk-bar"><h1>P&amp;ID <span class="pk-grad">Knowledge Graph</span></h1>'
            f'<div class="pk-chips">{chip_html}</div></div>')


if not db:
    header([("Waiting for Neo4j", "wait")])
    st.stop()

store = ps.PlantStore(db)


def q(query, **params):
    return db.run(query, **params)


def node_options(view):
    """{uid: label} of a level graph's nodes, equipment first."""
    order = {"Equipment": 0, "Instrumentation": 1, "Piping": 2}
    rows = sorted(view["nodes"].items(), key=lambda kv: (order.get(kv[1].get("category"), 3), str(kv[1].get("name"))))
    return {u: (n.get("name") or u) + (f"  (sheet {n['sheet']})" if n.get("sheet") else "") for u, n in rows}


def pick_node(label, key, view):
    opts = node_options(view)
    if not opts:
        st.info("No matching items loaded yet.")
        return None
    return st.selectbox(label, list(opts), format_func=opts.get, key=key)


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


def draw(view, uids, focus=None, height=GRAPH_HEIGHT, key="graph_main", level="Complete", origin="", cypher=""):
    """Draw `uids` of a level graph. A FLOWS_TO link's inspector offers "Show detail" (its Process path below)."""
    uids = list(uids)
    if not uids:
        st.info("Nothing to show.")
        return
    nodes, edges = view_rows(gl.subset(view, uids))
    event = graph_view.show(nodes, edges, focus, st.session_state["layout"], height, pl.OFFPAGE_TYPES, key=key,
                            level=level, flash=st.session_state.pop(f"{key}_flash", None), detail=level == "Conceptual",
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
        graph_view.show(nodes, edges, src, st.session_state["layout"], 300, pl.OFFPAGE_TYPES, key="graph_detail",
                        level="Process", origin=ps.FROM_SNAPSHOT, cypher=query)


# --------------------------------------------------------------------------- add / load

@st.dialog("Add / load DEXPI / PDF", width="large")
def add_dialog():
    st.caption("Each DEXPI sheet is added to the one plant graph. A matching PDF of a sheet is optional: it checks the "
               "sheet's pipe links against the drawing.")
    # no type filter: COMOS exports are DEXPI XML but have no .xml extension, so files are checked by content
    files = st.file_uploader("DEXPI P&ID files (Proteus XML, DEXPI 1.3 — .xml files or COMOS exports)",
                             accept_multiple_files=True)
    pdfs = st.file_uploader("Matching PDF (same sheet) — optional", type=["pdf"], accept_multiple_files=True,
                            key="dexpi_pdfs",
                            help="A PDF is paired with a sheet when its file name contains the sheet's drawing "
                                 "number or label; otherwise pick it in the table.")
    if files:
        rejected = [f.name for f in files if not pl.looks_like_dexpi(f.getvalue()[:4000])]
        if rejected:
            st.error(f"Not a DEXPI / Proteus XML file, skipped: {', '.join(rejected)}. "
                     "PDFs go in the *Matching PDF* box.")
        files = [f for f in files if f.name not in rejected]
    if files:
        loaded = set(store.registry()) | {r["name"] for r in q("MATCH (s:Sheet) RETURN s.name AS name")}
        sheets_in = [{"sheet": pl.sheet_name_from_file(f.name), "file": f.name,
                      "drawing": pdf_store.drawing_name(f.getvalue()[:20000])} for f in files]
        paired = pdf_store.pair_pdfs(sheets_in, [p.name for p in pdfs or []])
        table = pd.DataFrame([{"file": s["file"], "sheet": s["sheet"], "pdf": paired[s["sheet"]]} for s in sheets_in])
        edited = st.data_editor(table, hide_index=True, use_container_width=True,
                                column_config={"file": st.column_config.TextColumn(disabled=True),
                                               "pdf": st.column_config.SelectboxColumn(
                                                   "matching PDF", options=[""] + [p.name for p in pdfs or []])})
        replace = sorted(set(edited["sheet"]) & loaded)
        if replace:
            st.warning(f"These sheets are already loaded and will be replaced: {', '.join(replace)}")
        if edited["sheet"].duplicated().any():
            st.error("Two files have the same sheet name. Give each file its own sheet name.")
        elif st.button("Add to knowledge graph", type="primary"):
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            by_name = {f.name: f for f in files}
            pdf_by_name = {p.name: p for p in pdfs or []}
            existing_plants = [r["name"] for r in q("MATCH (p:Plant) RETURN p.name AS name")]
            plant = existing_plants[0] if existing_plants else pl.DEFAULT_PLANT
            lines, loaded_n = [], 0
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
                    r = store.load_sheet(path, sheet, plant, None, None, None, pdf_path, row.get("pdf"), sync=False)
                    loaded_n += 1
                    lines.append(("ok", f"**{sheet}**: {r['assets']} assets, {r['connections']} connections"
                                        + (f" · {r['pdfLine']}" if r.get("pdf") else "")))
                except Exception as exc:
                    lines.append(("error", f"**{sheet}** failed: {exc}"))
                bar.progress((i + 1) / len(edited))
            with st.spinner("Writing the conceptual level to Neo4j…"):
                store.sync()
            st.session_state["load_flash"] = lines
            st.rerun()   # closes the dialog and redraws the graph with the new sheets
    registered = list(store.registry())
    if registered:
        with st.expander(f"Loaded sheets ({len(registered)})"):
            st.write(", ".join(registered))
            target = st.selectbox("Remove a sheet", registered)
            if st.button(f"Remove sheet {target}"):
                store.remove_sheet(target)
                st.session_state["load_flash"] = [("ok", f"Sheet **{target}** removed.")]
                st.rerun()


# --------------------------------------------------------------------------- page

n_sheets = q("MATCH (s:Sheet) RETURN count(s) AS n")[0]["n"]
n_assets = q("MATCH (a:Asset) RETURN count(a) AS n")[0]["n"]
header([(f"{n_sheets} sheets · {n_assets} assets", "ok" if n_sheets else "wait"),
        ("Neo4j online · " + ("offline test database" if conn[3] else conn[0]), "ok")])

c_opts, c_add, c_gap, c_out = st.columns([1.7, 2.6, 4.2, 1.5])
with c_opts.popover("View options", icon=":material/tune:", use_container_width=True):
    mode = st.radio("Show", ["Whole plant", "Around an asset", "Downstream of an asset", "One sheet"], key="graph_mode")
    level = st.radio("Detail level", gl.LEVELS, key=f"graph_level_{mode}", index=gl.LEVELS.index(DEFAULT_LEVEL[mode]),
                     help="Conceptual: main equipment and off-page connectors, flow through valves and fittings. "
                          "Process: equipment, valves, off-page connectors and instrument loops. Complete: every object.")
    st.radio("Layout", list(LAYOUTS), key="layout", index=list(LAYOUTS).index("Flow (left → right)"))
    sheet, picked, hops = None, None, 2
    if mode == "One sheet":
        names = list(store.registry())
        if names:
            sheet = st.selectbox("Sheet", names, key="graph_sheet")
    plant_view = None
    if mode in ("Around an asset", "Downstream of an asset"):
        plant_view = (ps.conceptual_view(db, loops=False) if level == "Conceptual"
                      else store.level_view(level, None, candidates=False))
        picked = pick_node("Asset", f"graph_asset_{level}", plant_view)
        if mode == "Around an asset":
            hops = st.slider("Steps out", 1, 6, 2)
if c_add.button("Add / load DEXPI / PDF", icon=":material/upload_file:", type="primary", use_container_width=True):
    add_dialog()
with c_out:
    if st.button("Disconnect", icon=":material/logout:", type="tertiary", help="Back to the Neo4j login panel"):
        del st.session_state["conn"]
        st.rerun()

for kind, text in st.session_state.pop("load_flash", []):
    (st.success if kind == "ok" else st.error)(text)

if level == "Conceptual":
    plant_view, origin = plant_view or ps.conceptual_view(db, loops=False), "Neo4j"
else:
    plant_view = plant_view or store.level_view(level, [sheet] if sheet else None, candidates=False)
    origin = ps.FROM_SNAPSHOT

if not plant_view["nodes"]:
    st.info("No sheets loaded yet. Click **Add / load DEXPI / PDF** to add the first one.")
    st.stop()

rep = plant_view["representative"]
uids, focus, view = None, None, {"mode": mode}
if mode == "Whole plant":
    uids = sorted(plant_view["nodes"])[:1500]
    if len(plant_view["nodes"]) > 1500:
        st.warning("Large plant: showing the first 1,500 items. Use View options to focus.")
elif mode == "One sheet":
    if sheet:
        uids = [u for u, n in plant_view["nodes"].items() if sheet in n.get("sheets", [])]
        view["sheet"] = sheet
else:
    focus = rep.get(picked) if picked else None
    if picked and not focus:
        st.info(f"This item is not part of the {level} level. Pick another one in View options.")
    elif focus and mode == "Around an asset":
        uids = gl.neighbourhood(plant_view, focus, hops)
        view["steps"] = hops
    elif focus:
        uids = gl.downstream(plant_view, focus)
    if focus:
        view["asset"] = plant_view["nodes"][focus]["name"]
        view["assetUid"] = focus

if uids is not None:
    query = vq.for_view(plant_view, gl.subset(plant_view, uids), view, level, origin, loops=False) if uids else ""
    draw(plant_view, uids, focus, level=level, origin=origin, cypher=query)
    if level == "Conceptual" and origin == "Neo4j":
        show_link_detail(plant_view)
