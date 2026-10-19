"""
Look and feel for app.py: page styling, header, stat cards and status pills.
Colours and fonts come from .streamlit/config.toml; this adds what the theme can't do.
"""
import base64
from html import escape

import streamlit as st

CSS = """
<style>
:root{
  --pk-cyan:#22d3ee; --pk-violet:#c084fc; --pk-amber:#fbbf24; --pk-pink:#f472b6; --pk-green:#34d399;
  --pk-line:rgba(56,189,248,.16); --pk-panel:rgba(14,22,38,.72); --pk-mute:#7c8aa5;
  --pk-mono:"JetBrains Mono","Cascadia Mono",Consolas,monospace;
}
/* backdrop: soft glows + engineering grid */
.stApp{
  background:
    radial-gradient(1100px 520px at 8% -10%,rgba(34,211,238,.10),transparent 60%),
    radial-gradient(900px 520px at 105% 10%,rgba(192,132,252,.08),transparent 60%),
    linear-gradient(rgba(148,163,184,.045) 1px,transparent 1px) 0 0/40px 40px,
    linear-gradient(90deg,rgba(148,163,184,.045) 1px,transparent 1px) 0 0/40px 40px,
    #060a13;
  background-attachment:fixed;
}
[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:2.2rem;max-width:1500px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#1c2a40;border-radius:10px;border:2px solid #060a13}
::-webkit-scrollbar-thumb:hover{background:#27405f}

/* headings */
h3{letter-spacing:-.01em}
[data-testid="stHeadingWithActionElements"] h3,[data-testid="stMarkdownContainer"] h3{
  display:flex;align-items:center;gap:.6rem}
[data-testid="stHeadingWithActionElements"] h3::before{content:"";width:4px;height:1.05em;border-radius:3px;
  background:linear-gradient(var(--pk-cyan),var(--pk-violet));box-shadow:0 0 12px rgba(34,211,238,.6)}
[data-testid="stMarkdownContainer"] h4{font-family:var(--pk-mono);font-size:.8rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--pk-mute);margin-top:1.2rem}
[data-testid="stCaptionContainer"]{color:var(--pk-mute)}

/* header */
.pk-hero{position:relative;padding:22px 26px;margin:0 0 18px;border:1px solid var(--pk-line);border-radius:16px;overflow:hidden;
  background:linear-gradient(120deg,rgba(34,211,238,.08),rgba(192,132,252,.06) 55%,rgba(6,10,19,.2)),var(--pk-panel);
  box-shadow:0 20px 60px -30px rgba(34,211,238,.35)}
.pk-hero::after{content:"";position:absolute;right:-40px;top:-60px;width:360px;height:220px;opacity:.5;pointer-events:none;
  background:radial-gradient(circle at 1px 1px,rgba(103,232,249,.5) 1px,transparent 1.6px) 0 0/16px 16px;
  mask-image:radial-gradient(closest-side,#000,transparent)}
.pk-eyebrow{display:flex;align-items:center;gap:9px;font:600 11px/1 var(--pk-mono);letter-spacing:.18em;
  text-transform:uppercase;color:var(--pk-cyan)}
.pk-dot{width:7px;height:7px;border-radius:50%;background:var(--pk-cyan);box-shadow:0 0 10px var(--pk-cyan);
  animation:pk-pulse 1.8s infinite}
@keyframes pk-pulse{0%,100%{opacity:1}50%{opacity:.25}}
.pk-hero h1{margin:.55rem 0 .3rem;padding:0;font-size:2.15rem;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.pk-grad{background:linear-gradient(90deg,#67e8f9,#a5b4fc 55%,#e879f9);-webkit-background-clip:text;background-clip:text;color:transparent}
.pk-hero p{margin:0;color:#9fb0c8;font-size:.98rem}
.pk-chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.pk-chip{display:inline-flex;align-items:center;gap:7px;padding:4px 10px;border-radius:999px;font:600 11px var(--pk-mono);
  letter-spacing:.04em;color:#a9b8cf;border:1px solid var(--pk-line);background:rgba(6,10,19,.5)}
.pk-chip i{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}
.pk-ok{color:var(--pk-green)} .pk-wait{color:var(--pk-amber)} .pk-bad{color:#f87171}

/* stat cards */
.pk-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:6px 0 10px}
.pk-stat{position:relative;padding:14px 16px;border-radius:12px;border:1px solid var(--pk-line);overflow:hidden;
  background:linear-gradient(160deg,rgba(255,255,255,.03),transparent 60%),var(--pk-panel);transition:.2s}
.pk-stat:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--c) 55%,transparent);
  box-shadow:0 12px 30px -16px var(--c)}
.pk-stat::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--c);box-shadow:0 0 14px var(--c)}
.pk-stat .k{display:flex;align-items:center;gap:8px;font:600 10.5px var(--pk-mono);letter-spacing:.14em;text-transform:uppercase;color:var(--pk-mute)}
.pk-stat .k img{width:15px;height:15px}
.pk-stat .v{margin-top:6px;font:700 1.9rem/1 var(--pk-mono);color:#f1f5f9;text-shadow:0 0 18px color-mix(in srgb,var(--c) 45%,transparent)}

/* tabs as a segmented console */
[data-testid="stTabs"] [role="tablist"]{gap:4px;padding:5px;border:1px solid var(--pk-line);border-radius:12px;
  background:var(--pk-panel);backdrop-filter:blur(8px);overflow-x:auto;box-shadow:none}
[data-testid="stTabs"] [role="tab"]{height:38px;padding:0 14px;margin:0;border-radius:8px;background:transparent;border:0;
  display:flex;align-items:center;transition:background .15s,box-shadow .15s;white-space:nowrap}
[data-testid="stTabs"] [role="tab"] p{font-family:var(--pk-mono);font-size:.8rem;font-weight:600;letter-spacing:.03em;color:#9fb0c8}
[data-testid="stTabs"] [role="tab"]:hover{background:rgba(34,211,238,.07)}
[data-testid="stTabs"] [role="tab"]:hover p{color:#e2e8f0}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{background:linear-gradient(180deg,rgba(34,211,238,.18),rgba(34,211,238,.06));
  box-shadow:inset 0 0 0 1px rgba(34,211,238,.4),0 0 20px -6px rgba(34,211,238,.65)}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p{color:#a5f3fc}
[data-testid="stTabs"] .react-aria-SelectionIndicator,[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none}
[data-testid="stTabs"] [role="tabpanel"]{padding-top:1.1rem}

/* buttons */
[data-testid="stBaseButton-primary"]{background:linear-gradient(90deg,#06b6d4,#8b5cf6);border:0;color:#fff;font-weight:600;
  box-shadow:0 8px 24px -10px rgba(34,211,238,.8);transition:.2s}
[data-testid="stBaseButton-primary"]:hover{filter:brightness(1.12);box-shadow:0 10px 30px -8px rgba(139,92,246,.8);color:#fff}
[data-testid="stBaseButton-secondary"]{background:rgba(14,22,38,.8);transition:.2s}
[data-testid="stBaseButton-secondary"]:hover{border-color:var(--pk-cyan);color:var(--pk-cyan);box-shadow:0 0 0 3px rgba(34,211,238,.1)}

/* inputs, uploader, tables, expanders, alerts */
[data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within{box-shadow:0 0 0 3px rgba(34,211,238,.14)}
[data-testid="stFileUploaderDropzone"]{border:1px dashed rgba(34,211,238,.35);
  background:repeating-linear-gradient(-45deg,rgba(34,211,238,.025) 0 10px,transparent 10px 20px),rgba(14,22,38,.6)}
[data-testid="stFileUploaderDropzone"]:hover{border-color:var(--pk-cyan)}
[data-testid="stDataFrame"],[data-testid="stDataFrameResizable"]{border-radius:10px;box-shadow:0 0 0 1px var(--pk-line),0 14px 40px -24px rgba(34,211,238,.35)}
[data-testid="stExpander"] details{background:var(--pk-panel);border-color:var(--pk-line)}
[data-testid="stExpander"] summary:hover{color:var(--pk-cyan)}
[data-testid="stAlert"]{backdrop-filter:blur(6px)}
[data-testid="stVerticalBlockBorderWrapper"]{background:var(--pk-panel)}
[data-testid="stProgress"] [role="progressbar"]>div>div>div{background:linear-gradient(90deg,var(--pk-cyan),var(--pk-violet))}

/* horizontal radios as pill toggles */
[data-testid="stRadioGroup"][aria-orientation="horizontal"]{gap:6px}
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]{margin:0;padding:6px 12px 6px 10px;
  border-radius:8px;border:1px solid var(--pk-line);background:rgba(14,22,38,.7);transition:.15s}
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]:hover{border-color:rgba(34,211,238,.45)}
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"][data-selected="true"]{
  border-color:rgba(34,211,238,.6);background:rgba(34,211,238,.1);box-shadow:0 0 16px -6px rgba(34,211,238,.7)}

/* graph iframe */
iframe[title="st.iframe"],[data-testid="stIFrame"],[data-testid="stCustomComponentV1"]{border-radius:14px}

/* sidebar */
[data-testid="stSidebar"]{border-right:1px solid var(--pk-line)}
[data-testid="stSidebar"] h2{font-family:var(--pk-mono);font-size:.78rem;letter-spacing:.16em;text-transform:uppercase;color:var(--pk-mute)}
.pk-brand{display:flex;align-items:center;gap:11px;padding:4px 0 16px;margin-bottom:6px;border-bottom:1px solid var(--pk-line)}
.pk-brand img{width:34px;height:34px;flex:none;filter:drop-shadow(0 0 8px rgba(34,211,238,.55))}
.pk-brand b{display:block;font:700 14px var(--pk-mono);letter-spacing:.04em;color:#e6f6ff}
.pk-brand span{font:500 10px var(--pk-mono);letter-spacing:.16em;text-transform:uppercase;color:var(--pk-mute)}

/* disconnect button, pinned into the header's top-right corner */
[data-testid="stMainBlockContainer"]{position:relative}
[data-testid="stLayoutWrapper"]:has(> .st-key-pk_signout){display:contents}  /* no empty row under the header */
.st-key-pk_signout{position:absolute;top:calc(2.2rem + 14px);right:calc(var(--pk-pad,5rem) + 16px);width:auto!important;z-index:2}
.st-key-pk_signout button{font-family:var(--pk-mono);font-size:.75rem;letter-spacing:.06em;color:var(--pk-mute);
  padding:4px 10px;border:1px solid var(--pk-line);border-radius:999px;background:rgba(6,10,19,.5)}
.st-key-pk_signout button:hover{color:#f87171;border-color:rgba(248,113,113,.5)}
</style>
"""

