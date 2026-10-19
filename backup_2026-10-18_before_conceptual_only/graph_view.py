"""
Interactive graph view for app.py: a dark "control room" canvas built on vis-network.

show(...) puts the graph in the Streamlit page as a custom component. With edit=... it is editable: click a
node, then Edit (name, category, type, description), reverse or delete a connection, or Connect it to another
item. Each change comes back to Python as an event (a dict with "action"); app.py saves it as a manual edit.
render(...) returns the same graph as a stand-alone, read-only HTML page.

The vis-network library is read from the pyvis package, so it works offline.
"""
import hashlib
import json
import os
import tempfile
from collections import defaultdict, deque
from functools import lru_cache

import pyvis
import streamlit.components.v1 as components

LAYOUTS = {
    "Flow (left → right)": {
        "layout": {"hierarchical": {"enabled": True, "direction": "LR", "levelSeparation": 140,
                                    "nodeSpacing": 55, "treeSpacing": 70, "sortMethod": "hubsize"}},
        "physics": {"enabled": False},
        "edges": {"smooth": {"type": "cubicBezier", "forceDirection": "horizontal", "roundness": 0.45}},
    },
    "Free": {
        "physics": {"barnesHut": {"gravitationalConstant": -2500, "centralGravity": 0.5,
                                  "springLength": 70, "springConstant": 0.05, "damping": 0.35},
                    "stabilization": {"iterations": 400, "updateInterval": 20}},
        "edges": {"smooth": {"type": "continuous"}},
    },
}


PIPE_RELS = ("PIPE", "DIRECT_PIPING_CONNECTION", "CONTINUES_TO")


def flow_levels(node_ids, edges, focus):
    """Column for each node in the flow layout = steps along the pipes from the start (works with
    recirculation loops). Instruments and other items off the pipes go next to what they are linked to."""
    out = defaultdict(list)
    indeg = defaultdict(int)
    piped = set()
    for e in edges:
        if e["rel"] in PIPE_RELS:
            out[e["s"]].append(e["t"])
            indeg[e["t"]] += 1
            piped |= {e["s"], e["t"]}

    level = {}

    def walk(starts):
        queue = deque(starts)
        while queue:
            n = queue.popleft()
            for m in out[n]:
                if m not in level:
                    level[m] = level[n] + 1
                    queue.append(m)

    roots = [focus] if focus else [n for n in node_ids if n in piped and indeg[n] == 0]
    for r in roots:
        level[r] = 0
    walk(roots)
    for n in node_ids:  # pipe items not reached from a start (e.g. a loop with no entry)
        if n in piped and n not in level:
            level[n] = 0
            walk([n])
    # the rest (instruments, actuators, motors): one column before what they act on, one after what they read,
    # repeated so chains such as instrument -> actuator -> valve line up
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e["s"] not in level and e["t"] in level:
                level[e["s"]] = max(level[e["t"]] - 1, 0)
                changed = True
            elif e["t"] not in level and e["s"] in level:
                level[e["t"]] = level[e["s"]] + 1
                changed = True
    return {n: level.get(n, 0) for n in node_ids}


@lru_cache(maxsize=1)
def _vis_js():
    path = os.path.join(os.path.dirname(pyvis.__file__), "templates", "lib", "vis-9.1.2", "vis-network.min.js")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _edge_kind(e):
    if e.get("status") == "candidate":
        return "candidate"
    if e.get("source") == "manual":
        return "manual"
    if e["rel"] == "CONTINUES_TO" or e.get("crossSheet"):
        return "offpage"
    if e.get("loopLink"):
        return "loop"
    if e["rel"] in ("PIPE", "DIRECT_PIPING_CONNECTION"):
        return "pipe"
    return "signal"


def payload(nodes, edges, focus=None, layout="Flow (left → right)", offpage_types=(), edit=None, level=""):
    """The data the page draws. nodes: uid, name, cat, type, line, sheets (+ desc, folded, instruments).
    edges: s, t, rel, sheet, source (+ crossSheet, loopLink, via, lines, loops: see graph_levels.py).
    edit: None (read-only) or {"relTypes": [...], "assetTypes": [...]} for the pickers."""
    levels = flow_levels([n["uid"] for n in nodes], edges, focus) if layout.startswith("Flow") else {}
    vn = []
    for n in nodes:
        cat = "OffPage" if n["type"] in offpage_types else (n["cat"] or "Piping")
        valve = "valve" in str(n["type"]).lower()
        item = {"id": n["uid"], "name": str(n["name"]), "cat": cat, "category": n["cat"] or "Piping", "valve": valve,
                "type": str(n["type"] or ""), "line": n.get("line") or "", "desc": n.get("desc") or "",
                "sheets": sorted(s for s in (n.get("sheets") or []) if s), "focus": n["uid"] == focus,
                "folded": list(n.get("folded") or []), "instruments": list(n.get("instruments") or [])}
        if levels:
            item["level"] = levels[n["uid"]]
        vn.append(item)
    ve = [{"id": i, "from": e["s"], "to": e["t"], "rel": e["rel"], "sheet": e.get("sheet") or "",
           "kind": _edge_kind(e), "source": e.get("source") or "", "status": e.get("status") or "",
           "via": list(e.get("via") or []), "lines": list(e.get("lines") or []),
           "loops": list(e.get("loops") or [])} for i, e in enumerate(edges)]
    data = {"nodes": vn, "edges": ve, "layout": layout, "options": LAYOUTS[layout], "focus": focus,
            "edit": bool(edit), "level": level, **(edit or {})}
    data["version"] = hashlib.md5(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    return data


def render(nodes, edges, focus=None, layout="Flow (left → right)", height=650, offpage_types=()):
    """Stand-alone read-only HTML page with the graph."""
    data = json.dumps(payload(nodes, edges, focus, layout, offpage_types), default=str).replace("</", "<\\/")
    return _page(f"{height}px").replace("/*__DATA__*/null", data)


@lru_cache(maxsize=1)
def _component():
    """Register the page as a custom component (served from a temp folder, rewritten at start-up)."""
    folder = os.path.join(tempfile.gettempdir(), "pid_kg_graph_component")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(_page("100vh"))
    return components.declare_component("pid_graph", path=folder)


def show(nodes, edges, focus=None, layout="Flow (left → right)", height=650, offpage_types=(), key="graph",
         edit=None, flash=None, level=""):
    """Draw the graph in the page. Returns the latest event from the page (a dict with "action" and a unique
    "nonce"), or None. The same event comes back on every rerun until the next one, so check the nonce.
    flash: {"text", "kind", "id"} shown once as a short message, e.g. the result of the last edit.
    level: the detail level shown (graph_levels.py), for the status line."""
    data = payload(nodes, edges, focus, layout, offpage_types, edit, level)
    data["context"] = f"{key}|{layout}|{focus}|{level}"   # same context: the page keeps zoom, position and selection
    data["flash"] = flash
    return _component()(data=data, height=height, key=key, default=None)


def _page(height):
    return _TEMPLATE.replace("/*__VIS__*/", _vis_js()).replace("__HEIGHT__", height)


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
:root{
  --bg:#05080f; --panel:rgba(10,18,32,.78); --line:rgba(56,189,248,.18); --line2:rgba(148,163,184,.14);
  --txt:#e2e8f0; --mute:#7c8aa5; --cyan:#22d3ee; --amber:#fbbf24; --slate:#64748b; --valve:#cbd5e1;
  --violet:#c084fc; --pipe:#38bdf8; --pink:#f472b6; --green:#34d399;
  --mono:"JetBrains Mono","Cascadia Mono",Consolas,"SF Mono",Menlo,monospace;
  --sans:Inter,"Segoe UI",system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;height:__HEIGHT__;background:transparent;font-family:var(--sans);color:var(--txt);overflow:hidden}
#wrap{position:relative;height:100%;border-radius:14px;overflow:hidden;border:1px solid var(--line);
  background:
    radial-gradient(1200px 600px at 15% 0%,rgba(34,211,238,.10),transparent 60%),
    radial-gradient(900px 500px at 100% 100%,rgba(192,132,252,.10),transparent 60%),
    linear-gradient(var(--line2) 1px,transparent 1px) 0 0/32px 32px,
    linear-gradient(90deg,var(--line2) 1px,transparent 1px) 0 0/32px 32px,
    var(--bg);
  box-shadow:inset 0 0 120px rgba(0,0,0,.65),0 0 0 1px rgba(34,211,238,.05),0 20px 50px -20px rgba(34,211,238,.25)}
