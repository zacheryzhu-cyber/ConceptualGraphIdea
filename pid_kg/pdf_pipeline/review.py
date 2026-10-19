"""Turn the reviewed tables from the app back into assets + connections for the plant loader."""
import re


def asset_table(result):
    return [{"keep": True, "name": a["name"], "category": a["category"], "tag": a["tagged"],
             "type": a["type"], "uid": a["uid"]} for a in result["assets"]]


def edge_table(result):
    name = {a["uid"]: a["name"] for a in result["assets"]}
    return [{"keep": True, "from": name.get(e["src"], e["src"]), "to": name.get(e["dst"], e["dst"]),
             "direction": "known" if e["direction"] == "known" else "unknown (check)",
             "src": e["src"], "dst": e["dst"]} for e in result["edges"]]


def apply_review(result, assets_rows, edges_rows, sheet):
    """assets_rows / edges_rows: the edited tables (lists of dicts). Returns (assets, edges, issues)."""
    original = {a["uid"]: a for a in result["assets"]}
    new_uid, assets = {}, []
    for row in assets_rows:
        if not row["keep"]:
            continue
        a = dict(original[row["uid"]])
        name = str(row["name"]).strip()
        tagged = bool(row["tag"]) and bool(name)
        compact = re.sub(r"\s+", "", name)
        uid = ("TAG:" + compact) if tagged else (
            row["uid"] if not row["uid"].startswith("TAG:") else f"{sheet}::{name}")
        a.update(uid=uid, name=compact if tagged else name,
                 category=row["category"], tagged=tagged, reviewed=True)
        a.pop("box", None)
        if not tagged:
            a["sheet"] = sheet
        new_uid[row["uid"]] = uid
        assets.append(a)
    # two rows renamed to the same tag become one asset
    merged = {}
    for a in assets:
        merged.setdefault(a["uid"], a)
    edges = []
    for row in edges_rows:
        if not row["keep"] or row["src"] not in new_uid or row["dst"] not in new_uid:
            continue
        src, dst = new_uid[row["src"]], new_uid[row["dst"]]
        if src == dst:
            continue
        edges.append({"src": src, "dst": dst, "rel": "PIPE",
                      "props": {"dexpiType": "Line (from PDF)", "source": "pdf", "reviewed": True,
                                "direction": "known" if row["direction"] == "known" else "unknown"}})
    issues = [i for i in result.get("issues", []) if "need review" not in i] + ["from PDF, reviewed in app"]
    return list(merged.values()), edges, issues