ICONS = {
    "Sheets": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    "Assets": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="9" cy="18" r="2.5"/><path d="m8 7.5 7.6 0M7 8.3l1.6 7.3M16.6 10l-5.6 6.4"/>',
    "Equipment": '<rect x="4" y="6" width="16" height="12" rx="2"/><path d="M8 10h8M8 14h5"/>',
    "Cross-sheet links": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
}
ACCENTS = ["#22d3ee", "#c084fc", "#fbbf24", "#f472b6"]
LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" fill="none" stroke-width="2">
  <defs><linearGradient id="pkg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#c084fc"/></linearGradient></defs>
  <rect x="2" y="2" width="36" height="36" rx="9" stroke="url(#pkg)" fill="rgba(34,211,238,.08)"/>
  <path d="M12 13 20 20 28 14M20 20v8M12 13l-.5 13M28 14l.5 12" stroke="url(#pkg)" stroke-linecap="round"/>
  <circle cx="12" cy="13" r="3" fill="#062630" stroke="#22d3ee"/><circle cx="28" cy="14" r="3" fill="#22103a" stroke="#c084fc"/>
  <circle cx="20" cy="20" r="3.2" fill="#062630" stroke="#67e8f9"/><circle cx="20" cy="28" r="2.6" fill="#2b1f06" stroke="#fbbf24"/>