#wrap:fullscreen{border-radius:0}
#wrap::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(0deg,rgba(255,255,255,.012) 0 1px,transparent 1px 3px)}
#net{position:absolute;inset:0}
.corner{position:absolute;width:18px;height:18px;border-color:rgba(34,211,238,.55);border-style:solid;pointer-events:none;z-index:3}
.c1{top:8px;left:8px;border-width:2px 0 0 2px}.c2{top:8px;right:8px;border-width:2px 2px 0 0}
.c3{bottom:8px;left:8px;border-width:0 0 2px 2px}.c4{bottom:8px;right:8px;border-width:0 2px 2px 0}
.glass{background:var(--panel);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  border:1px solid var(--line);border-radius:10px;box-shadow:0 10px 30px -10px rgba(0,0,0,.7)}
/* top-left status */
#hud{position:absolute;top:16px;left:16px;z-index:5;padding:9px 13px;display:flex;gap:14px;align-items:center;
  font:600 11px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase}
#hud .live{display:flex;align-items:center;gap:7px;color:var(--cyan)}
#hud .live i{width:7px;height:7px;border-radius:50%;background:var(--cyan);box-shadow:0 0 10px var(--cyan);animation:pulse 1.6s infinite}
#hud .k{color:var(--mute)} #hud b{color:var(--txt);font-weight:700}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
/* toolbar */
#tools{position:absolute;top:16px;right:16px;z-index:6;display:flex;gap:8px;align-items:flex-start}
#bar{display:flex;padding:4px;gap:2px}
.btn{all:unset;cursor:pointer;width:32px;height:32px;display:grid;place-items:center;border-radius:7px;color:#a5b4cb;transition:.15s}
.btn:hover{background:rgba(34,211,238,.12);color:var(--cyan)}
.btn.on{color:var(--cyan);background:rgba(34,211,238,.14);box-shadow:inset 0 0 0 1px rgba(34,211,238,.35)}
.btn svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.sep{width:1px;margin:6px 3px;background:var(--line)}
#search{position:relative}
#q{width:210px;height:40px;padding:0 12px 0 34px;border-radius:10px;border:1px solid var(--line);background:var(--panel);
  backdrop-filter:blur(10px);color:var(--txt);font:500 12.5px var(--mono);outline:none;transition:.2s}
#q:focus{border-color:rgba(34,211,238,.6);box-shadow:0 0 0 3px rgba(34,211,238,.15);width:250px}
#q::placeholder{color:#55627a}
#search svg{position:absolute;left:11px;top:12px;width:15px;height:15px;stroke:var(--mute);fill:none;stroke-width:2}
#hits{position:absolute;top:46px;right:0;width:100%;min-width:250px;max-height:280px;overflow:auto;display:none;padding:4px}
#hits div{padding:7px 9px;border-radius:6px;cursor:pointer;font:12px var(--mono);display:flex;gap:8px;align-items:center;white-space:nowrap}
#hits div span{overflow:hidden;text-overflow:ellipsis}
#hits div:hover,#hits div.act{background:rgba(34,211,238,.12)}
#hits small{margin-left:auto;color:var(--mute);font-size:10px}
.sw{width:9px;height:9px;border-radius:50%;flex:none;box-shadow:0 0 8px currentColor;background:currentColor}
/* legend */
#legend{position:absolute;left:16px;bottom:16px;z-index:5;padding:7px 9px;font-size:11.5px;display:flex;align-items:center;gap:8px;max-width:calc(100% - 300px)}
#legend h4{margin:0;padding:0 4px;font:700 9.5px var(--mono);letter-spacing:.14em;color:var(--mute);text-transform:uppercase;
  cursor:pointer;user-select:none;white-space:nowrap;writing-mode:vertical-rl;transform:rotate(180deg);border-left:1px solid var(--line)}
#legend.min .rows{display:none}
.rows{display:grid;gap:3px}
.rw{display:flex;flex-wrap:wrap;gap:2px}
.lg{display:flex;align-items:center;gap:7px;padding:3px 8px;border-radius:6px;cursor:pointer;user-select:none;transition:.15s;white-space:nowrap}
.lg:hover{background:rgba(148,163,184,.08)}
.lg.off{opacity:.3;text-decoration:line-through}
.lg .n{font:10.5px var(--mono);color:var(--mute)}
.lg svg{width:20px;height:13px;flex:none;overflow:visible}
/* hint */
#hint{position:absolute;right:16px;bottom:16px;z-index:5;font:10.5px var(--mono);color:#56637b;letter-spacing:.04em;text-align:right;line-height:1.7}
#hint kbd{font:inherit;color:#8b9ab4;border:1px solid var(--line);border-radius:4px;padding:0 4px;background:rgba(15,23,42,.6)}
/* tooltip */
#tip{position:absolute;z-index:9;pointer-events:none;padding:10px 12px;min-width:180px;max-width:300px;opacity:0;
  transform:translateY(4px);transition:opacity .12s,transform .12s;font-size:12px}
#tip.show{opacity:1;transform:none}
.tt-h{display:flex;align-items:center;gap:8px;font:700 13px var(--mono);margin-bottom:6px;word-break:break-all}
.row{display:flex;gap:10px;padding:2px 0;font-size:11.5px}
.row .k{color:var(--mute);font:600 10px var(--mono);letter-spacing:.08em;text-transform:uppercase;min-width:52px;padding-top:2px}
.row .v{color:var(--txt);word-break:break-word}
/* inspector */
#insp{position:absolute;top:66px;right:16px;bottom:16px;width:300px;z-index:7;padding:16px;display:flex;flex-direction:column;
  transform:translateX(calc(100% + 24px));transition:transform .28s cubic-bezier(.2,.8,.2,1)}
#insp.open{transform:none}
#insp .x{position:absolute;top:10px;right:10px}
#insp .eyebrow{font:700 10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--mute)}
#insp h3{margin:6px 30px 10px 0;font:700 17px var(--mono);word-break:break-all;line-height:1.3}
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;font:700 10px var(--mono);
  letter-spacing:.08em;text-transform:uppercase;border:1px solid currentColor;background:rgba(255,255,255,.03)}
.kv{margin:12px 0;display:grid;gap:7px}
#insp .conns{flex:1;overflow:auto;margin:0 -6px;padding:0 6px}
#insp .ch{font:700 10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--mute);margin:10px 0 6px;
  display:flex;justify-content:space-between}
.cn{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:7px;cursor:pointer;font:12px var(--mono);
  border:1px solid transparent;transition:.15s}
.cn:hover{border-color:var(--line);background:rgba(34,211,238,.06)}
.cn .dir{color:var(--mute);width:12px}
.cn .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cn .rel{margin-left:auto;font-size:9.5px;letter-spacing:.06em;padding:2px 6px;border-radius:4px;white-space:nowrap}
/* loader */
#load{position:absolute;inset:0;z-index:20;display:grid;place-items:center;background:rgba(5,8,15,.82);transition:opacity .4s}
#load.done{opacity:0;pointer-events:none}
.ld{display:grid;justify-items:center;gap:14px;font:600 11px var(--mono);letter-spacing:.16em;color:var(--cyan);text-transform:uppercase}
.ring{width:46px;height:46px;border-radius:50%;border:2px solid rgba(34,211,238,.15);border-top-color:var(--cyan);
  animation:spin .9s linear infinite;box-shadow:0 0 24px rgba(34,211,238,.25)}
