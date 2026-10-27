"""
Look and feel for app.py: page styling, header, stat cards and status pills.
Siemens iX dark styling; colours and fonts come from .streamlit/config.toml; this adds what the theme can't do.
"""
import base64
from html import escape

import streamlit as st

CSS = """
<style>
:root{
  /* Siemens iX dark theme tokens */
  --pk-cyan:#00ffb9; --pk-violet:#a78bfa; --pk-amber:#ffd732; --pk-pink:#ff6b9d; --pk-green:#00cc00;
  --pk-line:#2b3a63; --pk-panel:#0f1b3f; --pk-mute:#a0a5b5; --pk-bg:#000028;
  --pk-mono:"Siemens Sans","Segoe UI",Arial,sans-serif;
}
.stApp{background:var(--pk-bg)}
[data-testid="stHeader"]{background:transparent}
[data-testid="stAppDeployButton"],[data-testid="stToolbar"]{display:none}
.block-container{padding:2.2rem 1.6rem 1rem;max-width:none}
.stApp{background:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px) 0 0/32px 32px,
  linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px) 0 0/32px 32px,var(--pk-bg)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#2b3a63;border-radius:2px;border:2px solid #000028}
::-webkit-scrollbar-thumb:hover{background:#4a5b87}

/* headings */
[data-testid="stMarkdownContainer"] h4{font-size:.8rem;letter-spacing:.04em;text-transform:uppercase;color:var(--pk-mute);margin-top:1.2rem}
[data-testid="stCaptionContainer"]{color:var(--pk-mute)}

/* header */
.pk-hero{padding:20px 24px;margin:0 0 18px;border:1px solid var(--pk-line);border-radius:4px;background:var(--pk-panel)}
.pk-eyebrow{display:flex;align-items:center;gap:9px;font:600 11px/1 var(--pk-mono);letter-spacing:.08em;
  text-transform:uppercase;color:var(--pk-cyan)}
.pk-dot{width:7px;height:7px;border-radius:50%;background:var(--pk-cyan);animation:pk-pulse 1.8s infinite}
@keyframes pk-pulse{0%,100%{opacity:1}50%{opacity:.25}}
.pk-hero h1{margin:.55rem 0 .3rem;padding:0;font-size:2rem;font-weight:700;line-height:1.1}
.pk-grad{color:var(--pk-cyan)}
.pk-hero p{margin:0;color:var(--pk-mute);font-size:.98rem}
.pk-chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.pk-chip{display:inline-flex;align-items:center;gap:7px;padding:3px 10px;border-radius:4px;font:600 11px var(--pk-mono);
  letter-spacing:.02em;color:#c3c7d3;border:1px solid currentColor;background:transparent}
.pk-chip i{width:6px;height:6px;border-radius:50%;background:currentColor}
.pk-ok{color:var(--pk-green)} .pk-wait{color:var(--pk-amber)} .pk-bad{color:#ff2640}

/* stat cards */
.pk-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:6px 0 10px}
.pk-stat{position:relative;padding:14px 16px;border-radius:4px;border:1px solid var(--pk-line);background:var(--pk-panel);transition:.15s}
.pk-stat:hover{border-color:var(--c)}
.pk-stat::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--c)}
.pk-stat .k{display:flex;align-items:center;gap:8px;font:600 11px var(--pk-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--pk-mute)}
.pk-stat .k img{width:15px;height:15px}
.pk-stat .v{margin-top:6px;font:700 1.9rem/1 var(--pk-mono);color:#fff}

/* tabs: iX underlined tabs */
[data-testid="stTabs"] [role="tablist"]{gap:0;padding:0;border:0;border-bottom:1px solid var(--pk-line);border-radius:0;
  background:transparent;overflow-x:auto;box-shadow:none}
[data-testid="stTabs"] [role="tab"]{height:40px;padding:0 16px;margin:0;border-radius:0;background:transparent;border:0;
  border-bottom:2px solid transparent;display:flex;align-items:center;transition:background .15s;white-space:nowrap}
[data-testid="stTabs"] [role="tab"] p{font-family:var(--pk-mono);font-size:.9rem;font-weight:400;color:#c3c7d3}
[data-testid="stTabs"] [role="tab"]:hover{background:rgba(255,255,255,.08)}
[data-testid="stTabs"] [role="tab"]:hover p{color:#fff}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{border-bottom-color:var(--pk-cyan)}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p{color:#fff;font-weight:600}
[data-testid="stTabs"] .react-aria-SelectionIndicator,[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"]{display:none}
[data-testid="stTabs"] [role="tabpanel"]{padding-top:1.1rem}

/* buttons: iX primary (filled accent) / secondary (outlined) */
[data-testid="stBaseButton-primary"]{position:relative;background:linear-gradient(90deg,rgba(0,255,185,.16),rgba(0,255,185,.04));
  border:1px solid var(--pk-cyan);border-left-width:4px;border-radius:2px;color:var(--pk-cyan);font-weight:600;
  letter-spacing:.06em;text-transform:uppercase;font-size:.82rem;transition:.2s;
  clip-path:polygon(0 0,calc(100% - 12px) 0,100% 12px,100% 100%,0 100%);box-shadow:inset 0 0 18px rgba(0,255,185,.08)}
[data-testid="stBaseButton-primary"]:hover{background:var(--pk-cyan);color:#000028;box-shadow:inset 0 0 0 1px var(--pk-cyan)}
[data-testid="stBaseButton-primary"]:hover *{color:#000028}
[data-testid="stBaseButton-secondary"]{background:transparent;border:1px solid var(--pk-cyan);color:var(--pk-cyan);transition:.15s}
[data-testid="stBaseButton-secondary"]:hover{background:rgba(0,255,185,.12);border-color:var(--pk-cyan);color:var(--pk-cyan)}

/* inputs, uploader, tables, expanders, alerts */
[data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within{box-shadow:0 0 0 1px var(--pk-cyan)}
[data-testid="stFileUploaderDropzone"]{border:1px dashed var(--pk-line);background:var(--pk-panel)}
[data-testid="stFileUploaderDropzone"]:hover{border-color:var(--pk-cyan)}
[data-testid="stDataFrame"],[data-testid="stDataFrameResizable"]{border-radius:4px;box-shadow:0 0 0 1px var(--pk-line)}
[data-testid="stExpander"] details{background:var(--pk-panel);border-color:var(--pk-line);border-radius:4px}
[data-testid="stExpander"] summary:hover{color:var(--pk-cyan)}
[data-testid="stVerticalBlockBorderWrapper"]{background:var(--pk-panel)}
[data-testid="stProgress"] [role="progressbar"]>div>div>div{background:var(--pk-cyan)}

/* horizontal radios as iX toggle buttons */
[data-testid="stRadioGroup"][aria-orientation="horizontal"]{gap:6px}
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]{margin:0;padding:6px 12px 6px 10px;
  border-radius:4px;border:1px solid var(--pk-line);background:transparent;transition:.15s}
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]:hover{background:rgba(255,255,255,.08)}
[data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"][data-selected="true"]{
  border-color:var(--pk-cyan);background:rgba(0,255,185,.12)}

/* graph iframe */
iframe[title="st.iframe"],[data-testid="stIFrame"],[data-testid="stCustomComponentV1"]{border-radius:4px}

/* sidebar */
[data-testid="stSidebar"]{border-right:1px solid var(--pk-line)}
[data-testid="stSidebar"] h2{font-size:.8rem;letter-spacing:.06em;text-transform:uppercase;color:var(--pk-mute)}
.pk-brand{display:flex;align-items:center;gap:11px;padding:4px 0 16px;margin-bottom:6px;border-bottom:1px solid var(--pk-line)}
.pk-brand img{width:34px;height:34px;flex:none}
.pk-brand b{display:block;font:700 14px var(--pk-mono);color:#fff}
.pk-brand span{font:400 11px var(--pk-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--pk-mute)}

/* disconnect button, pinned into the header's top-right corner */
[data-testid="stMainBlockContainer"]{position:relative}
[data-testid="stLayoutWrapper"]:has(> .st-key-pk_signout){display:contents}  /* no empty row under the header */
.st-key-pk_signout{position:absolute;top:calc(2.2rem + 14px);right:calc(var(--pk-pad,5rem) + 16px);width:auto!important;z-index:2}
.st-key-pk_signout button{font-family:var(--pk-mono);font-size:.75rem;color:var(--pk-mute);
  padding:4px 10px;border:1px solid var(--pk-line);border-radius:4px;background:transparent}
.st-key-pk_signout button:hover{color:#ff2640;border-color:#ff2640}
</style>"""