</svg>"""


def _img(svg):
    """SVG as a data-URI image: st.html sanitises inline <svg>, but keeps <img>."""
    return f'<img alt="" src="data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}">'


def _icon(name, colour):
    return _img(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{colour}" '
                f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS.get(name, "")}</svg>')


def inject():
    st.html(CSS)


def sidebar_brand():
    st.html(f'<div class="pk-brand">{_img(LOGO)}<div><b>P&amp;ID · KG</b><span>Plant graph console</span></div></div>')


def hero(title, accent, subtitle, chips=()):
    chip_html = "".join(f'<span class="pk-chip pk-{kind}"><i></i>{escape(text)}</span>' for text, kind in chips)
    st.html(f"""<div class="pk-hero">
      <div class="pk-eyebrow"><span class="pk-dot"></span>Plant digital twin · P&amp;ID → knowledge graph</div>
      <h1>{escape(title)} <span class="pk-grad">{escape(accent)}</span></h1>
      <p>{escape(subtitle)}</p>
      {f'<div class="pk-chips">{chip_html}</div>' if chips else ''}
    </div>""")


def stat_cards(values):
    cards = "".join(
        f'<div class="pk-stat" style="--c:{ACCENTS[i % len(ACCENTS)]}">'
        f'<div class="k">{_icon(k, ACCENTS[i % len(ACCENTS)])}{escape(k)}</div>'
        f'<div class="v">{v:,}</div></div>'
        for i, (k, v) in enumerate(values.items()))
    st.html(f'<div class="pk-stats">{cards}</div>')