.pbar{width:180px;height:3px;border-radius:3px;background:rgba(34,211,238,.12);overflow:hidden}
.pbar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--cyan),var(--violet));box-shadow:0 0 10px var(--cyan)}
@keyframes spin{to{transform:rotate(360deg)}}
/* editing */
#insp .ibtns{position:absolute;top:10px;right:10px;display:flex;gap:2px}
#insp h3{margin-right:64px}
.form{display:none;gap:9px;margin:12px 0 4px}
.form label{display:grid;gap:4px;font:600 9.5px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--mute)}
.form input,.form select,#cbar input{height:32px;padding:0 9px;border-radius:7px;border:1px solid var(--line);background:rgba(5,8,15,.7);
  color:var(--txt);font:500 12.5px var(--mono);letter-spacing:0;text-transform:none;outline:none}
.form input:focus,.form select:focus,#cbar input:focus{border-color:rgba(34,211,238,.6);box-shadow:0 0 0 3px rgba(34,211,238,.14)}
.fbtns{display:flex;gap:8px;margin-top:2px}
.pbtn,.gbtn,.wide{all:unset;cursor:pointer;height:30px;padding:0 12px;border-radius:7px;font:600 12px var(--sans);display:inline-flex;align-items:center;gap:6px;transition:.15s}
.pbtn{background:linear-gradient(90deg,#06b6d4,#8b5cf6);color:#fff;box-shadow:0 6px 18px -8px rgba(34,211,238,.8)}
.pbtn:hover{filter:brightness(1.12)}
.gbtn{border:1px solid var(--line);color:#a5b4cb;background:rgba(15,23,42,.6)}
.gbtn:hover{border-color:var(--cyan);color:var(--cyan)}
.fnote{font-size:10.5px;color:var(--mute);line-height:1.4}
.wide{margin:12px 0 4px;width:100%;box-sizing:border-box;justify-content:center;border:1px dashed rgba(52,211,153,.45);color:var(--green)}
.wide:hover{background:rgba(52,211,153,.08)}
.wide svg,.mini svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.cn .acts{display:none;gap:2px;margin-left:4px}
.cn:hover .acts{display:flex}
.mini{all:unset;cursor:pointer;width:22px;height:22px;display:grid;place-items:center;border-radius:5px;color:#a5b4cb}
.mini:hover{background:rgba(34,211,238,.14);color:var(--cyan)}
.mini.danger:hover,.mini.sure{background:rgba(248,113,113,.16);color:#f87171}
#cbar{position:absolute;top:66px;left:50%;transform:translate(-50%,-12px);z-index:8;display:flex;align-items:center;gap:9px;
  padding:8px 10px;font-size:12px;opacity:0;pointer-events:none;transition:.2s;border-color:rgba(52,211,153,.5);white-space:nowrap}
#cbar.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}
#cbar b{font:700 12.5px var(--mono)}
#cbar input{width:170px;height:28px}
#cbar .gbtn{height:28px}
.cdot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 1.4s infinite}
.cmute{color:var(--mute)}
.tt-go{margin-top:6px;font:700 10px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--green)}
#toast{position:absolute;left:50%;bottom:22px;transform:translate(-50%,10px);z-index:12;padding:9px 14px;font:600 12.5px var(--sans);
  opacity:0;pointer-events:none;transition:.2s}
#toast.show{opacity:1;transform:translate(-50%,0)}
#toast.ok{border-color:rgba(52,211,153,.6);color:#a7f3d0}
#toast.error{border-color:rgba(248,113,113,.6);color:#fecaca}
#toast.busy{color:var(--cyan)}
@media (max-width:640px){#hud .k2,#hint,#cbar .cmute{display:none}#q{width:130px}#q:focus{width:160px}#insp{width:auto;left:16px}}
</style></head>
<body>
<div id="wrap">
  <div id="net"></div>
  <i class="corner c1"></i><i class="corner c2"></i><i class="corner c3"></i><i class="corner c4"></i>
  <div id="hud" class="glass">
    <span class="live"><i></i>Graph</span>
    <span class="k">Nodes <b id="cN">0</b></span>
    <span class="k">Links <b id="cE">0</b></span>
    <span class="k k2" id="lay"></span>
  </div>
  <div id="tools">
    <div id="search">
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input id="q" placeholder="Find asset…  ( / )" autocomplete="off" spellcheck="false">
      <div id="hits" class="glass"></div>
    </div>
    <div id="bar" class="glass">
      <button class="btn" id="bFit" title="Fit to view (F)"><svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
      <button class="btn" id="bIn" title="Zoom in"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
      <button class="btn" id="bOut" title="Zoom out"><svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>
      <span class="sep"></span>
      <button class="btn" id="bFlow" title="Animate flow along pipes"><svg viewBox="0 0 24 24"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg></button>
      <button class="btn" id="bLbl" title="Show / hide valve and off-page labels"><svg viewBox="0 0 24 24"><path d="M4 7V5h16v2M9 19h6M12 5v14"/></svg></button>
      <button class="btn" id="bSimple" title="Simple view: every item a dot, every link a plain line (S)"><svg viewBox="0 0 24 24"><circle cx="6" cy="7" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.5 7h7M7.3 9.2l3.4 6.6M16.7 9.2l-3.4 6.6"/></svg></button>
      <button class="btn" id="bShot" title="Save as PNG"><svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg></button>
      <button class="btn" id="bFull" title="Fullscreen"><svg viewBox="0 0 24 24"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg></button>
    </div>
  </div>
  <div id="legend" class="glass"><h4 id="lgH" title="Click an item to show / hide it. Click here to collapse.">Legend</h4><div class="rows" id="lgRows"></div></div>
  <div id="hint"><kbd>drag</kbd> pan · <kbd>scroll</kbd> zoom · <kbd>click</kbd> inspect<br><kbd>/</kbd> search · <kbd>F</kbd> fit · <kbd>S</kbd> simple · <kbd>Esc</kbd> clear</div>
  <div id="tip" class="glass"></div>
  <div id="insp" class="glass"></div>
  <div id="cbar" class="glass"></div>
  <div id="toast" class="glass"></div>
  <div id="load"><div class="ld"><div class="ring"></div><span id="ldT">Building graph</span><div class="pbar"><i id="ldP"></i></div></div></div>
</div>
<script>/*__VIS__*/</script>
<script>
(function(){
const EMBEDDED = /*__DATA__*/null;   // filled in for a static page; empty when running as a Streamlit component
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const words = s => String(s || "").replace(/_/g, " ").replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().replace(/^\w/, c => c.toUpperCase());
const MONO = 'JetBrains Mono, Cascadia Mono, Consolas, monospace';

const CAT = {
  Equipment:       {c:"#22d3ee", fill:"#062630", txt:"#d6faff", name:"Equipment"},
  Instrumentation: {c:"#fbbf24", fill:"#2b1f06", txt:"#fff1cc", name:"Instrumentation"},
  Valve:           {c:"#cbd5e1", fill:"#0d1524", txt:"#aab6c8", name:"Valve"},
  Piping:          {c:"#64748b", fill:"#1e293b", txt:"#8a97ad", name:"Piping item"},
  OffPage:         {c:"#c084fc", fill:"#22103a", txt:"#ead5ff", name:"Off-page arrow"},
  CtrlValve:       {c:"#22d3ee", fill:"#062630", txt:"#d6faff", name:"Control / safety valve"},
};
// colours for categories people add themselves (Electrical, Safety device, ...): picked from the name
const EXTRA = ["#a3e635", "#fb923c", "#f472b6", "#2dd4bf", "#818cf8", "#facc15", "#f87171", "#e879f9", "#4ade80", "#60a5fa"];
const shade = (hex, f) => { const n = parseInt(hex.slice(1), 16); const c = v => Math.round(v * f).toString(16).padStart(2, "0");
                            return "#" + c(n >> 16) + c((n >> 8) & 255) + c(n & 255); };
function catOf(key){
  if (!CAT[key]){
    let h = 0; for (const ch of String(key)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    const c = EXTRA[h % EXTRA.length];
    CAT[key] = {c, fill: shade(c, .16), txt: "#f1f5f9", name: key, custom: true};
  }
  return CAT[key];
}
const EDGE = {
  pipe:    {c:"#38bdf8", w:2,   dash:false,  name:"Pipe",               flow:true},
  signal:  {c:"#fbbf24", w:1.2, dash:[4,5],  name:"Signal / measurement"},
  loop:    {c:"#fbbf24", w:1.6, dash:[1,5],  name:"Loop signal"},
  offpage: {c:"#f472b6", w:2.6, dash:[8,6],  name:"Continues on other sheet", flow:true},
  manual:  {c:"#34d399", w:2.6, dash:false,  name:"Added by hand",      flow:true},
  candidate:{c:"#fb923c", w:2.4, dash:[7,5], name:"Drawing candidate",  flow:true},
};
const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n>>16},${(n>>8)&255},${n&255},${a})`; };
const keyOf = n => n.cat === "OffPage" ? "OffPage"
  : n.valve && n.cat === "Piping" ? "Valve"            // manual valve
  : n.valve && n.cat === "Equipment" ? "CtrlValve"     // control / on-off / safety valve, counted as equipment
  : (n.cat || "Piping");
const ICON = {
  edit:'<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></svg>',
  x:'<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  rev:'<svg viewBox="0 0 24 24"><path d="M7 7h11l-3-3M17 17H6l3 3"/></svg>',
  del:'<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/></svg>',
  plus:'<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
};

// ---------------------------------------------------------------- talking to Streamlit (custom component protocol)
const ST = {
  live: !EMBEDDED && window.parent !== window,
  send(type, extra){ window.parent.postMessage(Object.assign({isStreamlitMessage:true, type}, extra || {}), "*"); },
  ready(){ this.send("streamlit:componentReady", {apiVersion:1}); },
  height(h){ this.send("streamlit:setFrameHeight", {height:h}); },
  value(v){ this.send("streamlit:setComponentValue", {value:v, dataType:"json"}); },
};
function emit(action, payload){
  if (!ST.live || !G.D.edit) return;
  ST.value(Object.assign({action, nonce: Date.now() + "-" + Math.random().toString(36).slice(2)}, payload));
  toast("Saving…", "busy");
}

// ---------------------------------------------------------------- state kept across data refreshes
let lastFlash = null;
let G = null;                     // the graph on screen: data, network, datasets, selection, edit state
let showLabels = true, flowOn = null;
let simple = false;               // simple view: dots and plain lines, to see just what connects to what
try { simple = localStorage.getItem("pidGraphSimple") === "1"; } catch (_) {}
const PLAIN = "#7c93b0";          // the one link colour in simple view
const hiddenCat = new Set(), hiddenKind = new Set();
const t0 = performance.now();

function nodeStyle(n, dim){
  const s = catOf(n.key), a = dim ? .14 : 1;
  const base = {id:n.id, label: labelOf(n),
    borderWidth: n.focus ? 3 : 1.5, borderWidthSelected: 3,
    font:{face:MONO, color: rgba(s.txt, dim ? .15 : 1), size:13,
          strokeWidth: 3, strokeColor: rgba("#05080f", dim ? .2 : .9)},
    color:{background: rgba(s.fill, dim ? .35 : 1), border: rgba(s.c, a),
           highlight:{background:s.fill, border:"#ffffff"}, hover:{background:s.fill, border:"#ffffff"}},
    shadow:{enabled:!dim, color: rgba(s.c, n.focus ? .9 : .55), size: n.focus ? 30 : 16, x:0, y:0}};
  if (n.key === "Equipment") Object.assign(base, {shape:"box", margin:{top:10,bottom:10,left:14,right:14},
      shapeProperties:{borderRadius:5}, font:{...base.font, size:15, strokeWidth:0}});
  else if (n.key === "Instrumentation") Object.assign(base, {shape:"ellipse", margin:{top:7,bottom:7,left:9,right:9},
      font:{...base.font, size:12.5, strokeWidth:0}});
  else if (n.key === "Valve") Object.assign(base, {shape:"diamond", size:9, font:{...base.font, size:11}});
  else if (n.key === "CtrlValve") Object.assign(base, {shape:"diamond", size:13, borderWidth: n.focus ? 3 : 2,
      font:{...base.font, size:12.5, color: rgba(s.txt, dim ? .15 : 1)}});
  else if (s.custom) Object.assign(base, n.valve ? {shape:"diamond", size:12, font:{...base.font, size:12}}
                                                 : {shape:"hexagon", size:12, font:{...base.font, size:12}});
  else if (n.key === "OffPage") Object.assign(base, {shape:"triangle", size:12, font:{...base.font, size:12}});
  else Object.assign(base, {shape:"dot", size:5.5, shadow:{enabled:false}});
  if (simple)   // same colours, but every item a dot (bigger for equipment, smallest for fittings)
    Object.assign(base, {shape:"dot", size: n.key === "Equipment" ? 11 : n.key === "Piping" ? 4.5 : 7.5,
      borderWidth: n.focus ? 3 : 1.5,
      font:{...base.font, size: n.key === "Equipment" ? 13 : 11, strokeWidth:3, color: rgba(s.txt, dim ? .15 : 1)},
      shadow:{enabled: !dim && n.key !== "Piping", color: rgba(s.c, .5), size: 10, x:0, y:0}});
  return base;
}
function labelOf(n){
  if (n.key === "Piping") return "";
  if (!showLabels && (n.key === "Valve" || n.key === "OffPage")) return "";
  if (n.key === "CtrlValve") return n.name.length > 22 ? n.name.slice(0, 20) + "…" : n.name;
  return n.key !== "Equipment" && n.name.length > 18 ? n.name.slice(0, 16) + "…" : n.name;
}
function edgeStyle(e, dim){
  const s = EDGE[e.kind];
  if (simple)   // one plain line for every kind of link
    return {id:e.id, from:e.from, to:e.to, width:1.4, dashes:false,
      color:{color:PLAIN, opacity: dim ? .06 : .7, highlight:"#ffffff", hover:"#ffffff"},
      arrows:{to:{enabled:true, scaleFactor:.45, type:"arrow"}},
      hoverWidth: 1.2, selectionWidth: 1.6, shadow:{enabled:false}};
  return {id:e.id, from:e.from, to:e.to, width:s.w, dashes:s.dash,
    color:{color:s.c, opacity: dim ? .06 : (s.flow ? .8 : .65), highlight:"#ffffff", hover:"#ffffff"},
    arrows:{to:{enabled:true, scaleFactor: s.flow ? .55 : .45, type:"arrow"}},
    hoverWidth: 1.2, selectionWidth: 1.6,
    shadow:{enabled: !dim && !!s.flow, color: rgba(s.c, .45), size: 8, x:0, y:0}};
}

// ---------------------------------------------------------------- building (and rebuilding) the graph
function build(D, prev){
  if (G){ G.net.destroy(); }
  if (!prev) $("insp").classList.remove("open");   // another view: the open inspector belongs to the old one
  const byId = {}, adj = {};
  D.nodes.forEach(n => { n.key = keyOf(n); byId[n.id] = n; adj[n.id] = {out:[], inn:[]}; });
  D.edges.forEach(e => { adj[e.from]?.out.push(e); adj[e.to]?.inn.push(e); });
  const free = !(D.options.physics && D.options.physics.enabled === false);
  const nodesDS = new vis.DataSet(D.nodes.map(n => {
    const st = nodeStyle(n, false);
    if (n.level !== undefined) st.level = n.level;
    const p = prev && prev.positions && prev.positions[n.id];
    if (p && free) Object.assign(st, {x:p.x, y:p.y});   // keep a free layout where it was
    return st;
  }));
  const edgesDS = new vis.DataSet(D.edges.map(e => edgeStyle(e, false)));
  const options = Object.assign({}, D.options, {
    autoResize:true, nodes:{chosen:true},
    edges:Object.assign({selfReference:{size:14}}, D.options.edges || {}),
    interaction:{hover:true, tooltipDelay:60, hideEdgesOnDrag: D.nodes.length > 700, multiselect:false,
                 navigationButtons:false, keyboard:false, zoomSpeed:.7},
  });
  if (prev && free && prev.positions) options.physics = Object.assign({}, options.physics, {stabilization:{iterations:60}});
  const net = new vis.Network($("net"), {nodes:nodesDS, edges:edgesDS}, options);
  G = {D, net, nodesDS, edgesDS, byId, adj, selected:null, editing:false, connect:null};
  if (flowOn === null) flowOn = D.edges.length <= 900;

  $("cN").textContent = D.nodes.length; $("cE").textContent = D.edges.length;
  $("lay").textContent = (D.level ? D.level + " · " : "") + (D.layout.startsWith("Flow") ? "Flow layout" : "Force layout") +
                         (D.edit && ST.live ? " · editable" : "");
  $("bFlow").classList.toggle("on", flowOn);
  $("bLbl").classList.toggle("on", showLabels);
  $("bSimple").classList.toggle("on", simple);
  buildLegend();
  applyFilters();
  wireNetwork(net);

  const restore = () => {
    const view = () => net.moveTo({position: prev.pos, scale: prev.scale});
    view(); setTimeout(view, 0); setTimeout(view, 60);   // vis fits the new network once more after its first draw
    if (prev.sel && byId[prev.sel]){ net.selectNodes([prev.sel]); highlight(prev.sel); openInspector(prev.sel); }
  };
  if (prev){ $("load").style.display = "none"; net.once(free ? "stabilizationIterationsDone" : "afterDrawing", restore); }
  else startLoader(free);
}
function snapshot(){
  if (!G) return null;
  return {scale: G.net.getScale(), pos: G.net.getViewPosition(), sel: G.selected, positions: G.net.getPositions()};
}

// ---------------------------------------------------------------- legend and filters
const glyph = {
  Equipment:'<rect x="2" y="2" width="18" height="10" rx="2.5" fill="#062630" stroke="#22d3ee" stroke-width="1.5"/>',
  Instrumentation:'<ellipse cx="11" cy="7" rx="9" ry="5.5" fill="#2b1f06" stroke="#fbbf24" stroke-width="1.5"/>',
  Valve:'<path d="M11 1.5 16.5 7 11 12.5 5.5 7z" fill="#0d1524" stroke="#cbd5e1" stroke-width="1.5"/>',
  Piping:'<circle cx="11" cy="7" r="3.5" fill="#1e293b" stroke="#64748b" stroke-width="1.5"/>',
  OffPage:'<path d="M11 1.5 17 12.5H5z" fill="#22103a" stroke="#c084fc" stroke-width="1.5"/>',
  CtrlValve:'<path d="M11 .5 17.5 7 11 13.5 4.5 7z" fill="#062630" stroke="#22d3ee" stroke-width="1.8"/>',
};
function glyphOf(k){
  const s = catOf(k);
  if (simple) return `<circle cx="11" cy="7" r="${k === "Equipment" ? 5.5 : k === "Piping" ? 3 : 4.5}" fill="${s.fill}" stroke="${s.c}" stroke-width="1.5"/>`;
  if (glyph[k]) return glyph[k];
  return `<path d="M6 1.5h10L21 7l-5 5.5H6L1 7z" fill="${s.fill}" stroke="${s.c}" stroke-width="1.5"/>`;
}
function edgeGlyph(k){
  const s = simple ? {c: PLAIN, w: 1.4, dash: false} : EDGE[k];
  return `<line x1="1" y1="7" x2="21" y2="7" stroke="${s.c}" stroke-width="${Math.max(s.w,1.5)}" ${s.dash ? `stroke-dasharray="${s.dash.join(" ")}"` : ""}/>` +
         `<path d="M16 3.5 21 7 16 10.5" fill="none" stroke="${s.c}" stroke-width="1.5"/>`;
}
function buildLegend(){
  const catCount = {}, kindCount = {};
  G.D.nodes.forEach(n => catCount[n.key] = (catCount[n.key] || 0) + 1);
  G.D.edges.forEach(e => kindCount[e.kind] = (kindCount[e.kind] || 0) + 1);
  let h = '<div class="rw">';
  const order = ["Equipment","CtrlValve","Instrumentation","Valve","Piping","OffPage"];
  const own = Object.keys(catCount).filter(k => !order.includes(k)).sort();
  [...order, ...own].forEach(k => { if (catCount[k])
    h += `<div class="lg${hiddenCat.has(k) ? " off" : ""}" data-cat="${esc(k)}" title="Show / hide"><svg viewBox="0 0 22 14">${glyphOf(k)}</svg>${esc(catOf(k).name)}<span class="n">${catCount[k]}</span></div>`; });
  h += '</div><div class="rw">';
  ["pipe","signal","loop","offpage","manual","candidate"].forEach(k => { if (kindCount[k])
    h += `<div class="lg${hiddenKind.has(k) ? " off" : ""}" data-kind="${k}" title="Show / hide"><svg viewBox="0 0 22 14">${edgeGlyph(k)}</svg>${EDGE[k].name}<span class="n">${kindCount[k]}</span></div>`; });
  $("lgRows").innerHTML = h + "</div>";
  $("lgRows").querySelectorAll(".lg").forEach(el => el.onclick = () => {
    const c = el.dataset.cat, k = el.dataset.kind, set = c ? hiddenCat : hiddenKind, v = c || k;
    set.has(v) ? set.delete(v) : set.add(v);
    el.classList.toggle("off", set.has(v));
    applyFilters();
  });
}
$("lgH").onclick = () => $("legend").classList.toggle("min");
function applyFilters(){
  G.nodesDS.update(G.D.nodes.map(n => ({id:n.id, hidden: hiddenCat.has(n.key)})));
  G.edgesDS.update(G.D.edges.map(e => ({id:e.id, hidden: hiddenKind.has(e.kind) || hiddenCat.has(G.byId[e.from]?.key) || hiddenCat.has(G.byId[e.to]?.key)})));
}

// ---------------------------------------------------------------- highlight on select
function highlight(id){
  G.selected = id;
  if (!id){
    G.nodesDS.update(G.D.nodes.map(n => nodeStyle(n, false)));
    G.edgesDS.update(G.D.edges.map(e => edgeStyle(e, false)));
    return;
  }
  const keep = new Set([id]), keepE = new Set();
  G.adj[id].out.forEach(e => { keep.add(e.to); keepE.add(e.id); });
  G.adj[id].inn.forEach(e => { keep.add(e.from); keepE.add(e.id); });
  G.nodesDS.update(G.D.nodes.map(n => nodeStyle(n, !keep.has(n.id))));
  G.edgesDS.update(G.D.edges.map(e => edgeStyle(e, !keepE.has(e.id))));
}

// ---------------------------------------------------------------- tooltip
const tip = $("tip");
function rows(n){
  let h = `<div class="row"><span class="k">Type</span><span class="v">${esc(words(n.type))}</span></div>`;
  if (n.desc) h += `<div class="row"><span class="k">About</span><span class="v">${esc(n.desc)}</span></div>`;
  if (n.sheets.length) h += `<div class="row"><span class="k">Sheet${n.sheets.length > 1 ? "s" : ""}</span><span class="v">${esc(n.sheets.join(", "))}</span></div>`;
  if (n.line) h += `<div class="row"><span class="k">Line</span><span class="v">${esc(n.line)}</span></div>`;
  if (n.folded.length) h += `<div class="row"><span class="k">Folded</span><span class="v">${n.folded.map(esc).join("<br>")}</span></div>`;
  if (n.instruments.length) h += `<div class="row"><span class="k">Instr.</span><span class="v">${esc(n.instruments.join(", "))}</span></div>`;
  return h;
}
function edgeRows(e){
  let h = `<div class="row"><span class="k">From</span><span class="v">${esc(G.byId[e.from]?.name)}</span></div>`;
  h += `<div class="row"><span class="k">To</span><span class="v">${esc(G.byId[e.to]?.name)}</span></div>`;
  if (e.via.length) h += `<div class="row"><span class="k">Via</span><span class="v">${esc(e.via.join(" → "))}</span></div>`;
  if (e.lines.length) h += `<div class="row"><span class="k">Lines</span><span class="v">${esc(e.lines.join(", "))}</span></div>`;
  if (e.loops.length) h += `<div class="row"><span class="k">Loops</span><span class="v">${esc(e.loops.join(", "))}</span></div>`;
  if (e.source === "drawing") h += `<div class="row"><span class="k">Origin</span><span class="v">PDF drawing check (${e.status === "candidate" ? "candidate, review in tab 6" : "approved"})</span></div>`;
  else if (e.kind === "manual") h += `<div class="row"><span class="k">Origin</span><span class="v">Added by hand</span></div>`;
  else if (e.kind === "offpage") h += `<div class="row"><span class="k">Origin</span><span class="v">Continues on another sheet</span></div>`;
  else if (e.sheet) h += `<div class="row"><span class="k">Sheet</span><span class="v">${esc(e.sheet)}</span></div>`;
  return h;
}
function openEdgeInspector(id){
  const e = G.D.edges[id]; if (!e) return;
  const s = EDGE[e.kind];
  G.selected = null;
  insp.innerHTML = `<div class="ibtns"><button class="btn" id="ix" title="Close (Esc)">${ICON.x}</button></div>
    <div class="eyebrow">Link inspector</div>
    <h3>${esc(G.byId[e.from]?.name)} → ${esc(G.byId[e.to]?.name)}</h3>
    <span class="badge" style="color:${s.c}"><span class="sw"></span>${esc(words(e.rel))}</span>
    <div class="kv">${edgeRows(e)}</div>
    <div class="conns">
      <div class="cn" data-go="${esc(e.from)}"><span class="dir">←</span><span class="nm">${esc(G.byId[e.from]?.name)}</span></div>
      <div class="cn" data-go="${esc(e.to)}"><span class="dir">→</span><span class="nm">${esc(G.byId[e.to]?.name)}</span></div>
    </div>`;
  insp.classList.add("open");
  $("ix").onclick = clearSel;
  insp.querySelectorAll("[data-go]").forEach(el => el.onclick = () => goTo(el.dataset.go));
}
function placeTip(p){
  const W = $("wrap").clientWidth, H = $("wrap").clientHeight, r = tip.getBoundingClientRect();
  let x = p.x + 16, y = p.y + 16;
  if (x + r.width > W - 8) x = p.x - r.width - 16;
  if (y + r.height > H - 8) y = p.y - r.height - 16;
  tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => { tip.classList.remove("show"); $("net").style.cursor = G && G.connect ? "crosshair" : ""; };
$("net").addEventListener("mousemove", ev => { if (tip.classList.contains("show")) placeTip({x:ev.offsetX, y:ev.offsetY}); });

function wireNetwork(net){
  net.on("hoverNode", p => {
    const n = G.byId[p.node]; if (!n) return;
    let h = `<div class="tt-h"><span class="sw" style="color:${catOf(n.key).c}"></span>${esc(n.name)}</div>${rows(n)}`;
    if (G.connect && p.node !== G.connect.from) h += `<div class="tt-go">Click to connect</div>`;
    tip.innerHTML = h; tip.classList.add("show"); placeTip(p.pointer.DOM);
    $("net").style.cursor = "pointer";
  });
  net.on("hoverEdge", p => {
    const e = G.D.edges[p.edge]; if (!e) return;
    const s = EDGE[e.kind];
    const h = `<div class="tt-h"><span class="sw" style="color:${s.c}"></span>${esc(words(e.rel))}</div>${edgeRows(e)}`;
    tip.innerHTML = h; tip.classList.add("show"); placeTip(p.pointer.DOM);
  });
  net.on("blurNode", hideTip); net.on("blurEdge", hideTip); net.on("dragStart", hideTip); net.on("zoom", hideTip);
  net.on("click", p => {
    if (G.connect){
      if (p.nodes.length && p.nodes[0] !== G.connect.from) finishConnect(p.nodes[0]);
      return;
    }
    if (p.nodes.length) { highlight(p.nodes[0]); openInspector(p.nodes[0]); }
    else if (p.edges.length) { highlight(null); openEdgeInspector(p.edges[0]); }
    else clearSel();
  });
  net.on("doubleClick", p => { if (p.nodes.length && !G.connect) goTo(p.nodes[0], 1.6); });
  net.on("afterDrawing", drawEffects);
}

// ---------------------------------------------------------------- inspector (and editing)
const insp = $("insp");
const editable = () => ST.live && G.D.edit;
const relPill = e => `<span class="rel" style="color:${EDGE[e.kind].c};background:${rgba(EDGE[e.kind].c,.1)}">${esc(words(e.rel))}</span>`;
function openInspector(id){
  const n = G.byId[id]; if (!n) return;
  const s = catOf(n.key), a = G.adj[id], ed = editable();
  const acts = e => ed ? `<span class="acts">
      <button class="mini" data-rev="${e.id}" title="Reverse direction">${ICON.rev}</button>
      <button class="mini danger" data-del="${e.id}" title="Delete this connection">${ICON.del}</button></span>` : "";
  const item = (e, other, dir) => `<div class="cn" data-go="${esc(other)}"><span class="dir">${dir}</span>` +
     `<span class="sw" style="color:${catOf(G.byId[other]?.key || "Piping").c}"></span><span class="nm">${esc(G.byId[other]?.name)}</span>${relPill(e)}${acts(e)}</div>`;
  const none = '<div class="row"><span class="v" style="color:var(--mute)">none</span></div>';
  insp.innerHTML = `<div class="ibtns">
      ${ed ? `<button class="btn" id="iEdit" title="Edit name, category, type">${ICON.edit}</button>` : ""}
      <button class="btn" id="ix" title="Close (Esc)">${ICON.x}</button></div>
    <div class="eyebrow">Asset inspector${n.focus ? " · focus" : ""}</div>
    <h3>${esc(n.name)}</h3>
    <span class="badge" style="color:${s.c}"><span class="sw"></span>${esc(s.name)}</span>
    <div class="kv" id="iKv">${rows(n)}</div>
    <div class="form" id="iForm"></div>
    <div class="conns">
      <div class="ch"><span>Downstream / out</span><span>${a.out.length}</span></div>
      ${a.out.map(e => item(e, e.to, "→")).join("") || none}
      <div class="ch"><span>Upstream / in</span><span>${a.inn.length}</span></div>
      ${a.inn.map(e => item(e, e.from, "←")).join("") || none}
      ${ed ? `<button class="wide" id="iConnect">${ICON.plus}Connect to another item</button>` : ""}
    </div>`;
  insp.classList.add("open");
  $("ix").onclick = clearSel;
  insp.querySelectorAll("[data-go]").forEach(el => el.onclick = () => goTo(el.dataset.go));
  if (!ed) return;
  $("iEdit").onclick = () => G.editing ? closeForm() : openForm(id);
  $("iConnect").onclick = () => startConnect(id);
  insp.querySelectorAll("[data-rev]").forEach(el => el.onclick = ev => {
    ev.stopPropagation();
    const e = G.D.edges[+el.dataset.rev];
    emit("reverse_rel", {src:e.from, dst:e.to, rel:e.rel});
  });
  insp.querySelectorAll("[data-del]").forEach(el => el.onclick = ev => {
    ev.stopPropagation();
    if (!el.classList.contains("sure")){ el.classList.add("sure"); el.title = "Click again to delete"; return; }
    const e = G.D.edges[+el.dataset.del];
    emit("delete_rel", {src:e.from, dst:e.to, rel:e.rel});
  });
  if (G.editing) openForm(id);
}
function openForm(id){
  const n = G.byId[id];
  G.editing = true;
  $("iEdit")?.classList.add("on");
  $("iKv").style.display = "none";
  const cats = G.D.categories && G.D.categories.length ? G.D.categories : ["Equipment", "Instrumentation", "Piping"];
  $("iForm").innerHTML = `
    <label>Name<input id="fName" value="${esc(n.name)}" spellcheck="false"></label>
    <label>Category<input id="fCat" value="${esc(n.category)}" list="catList" spellcheck="false"
      title="Pick one, or type a new category"></label>
    <datalist id="catList">${cats.map(c => `<option value="${esc(c)}">`).join("")}</datalist>
    <label>Type<input id="fType" value="${esc(n.type)}" list="typeList" spellcheck="false"></label>
    <label>Description<input id="fDesc" value="${esc(n.desc || "")}"></label>
    <datalist id="typeList">${(G.D.assetTypes || []).map(t => `<option value="${esc(t)}">`).join("")}</datalist>
    <div class="fbtns"><button class="pbtn" id="fSave">Save changes</button><button class="gbtn" id="fCancel">Cancel</button></div>
    <div class="fnote">Saved as a manual edit: kept when sheets are reloaded, undo in tab 7.</div>`;
  $("iForm").style.display = "grid";
  $("fName").focus();
  $("fCancel").onclick = closeForm;
  $("fSave").onclick = () => emit("set_asset", {uid:id, name:$("fName").value, category:$("fCat").value,
                                                type:$("fType").value, description:$("fDesc").value});
  $("iForm").onkeydown = ev => { if (ev.key === "Enter") $("fSave").click(); if (ev.key === "Escape") { ev.stopPropagation(); closeForm(); } };
}
function closeForm(){
  G.editing = false;
  $("iEdit")?.classList.remove("on");
  $("iForm").style.display = "none"; $("iForm").innerHTML = "";
  $("iKv").style.display = "";
}
function goTo(id, zoom){
  if (!G.byId[id]) return;
  hiddenCat.delete(G.byId[id].key); applyFilters();
  document.querySelectorAll(`.lg[data-cat="${CSS.escape(G.byId[id].key)}"]`).forEach(el => el.classList.remove("off"));
  G.editing = false;
  G.net.selectNodes([id]); highlight(id); openInspector(id);
  G.net.focus(id, {scale: Math.max(G.net.getScale(), zoom || 1.1), animation:{duration:600, easingFunction:"easeInOutCubic"}});
}
function clearSel(){ cancelConnect(); G.editing = false; G.net.unselectAll(); highlight(null); insp.classList.remove("open"); }

// connect mode: pick the other item on the canvas
function startConnect(id){
  const types = G.D.relTypes && G.D.relTypes.length ? G.D.relTypes : ["PIPE"];
  G.connect = {from:id, out:true};
  $("cbar").innerHTML = `<span class="cdot"></span><b>${esc(G.byId[id].name)}</b>
    <button class="gbtn" id="cDir" title="Swap direction">→ to</button>
    <span class="cmute">click an item on the graph · as</span>
    <input id="cRel" value="${types.includes("PIPE") ? "PIPE" : esc(types[0])}" list="relList" spellcheck="false">
    <datalist id="relList">${types.map(t => `<option value="${esc(t)}">`).join("")}</datalist>
    <button class="gbtn" id="cCancel">Cancel</button>`;
  $("cbar").classList.add("show");
  $("net").style.cursor = "crosshair";
  $("cDir").onclick = () => { G.connect.out = !G.connect.out; $("cDir").textContent = G.connect.out ? "→ to" : "← from"; };
  $("cCancel").onclick = cancelConnect;
}
function finishConnect(target){
  const rel = ($("cRel").value || "PIPE").trim();
  const c = G.connect;
  emit("add_rel", c.out ? {src:c.from, dst:target, rel} : {src:target, dst:c.from, rel});
  cancelConnect();
}
function cancelConnect(){
  if (!G || !G.connect) return;
  G.connect = null; $("cbar").classList.remove("show"); $("net").style.cursor = "";
}

// toast after a save
let toastTimer = null;
function toast(text, kind){
  const t = $("toast");
  t.textContent = text; t.className = "glass show " + (kind || "ok");
  clearTimeout(toastTimer);
  if (kind !== "busy") toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

// ---------------------------------------------------------------- search
const q = $("q"), hits = $("hits");
let hitIdx = 0, hitList = [];
function renderHits(){
  const s = q.value.trim().toLowerCase();
  if (!s || !G){ hits.style.display = "none"; return; }
  hitList = G.D.nodes.filter(n => n.name.toLowerCase().includes(s) || n.type.toLowerCase().includes(s))
    .sort((a, b) => (a.name.toLowerCase().indexOf(s) - b.name.toLowerCase().indexOf(s)) || a.name.localeCompare(b.name)).slice(0, 40);
  hitIdx = 0;
  hits.innerHTML = hitList.length ? hitList.map((n, i) =>
    `<div data-i="${i}" class="${i === 0 ? "act" : ""}"><span class="sw" style="color:${catOf(n.key).c}"></span><span>${esc(n.name)}</span><small>${esc(words(n.type))}</small></div>`).join("")
    : '<div style="color:var(--mute);cursor:default">No match</div>';
  hits.style.display = "block";
  hits.querySelectorAll("[data-i]").forEach(el => el.onmousedown = ev => { ev.preventDefault(); pick(+el.dataset.i); });
}
function pick(i){
  const n = hitList[i]; if (!n) return;
  q.value = n.name; hits.style.display = "none"; q.blur();
  if (G.connect) finishConnect(n.id); else goTo(n.id, 1.3);
}
q.addEventListener("input", renderHits);
q.addEventListener("focus", renderHits);
q.addEventListener("blur", () => setTimeout(() => hits.style.display = "none", 120));
q.addEventListener("keydown", ev => {
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp"){
    ev.preventDefault(); hitIdx = Math.max(0, Math.min(hitList.length - 1, hitIdx + (ev.key === "ArrowDown" ? 1 : -1)));
    hits.querySelectorAll("[data-i]").forEach((el, i) => el.classList.toggle("act", i === hitIdx));
    hits.querySelector(".act")?.scrollIntoView({block:"nearest"});
  } else if (ev.key === "Enter") pick(hitIdx);
  else if (ev.key === "Escape"){ q.value = ""; q.blur(); hits.style.display = "none"; }
});

// ---------------------------------------------------------------- toolbar
function fit(){
  G.net.fit({minZoomLevel:.05, maxZoomLevel:1.6});
  const s = G.net.getScale(), c = G.net.getViewPosition();
  // shrink a little and shift up so the top toolbar and bottom legend don't cover nodes
  const pad = $("legend").offsetHeight + 24, top = 64, H = $("wrap").clientHeight;
  const k = Math.max(.4, (H - pad - top) / H);
  G.net.moveTo({scale: s * k, position:{x:c.x, y:c.y + (pad - top) / 2 / (s * k)}, animation:{duration:500, easingFunction:"easeInOutCubic"}});
}
const zoomBy = f => G.net.moveTo({scale: G.net.getScale() * f, animation:{duration:250}});
$("bFit").onclick = () => fit(); $("bIn").onclick = () => zoomBy(1.3); $("bOut").onclick = () => zoomBy(1 / 1.3);
$("bFlow").onclick = () => { flowOn = !flowOn; $("bFlow").classList.toggle("on", flowOn); G.net.redraw(); };
$("bLbl").onclick = () => { showLabels = !showLabels; $("bLbl").classList.toggle("on", showLabels); highlight(G.selected); };
function toggleSimple(){
  simple = !simple;
  try { localStorage.setItem("pidGraphSimple", simple ? "1" : "0"); } catch (_) {}
  $("bSimple").classList.toggle("on", simple);
  highlight(G.selected);   // restyles every node and link (keeping the dimming around a selection)
  buildLegend();
}
$("bSimple").onclick = toggleSimple;
$("bShot").onclick = () => {
  const src = $("net").querySelector("canvas"), c = document.createElement("canvas");
  c.width = src.width; c.height = src.height;
  const x = c.getContext("2d"); x.fillStyle = "#05080f"; x.fillRect(0, 0, c.width, c.height); x.drawImage(src, 0, 0);
  const a = document.createElement("a"); a.download = "pid-graph.png"; a.href = c.toDataURL("image/png"); a.click();
};
if (!document.fullscreenEnabled) $("bFull").style.display = "none";
$("bFull").onclick = () => document.fullscreenElement ? document.exitFullscreen() : $("wrap").requestFullscreen().catch(() => {});
document.addEventListener("keydown", ev => {
  if (!G || ["INPUT", "SELECT", "TEXTAREA"].includes(ev.target.tagName)) {
    if (ev.key === "Escape" && G && G.connect) cancelConnect();
    return;
  }
  if (ev.key === "/"){ ev.preventDefault(); q.focus(); }
  else if (ev.key === "f" || ev.key === "F") fit();
  else if (ev.key === "s" || ev.key === "S") toggleSimple();
  else if (ev.key === "Escape") G.connect ? cancelConnect() : clearSel();
});

// ---------------------------------------------------------------- canvas effects: flow particles + focus pulse
function inside(nd, p){
  const b = nd.shape && nd.shape.boundingBox; if (!b) return false;
  return p.x > b.left - 2 && p.x < b.right + 2 && p.y > b.top - 2 && p.y < b.bottom + 2;
}
function edgeLen(eo){ const a = eo.from, b = eo.to; return Math.max(30, Math.hypot(a.x - b.x, a.y - b.y)); }
function drawEffects(ctx){
  const t = (performance.now() - t0) / 1000, net = G.net, D = G.D;
  const scale = net.getScale();
  if (flowOn){
    const body = net.body, many = D.edges.length > 300;
    ctx.save(); ctx.globalCompositeOperation = "lighter";
    for (const e of D.edges){
      const s = EDGE[e.kind]; if (!s.flow || e.from === e.to) continue;
      const eo = body.edges[e.id]; if (!eo || eo.options.hidden || !eo.from || !eo.to || eo.from.options.hidden || eo.to.options.hidden) continue;
      if (G.selected && e.from !== G.selected && e.to !== G.selected) continue;
      const len = edgeLen(eo), speed = 70, n = Math.max(1, Math.min(4, Math.round(len / 140)));
      for (let k = 0; k < n; k++){
        const ph = ((t * speed) / len + k / n + (e.id * .137)) % 1;
        let p;
        try { p = eo.edgeType.getPoint(ph); } catch (_) { p = {x: eo.from.x + (eo.to.x - eo.from.x) * ph, y: eo.from.y + (eo.to.y - eo.from.y) * ph}; }
        if (inside(eo.from, p) || inside(eo.to, p)) continue;
        const r = Math.max(1.6, 2.4 / Math.sqrt(scale));
        const pc = simple ? PLAIN : s.c;
        if (!many){ ctx.shadowColor = pc; ctx.shadowBlur = 10; }
        ctx.fillStyle = rgba(pc, .95);
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,.9)";
        ctx.beginPath(); ctx.arc(p.x, p.y, r * .45, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.restore();
  }
  const ring = (id, colour) => {
    const fn = net.body.nodes[id]; if (!fn) return;
    const w = (fn.shape.width || 30) / 2, h = (fn.shape.height || 30) / 2, base = Math.max(w, h) + 8;
    for (let k = 0; k < 2; k++){
      const ph = ((t * .6) + k * .5) % 1;
      ctx.save(); ctx.strokeStyle = rgba(colour, (1 - ph) * .8); ctx.lineWidth = 2 / Math.sqrt(scale);
      ctx.beginPath(); ctx.arc(fn.x, fn.y, base + ph * 34, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
    }
  };
  if (D.focus && G.byId[D.focus]) ring(D.focus, catOf(G.byId[D.focus].key).c);
  if (G.connect) ring(G.connect.from, "#34d399");
}
let last = 0;
(function loop(ts){
  if (G && (flowOn || G.D.focus || G.connect) && !document.hidden && ts - last > 30){ last = ts; G.net.redraw(); }
  requestAnimationFrame(loop);
})(0);

// ---------------------------------------------------------------- loader (first build only)
function startLoader(free){
  const load = $("load"), net = G.net;
  load.style.display = ""; load.classList.remove("done");
  const done = () => {
    if (load.classList.contains("done")) return;
    load.classList.add("done"); fit(); setTimeout(() => load.style.display = "none", 450);
  };
  net.on("stabilizationProgress", p => { $("ldT").textContent = "Stabilising layout"; $("ldP").style.width = (100 * p.iterations / p.total) + "%"; });
  net.once("stabilizationIterationsDone", () => { $("ldP").style.width = "100%"; setTimeout(done, 150); });
  net.once("afterDrawing", () => { if (!free) { $("ldP").style.width = "100%"; setTimeout(done, 200); } });
  setTimeout(done, 8000);
}

// ---------------------------------------------------------------- start
function setHeight(h){
  document.documentElement.style.height = document.body.style.height = h + "px";
  if (ST.live) ST.height(h);
}
if (EMBEDDED){
  build(EMBEDDED, null);
} else {
  window.addEventListener("message", ev => {
    if (!ev.data || ev.data.type !== "streamlit:render") return;
    const args = ev.data.args || {}, D = args.data;
    if (!D) return;
    setHeight(args.height || 650);
    const flash = D.flash && D.flash.id !== lastFlash ? D.flash : null;
    if (flash) lastFlash = flash.id;
    if (G && G.D.version === D.version){              // same graph (e.g. an edit that was refused): no redraw
      if (flash) toast(flash.text, flash.kind);
      return;
    }
    const keep = G && G.D.context === D.context ? snapshot() : null;
    const stayEditing = keep && G.editing && flash && flash.kind === "error";
    build(D, keep);
    if (stayEditing) G.editing = true;
    if (flash) toast(flash.text, flash.kind);
  });
  ST.ready();
}
window.pidGraph = {get network(){ return G && G.net; }, goTo:(id, z) => goTo(id, z), clearSel:() => clearSel(), fit:() => fit()};  // for the browser console
})();
</script>
</body></html>
"""