ICONS = {
    "Sheets": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    "Assets": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="9" cy="18" r="2.5"/><path d="m8 7.5 7.6 0M7 8.3l1.6 7.3M16.6 10l-5.6 6.4"/>',
    "Equipment": '<rect x="4" y="6" width="16" height="12" rx="2"/><path d="M8 10h8M8 14h5"/>',
    "Cross-sheet links": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
}
ACCENTS = ["#00ffb9", "#a78bfa", "#ffd732", "#00bedc"]
LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" fill="none" stroke-width="2">
  <defs><linearGradient id="pkg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#00ffb9"/><stop offset="1" stop-color="#00bedc"/></linearGradient></defs>
  <rect x="2" y="2" width="36" height="36" rx="9" stroke="url(#pkg)" fill="none"/>
  <path d="M12 13 20 20 28 14M20 20v8M12 13l-.5 13M28 14l.5 12" stroke="url(#pkg)" stroke-linecap="round"/>
  <circle cx="12" cy="13" r="3" fill="#00332b" stroke="#00ffb9"/><circle cx="28" cy="14" r="3" fill="#1c1640" stroke="#a78bfa"/>
  <circle cx="20" cy="20" r="3.2" fill="#00332b" stroke="#00ffb9"/><circle cx="20" cy="28" r="2.6" fill="#332b0a" stroke="#ffd732"/>
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
