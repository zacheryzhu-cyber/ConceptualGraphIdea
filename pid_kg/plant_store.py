"""
The plant: what is loaded, where its files are, and what goes into Neo4j. Neo4j holds ONLY the conceptual level;
the complete graph of each sheet is kept as a file.

Loading a sheet:
    DEXPI file --parse_sheet (reader + island-fix rules)--> complete graph in memory (networkx, plant_model.py)
    (optional matching PDF --crosscheck.store.run--> pdfCheck marks and drawing candidates on that graph)
    --> snapshots/<sheet>.json   the complete graph of the sheet, in the adjacency export format
    --> all snapshots together --graph_levels.build(..., "Conceptual")--> written to Neo4j (sync)

Files (under the project folder, PIDKG_DATA_DIR if set, or the root given to PlantStore):
    sources/sheets.json          every loaded sheet: plant, area, system, revision and its source files
    sources/<sheet>/             the DEXPI file and PDF as loaded (or reviewed.json for a sheet from the PDF route)
    sources/decisions.json       approved / rejected drawing candidates, keyed by the two assets' uids ("uid|uid")
    sources/links.csv            manual off-page links (optional)
    snapshots/<sheet>.json       the complete graph of the sheet

Neo4j (nothing else):
    (:Plant)-[:HAS_AREA]->(:Area)-[:HAS_SYSTEM]->(:System)-[:HAS_SHEET]->(:Sheet)-[:DEPICTS]->(node)
    (:Asset:Equipment | :Asset:Boundary {uid, name, tag, fullTag, type, category, description, sheets, folded})
        main equipment and off-page connectors (graph_levels.yaml groups main_equipment and boundary); the DEXPI
        class is also a label
    (:Asset)-[:FLOWS_TO {via, viaUids, lines, crossSheet, source, status, pdfCheck, loops, kind}]->(:Asset)
    (:Loop {uid, name, tag, type, functions, alarms, sheets})   one per InstrumentationLoopFunction
    (:Loop)-[:MEASURES]->(:Asset)          what the loop measures (a loop measuring a pipe is in FLOWS_TO.loops)
    (:Loop)-[:CONTROLS]->(:Valve | :Asset) the valve it drives (a small (:Valve) node, only for such valves), or the
                                           equipment whose drive it switches
"""
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime

import graph_levels as gl
import plant_loader as pl
import plant_model as pm
from crosscheck import store as pdf_store

PROJECT = os.path.dirname(os.path.abspath(__file__))
# where sources/ and snapshots/ live: the project folder, or PIDKG_DATA_DIR (e.g. a separate folder for a test database)
DATA_DIR = os.environ.get("PIDKG_DATA_DIR") or PROJECT
APP_LABELS = ("Asset", "Loop", "Valve", "Sheet", "System", "Area", "Plant", "OpenEnd", "ManualEdit")
CONCEPT_LABELS = ("Asset", "Loop", "Valve", "OpenEnd")
GROUP_LABEL = {"main_equipment": "Equipment", "boundary": "Boundary"}
NODE_PROPS = ("uid", "name", "tag", "fullTag", "type", "category", "description", "sheets", "folded", "lineNumber",
              "valveRole")
LINK_PROPS = ("via", "viaUids", "lines", "crossSheet", "source", "status", "pdfCheck", "loops", "sheet")
SHEET_META = ("revision", "file", "source", "issues", "pdfOpenCandidates", "unconnectedEquipment")
FROM_SNAPSHOT = "from file snapshot"


def slug(sheet):
    """File / folder name for a sheet: '=P1.H4.FB.001' -> '_P1.H4.FB.001'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(sheet)).strip(".") or "sheet"


def short_tag(name):
    """'P002 (P1.H4.T1)' -> 'P002'."""
    return str(name).split(" (")[0]


class PlantStore:
    def __init__(self, db, root=None):
        self.db = db
        self.root = os.path.abspath(root or DATA_DIR)
        self.sources = os.path.join(self.root, "sources")
        self.snapshots = os.path.join(self.root, "snapshots")

    # ----------------------------------------------------------------------- small files

    def _read(self, path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False, default=str)
        os.replace(tmp, path)

    def _abs(self, rel):
        return os.path.join(self.root, rel) if rel else None

    def _rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def registry(self):
        """{sheet: entry} of every loaded sheet, in load order."""
        return {e["sheet"]: e for e in self._read(os.path.join(self.sources, "sheets.json"), {"sheets": []})["sheets"]}

    def _save_registry(self, reg):
        self._write(os.path.join(self.sources, "sheets.json"), {"sheets": list(reg.values())})

    def decisions(self):
        return self._read(os.path.join(self.sources, "decisions.json"), {})

    def _save_decisions(self, d):
        self._write(os.path.join(self.sources, "decisions.json"), d)

    @property
    def links_csv(self):
        return os.path.join(self.sources, "links.csv")

    def set_links_csv(self, data):
        """Keep a links.csv (bytes) for every later load and rebuild."""
        os.makedirs(self.sources, exist_ok=True)
        with open(self.links_csv, "wb") as f:
            f.write(data)

    def snapshot_path(self, sheet):
        return os.path.join(self.snapshots, slug(sheet) + ".json")

    def snapshot(self, sheet):
        return self._read(self.snapshot_path(sheet), None)

    def _copy_in(self, path, folder):
        """Copy a source file into sources/<sheet>/ (unless it is already there); its path relative to the root."""
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, os.path.basename(path))
        if os.path.abspath(path) != os.path.abspath(dest):
            shutil.copy2(path, dest)
        return self._rel(dest)

    # ----------------------------------------------------------------------- loading

    def load_sheet(self, dexpi_path, sheet, plant=pl.DEFAULT_PLANT, area=None, system=None, revision=None,
                   pdf_path=None, pdf_name=None, sync=True):
        """Load one DEXPI file as a sheet (replacing a sheet of that name), with its matching PDF if given.
        Returns {sheet, assets, connections, issues, pdf (summary or None), pdfLine, candidates, unconnected}."""
        folder = os.path.join(self.sources, slug(sheet))
        keep = {os.path.abspath(p) for p in (dexpi_path, pdf_path) if p}
        if os.path.isdir(folder):   # a new revision: drop the old source files of this sheet
            for f in os.listdir(folder):
                if os.path.abspath(os.path.join(folder, f)) not in keep:
                    os.remove(os.path.join(folder, f))
        entry = {"sheet": sheet, "plant": plant or pl.DEFAULT_PLANT, "area": area or pl.UNASSIGNED,
                 "system": system or pl.UNASSIGNED, "revision": revision or "", "source": "dexpi",
                 "file": os.path.basename(dexpi_path), "dexpi": self._copy_in(dexpi_path, folder),
                 "pdf": self._copy_in(pdf_path, folder) if pdf_path else None,
                 "pdfName": (pdf_name or os.path.basename(pdf_path)) if pdf_path else None,
                 "loaded": datetime.now().isoformat(timespec="seconds")}

        details = {}
        assets, edges, issues = pl.parse_sheet(self._abs(entry["dexpi"]), sheet, details)
        G = pm.sheet_graph(assets, edges, sheet)
        decisions = self.decisions()
        pdf = None
        if entry["pdf"]:
            pdf = pdf_store.run(G, sheet, self._abs(entry["dexpi"]), self._abs(entry["pdf"]), decisions,
                                entry["pdfName"])
        pm.apply_decisions(G, decisions)
        return self._finish(entry, G, issues, details.get("open_ends", []), pdf, sync)

    def load_reviewed(self, sheet, assets, edges, issues, plant, area, system, revision, file, sync=True):
        """A sheet from the PDF / image route, after review: its rows are its source (sources/<sheet>/reviewed.json)."""
        folder = os.path.join(self.sources, slug(sheet))
        path = os.path.join(folder, "reviewed.json")
        self._write(path, {"assets": assets, "edges": edges, "issues": list(issues)})
        entry = {"sheet": sheet, "plant": plant or pl.DEFAULT_PLANT, "area": area or pl.UNASSIGNED,
                 "system": system or pl.UNASSIGNED, "revision": revision or "", "source": "pdf", "file": file,
                 "reviewed": self._rel(path), "loaded": datetime.now().isoformat(timespec="seconds")}
        G = pm.sheet_graph(assets, edges, sheet)
        pm.apply_decisions(G, self.decisions())
        return self._finish(entry, G, list(issues), [], None, sync)

    def _reload(self, entry, sync=False):
        """Load a registered sheet again from its saved source files."""
        if entry.get("source") == "pdf":
            rows = self._read(self._abs(entry["reviewed"]), None)
            if rows is None:
                raise FileNotFoundError(entry["reviewed"])
            return self.load_reviewed(entry["sheet"], rows["assets"], rows["edges"], rows["issues"], entry["plant"],
                                      entry["area"], entry["system"], entry["revision"], entry["file"], sync=sync)
        return self.load_sheet(self._abs(entry["dexpi"]), entry["sheet"], entry["plant"], entry["area"],
                               entry["system"], entry["revision"], self._abs(entry.get("pdf")), entry.get("pdfName"),
                               sync=sync)

    def _finish(self, entry, G, issues, open_ends, pdf, sync):
        sheet = entry["sheet"]
        meta = {**entry, "issues": issues, "open_ends": open_ends, "pdf": pdf}
        self._write(self.snapshot_path(sheet), pm.to_export(G, sheet=meta, level="Complete"))
        reg = self.registry()
        reg.pop(sheet, None)
        reg[sheet] = entry
        self._save_registry(reg)
        open_candidates = sum(d.get("status") == "candidate" for _, _, d in pm.drawing_links(G))
        print(f"  sheet {sheet}: {G.number_of_nodes()} assets, {G.number_of_edges()} connections, "
              f"{len(issues)} issues; {pdf_store.summary_line(pdf)}")
        result = {"sheet": sheet, "assets": G.number_of_nodes(), "connections": G.number_of_edges(),
                  "issues": issues, "pdf": pdf, "pdfLine": pdf_store.summary_line(pdf),
                  "candidates": open_candidates}
        if sync:
            self.sync()
            result["unconnected"] = self.unconnected(sheet)
        return result

    def remove_sheet(self, sheet, sync=True):
        """Remove a sheet: its snapshot, its source files, and (on sync) its nodes and links in Neo4j."""
        reg = self.registry()
        reg.pop(sheet, None)
        self._save_registry(reg)
        for path in (self.snapshot_path(sheet),):
            if os.path.exists(path):
                os.remove(path)
        shutil.rmtree(os.path.join(self.sources, slug(sheet)), ignore_errors=True)
        if sync:
            self.sync()

    def delete_all(self, keep_decisions=True):
        """Delete the whole plant: Neo4j, snapshots and saved sources (and the review decisions unless kept)."""
        self.reset_db()
        shutil.rmtree(self.snapshots, ignore_errors=True)
        for name in os.listdir(self.sources) if os.path.isdir(self.sources) else []:
            if name == "decisions.json" and keep_decisions:
                continue
            path = os.path.join(self.sources, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    # ----------------------------------------------------------------------- rebuild / migration

    def reset_db(self):
        """Delete everything this app keeps in Neo4j. Manual edits of the former full graph are saved to
        sources/manual_edits_backup.json first (editing on the full graph is gone; the records are kept for later)."""
        edits = self.db.run("MATCH (e:ManualEdit) RETURN properties(e) AS e")
        if edits:
            self._write(os.path.join(self.sources, "manual_edits_backup.json"), [r["e"] for r in edits])
        for label in APP_LABELS:
            self.db.run(f"MATCH (n:{label}) DETACH DELETE n")

    def adopt_loaded_sheets(self, upload_dir="uploads"):
        """Sheets loaded into Neo4j before this store existed (full graph in Neo4j) and not in sources/ yet: register
        them from their files in upload_dir and from the hierarchy, and keep their review decisions. A sheet from the
        PDF route keeps its reviewed items and links, read from Neo4j. Returns (adopted, problems)."""
        reg = self.registry()
        rows = self.db.run("""MATCH (s:Sheet) WHERE s.file IS NOT NULL
                              OPTIONAL MATCH (p:Plant)-[:HAS_AREA]->(ar:Area)-[:HAS_SYSTEM]->(sy:System)-[:HAS_SHEET]->(s)
                              RETURN s.name AS sheet, s.file AS file, s.revision AS revision, s.source AS source,
                                     s.pdfPath AS pdfPath, s.pdfFile AS pdfFile, s.pdfApproved AS ok,
                                     s.pdfRejected AS no, p.name AS plant, ar.name AS area, sy.name AS system""")
        decisions = self.decisions()
        adopted, problems = [], []
        for r in rows:
            for keys, word in ((r["ok"], "approved"), (r["no"], "rejected")):
                for k in keys or []:
                    decisions.setdefault(k, {"decision": word, "sheet": r["sheet"], "at": "adopted"})
            if r["sheet"] in reg:
                continue
            folder = os.path.join(self.sources, slug(r["sheet"]))
            base = {"sheet": r["sheet"], "plant": r["plant"] or pl.DEFAULT_PLANT, "area": r["area"] or pl.UNASSIGNED,
                    "system": r["system"] or pl.UNASSIGNED, "revision": r["revision"] or "", "file": r["file"]}
            if r["source"] == "pdf":
                assets = [x["a"] for x in self.db.run(
                    "MATCH (:Sheet {name: $s})-[:DEPICTS]->(a:Asset) RETURN properties(a) AS a", s=r["sheet"])]
                edges = [{"src": x["src"], "dst": x["dst"], "rel": x["rel"], "props": x["props"]} for x in self.db.run(
                    """MATCH (a:Asset)-[r]->(b:Asset) WHERE r.sheet = $s
                       RETURN a.uid AS src, b.uid AS dst, type(r) AS rel, properties(r) AS props""", s=r["sheet"])]
                path = os.path.join(folder, "reviewed.json")
                self._write(path, {"assets": assets, "edges": edges, "issues": []})
                reg[r["sheet"]] = {**base, "source": "pdf", "reviewed": self._rel(path), "loaded": "adopted"}
                adopted.append(r["sheet"])
                continue
            dexpi = os.path.join(self.root, upload_dir, r["file"])
            if not os.path.exists(dexpi):
                problems.append(f"{r['sheet']}: source file {r['file']} not found in {upload_dir}/; load it again")
                continue
            pdf = r["pdfPath"] if r["pdfPath"] and os.path.exists(r["pdfPath"]) else None
            if r["pdfPath"] and not pdf:
                problems.append(f"{r['sheet']}: its PDF {r['pdfFile']} is no longer there; rebuilt without it")
            reg[r["sheet"]] = {**base, "source": "dexpi", "dexpi": self._copy_in(dexpi, folder),
                               "pdf": self._copy_in(pdf, folder) if pdf else None,
                               "pdfName": r["pdfFile"] if pdf else None, "loaded": "adopted"}
            adopted.append(r["sheet"])
        self._save_registry(reg)
        self._save_decisions(decisions)
        links = os.path.join(self.root, upload_dir, "links.csv")
        if os.path.exists(links) and not os.path.exists(self.links_csv):
            shutil.copy2(links, self.links_csv)
        return adopted, problems

    def rebuild(self, progress=None):
        """Reset Neo4j and load every sheet again from sources/, so the database holds the conceptual level only.
        Sheets still only in the old full-graph database are adopted first. Returns (results, problems)."""
        _, problems = self.adopt_loaded_sheets()
        self.reset_db()
        shutil.rmtree(self.snapshots, ignore_errors=True)
        reg = self.registry()
        results = []
        for i, entry in enumerate(reg.values()):
            try:
                results.append(self._reload(entry))
            except Exception as exc:
                problems.append(f"{entry['sheet']}: {exc.__class__.__name__}: {exc}")
            if progress:
                progress((i + 1) / max(len(reg), 1))
        self.sync()
        for r in results:
            r["unconnected"] = self.unconnected(r["sheet"])
        return results, problems

    # ----------------------------------------------------------------------- the plant in memory

    def snapshots_of(self, sheets=None):
        reg = self.registry()
        out = []
        for sheet in reg:
            if sheets is None or sheet in sheets:
                snap = self.snapshot(sheet)
                if snap:
                    out.append(snap)
        return out

    def plant(self, sheets=None, candidates=False):
        """(networkx graph, merge issues) of the given sheets (default all), from their snapshots."""
        return pm.plant_graph(self.snapshots_of(sheets), self.links_csv, candidates)

    def export(self, sheets=None, candidates=False, view=None):
        """The complete graph of the given sheets as an adjacency export (from the snapshots, not Neo4j)."""
        G, _ = self.plant(sheets, candidates)
        reg = self.registry()
        return pm.to_export(G, plant=sorted({reg[s]["plant"] for s in reg if sheets is None or s in sheets}),
                            view=view or {"mode": "Whole plant"}, origin=FROM_SNAPSHOT)

    # ----------------------------------------------------------------------- Neo4j: the conceptual level

    def sync(self):
        """Write the conceptual level of all loaded sheets to Neo4j, replacing what was there."""
        export = self.export()
        concept = gl.build(export, "Conceptual")
        write_conceptual(self.db, concept, export, self.registry(), self._sheet_extras())
        return concept

    def _sheet_extras(self):
        out = {}
        for sheet in self.registry():
            snap = self.snapshot(sheet) or {"sheet": {}, "adjacency": {}}
            meta = snap["sheet"]
            cands = sum(r["properties"].get("status") == "candidate" for rels in snap["adjacency"].values()
                        for r in rels)
            out[sheet] = {"issues": meta.get("issues") or [], "pdf": meta.get("pdf"), "pdfOpenCandidates": cands}
        return out

    def unconnected(self, sheet=None):
        """Names of main equipment (on this sheet) with no FLOWS_TO in Neo4j."""
        rows = self.db.run("""MATCH (a:Asset:Equipment) WHERE NOT (a)-[:FLOWS_TO]-()
                                AND ($sheet IS NULL OR $sheet IN a.sheets)
                              RETURN a.name AS name ORDER BY name""", sheet=sheet)
        return [r["name"] for r in rows]

    def stats(self):
        """The Plant tab's counts: sheets, assets and equipment in Neo4j; cross-sheet links = the connections between
        sheets in the complete graph (off-page arrows and pipe lines going on to another sheet), as before."""
        one = lambda s: self.db.run(s)[0]["n"]
        G, _ = self.plant()
        return {
            "Sheets": one("MATCH (s:Sheet) RETURN count(s) AS n"),
            "Assets": one("MATCH (a:Asset) RETURN count(a) AS n"),
            "Equipment": one("MATCH (a:Asset:Equipment) RETURN count(a) AS n"),
            "Cross-sheet links": sum(d["type"] == "CONTINUES_TO" or bool(d.get("crossSheet"))
                                     for _, _, d in G.edges(data=True)),
        }

    # ----------------------------------------------------------------------- reviewing drawing candidates

    def decide(self, src, dst, decision):
        """Approve or reject the drawing link between src and dst (either direction): kept in decisions.json, applied
        to every snapshot that has it, then written to Neo4j."""
        key = pm.pair_key(src, dst)
        d = self.decisions()
        d[key] = {"decision": decision, "at": datetime.now().isoformat(timespec="seconds")}
        sheets = []
        for sheet in self.registry():
            snap = self.snapshot(sheet)
            if not snap:
                continue
            G = pm.from_export(snap)
            if not any(dd.get("source") == "drawing" for _, _, _, dd in pm.edges_between(G, src, dst, "PIPE")):
                continue
            sheets.append(sheet)
            pm.apply_decisions(G, d)
            self._write(self.snapshot_path(sheet), pm.to_export(G, sheet=snap["sheet"], level="Complete"))
        d[key]["sheet"] = ", ".join(sheets)
        self._save_decisions(d)
        self.sync()
        return sheets

    def approve(self, src, dst):
        return self.decide(src, dst, "approved")

    def reject(self, src, dst):
        return self.decide(src, dst, "rejected")

    def drawing_links(self, sheet=None):
        """Drawing links (candidates and approved) of the snapshots, with both assets' names, classes, full labels."""
        rows = []
        for s in self.registry():
            if sheet and s != sheet:
                continue
            snap = self.snapshot(s)
            if not snap:
                continue
            nodes = snap["nodes"]
            for u, rels in snap["adjacency"].items():
                for r in rels:
                    p = r["properties"]
                    if p.get("source") != "drawing":
                        continue
                    a, b = nodes.get(u, {}), nodes.get(r["to"], {})
                    rows.append({"sheet": s, "status": p.get("status"), "src": u, "dst": r["to"],
                                 "from_name": a.get("name", u), "from_class": a.get("type"),
                                 "from_label": a.get("fullTag"), "to_name": b.get("name", r["to"]),
                                 "to_class": b.get("type"), "to_label": b.get("fullTag")})
        return rows

    def pdf_checks(self):
        """One row per sheet: its PDF check summary (None: loaded without drawing check) and open candidates."""
        rows = []
        for sheet, extra in self._sheet_extras().items():
            rows.append({"sheet": sheet, **dict.fromkeys(pdf_store.SHEET_PROPS), **(extra["pdf"] or {}),
                         "openCandidates": extra["pdfOpenCandidates"]})
        return rows

    def describe_pairs(self, pairs_json, sheet):
        """[(uid, uid)] of a sheet's check -> rows with both assets' names, classes and full labels."""
        pairs = json.loads(pairs_json or "[]")
        nodes = (self.snapshot(sheet) or {}).get("nodes", {})
        rows = []
        for a, b in pairs:
            x, y = nodes.get(a, {}), nodes.get(b, {})
            rows.append({"item A": x.get("name", a), "class A": x.get("type"), "full label A": x.get("fullTag"),
                         "item B": y.get("name", b), "class B": y.get("type"), "full label B": y.get("fullTag"),
                         "src": a, "dst": b})
        return rows

    # ----------------------------------------------------------------------- detail views

    def level_view(self, level, sheets=None, candidates=False, view=None):
        """Process or Complete (or Conceptual) computed from the snapshots of `sheets` (default all)."""
        return gl.build(self.export(sheets, candidates, view), level)

    def path_detail(self, src, dst, via_uids):
        """The Process view of one FLOWS_TO link: its two ends and the valves it passes, from the snapshots."""
        process = self.level_view("Process")
        keep = {src, dst} | {u for u in via_uids or [] if u in process["nodes"]}
        return gl.subset(process, keep)

    # ----------------------------------------------------------------------- reports

    def report(self, out_dir=None):
        """Load issues, off-page arrows with no other end, tags on several sheets (from the snapshots)."""
        G, merge = self.plant()
        rows = []
        for sheet, extra in self._sheet_extras().items():
            for issue in extra["issues"]:
                rows.append({"sheet": sheet, "check": "load", "item": "", "detail": issue})
        rows += [{"sheet": m["sheet"], "check": "type conflict (merged)", "item": m["item"], "detail": m["detail"]}
                 for m in merge]
        for u, n in sorted(G.nodes(data=True), key=lambda x: (x[1].get("sheet") or "", x[1].get("name") or "")):
            if n.get("type") in pl.OFFPAGE_TYPES and not any(d["type"] == "CONTINUES_TO" for _, _, d in
                                                             list(G.in_edges(u, data=True)) + list(G.out_edges(u, data=True))):
                rows.append({"sheet": n.get("sheet") or "", "check": "unresolved off-page arrow", "item": n.get("name"),
                             "detail": f"line {n.get('lineNumber') or '?'} not found on any other loaded sheet"})
        for u, n in sorted(G.nodes(data=True), key=lambda x: x[1].get("name") or ""):
            if n.get("tagged") and len(n.get("sheets") or []) > 1:
                rows.append({"sheet": ", ".join(n["sheets"]), "check": "tag on several sheets (merged)",
                             "item": n.get("name"), "detail": "one asset node shared by these sheets"})
        if out_dir:
            import csv
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "validation_report.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["sheet", "check", "item", "detail"])
                w.writeheader()
                w.writerows(rows)
        return rows


# --------------------------------------------------------------------------- writing the conceptual level

def _row(n, keys=NODE_PROPS):
    return {k: n[k] for k in keys if n.get(k) not in (None, "", [])}


def write_conceptual(db, concept, export, registry, extras):
    """Replace the conceptual level in Neo4j: hierarchy, main equipment and off-page connectors, loops, the valves
    loops drive, and FLOWS_TO / MEASURES / CONTROLS. concept: graph_levels.build(export, "Conceptual")."""
    config = gl.load_config()
    nodes = export["nodes"]
    group = {u: gl.classify(n, config)[0] for u, n in nodes.items()}
    for label in CONCEPT_LABELS:
        db.run(f"MATCH (n:{label}) DETACH DELETE n")

    # ---- hierarchy: Plant -> Area -> System -> Sheet, one Sheet per registered sheet
    db.run("MATCH (s:Sheet) WHERE NOT s.name IN $names DETACH DELETE s", names=list(registry))
    for sheet, e in registry.items():
        extra = extras.get(sheet, {})
        props = {"revision": e.get("revision") or "", "file": e.get("file"), "source": e.get("source"),
                 "issues": extra.get("issues") or [], "pdfOpenCandidates": extra.get("pdfOpenCandidates", 0)}
        pdf = {k: v for k, v in (extra.get("pdf") or {}).items() if k in pdf_store.SHEET_PROPS and v is not None}
        db.run("MATCH (:System)-[h:HAS_SHEET]->(s:Sheet {name: $sheet}) DELETE h", sheet=sheet)
        db.run(f"""
            MERGE (p:Plant {{name: $plant}})
            MERGE (ar:Area {{uid: $plant + '/' + $area}}) SET ar.name = $area
            MERGE (p)-[:HAS_AREA]->(ar)
            MERGE (sy:System {{uid: $plant + '/' + $area + '/' + $system}}) SET sy.name = $system
            MERGE (ar)-[:HAS_SYSTEM]->(sy)
            MERGE (s:Sheet {{name: $sheet}})
            SET {', '.join(f's.{p} = null' for p in pdf_store.SHEET_PROPS)}
            SET s += $props, s += $pdf
            MERGE (sy)-[:HAS_SHEET]->(s)
            """, plant=e["plant"], area=e["area"], system=e["system"], sheet=sheet, props=props, pdf=pdf)
    db.run("MATCH (y:System) WHERE NOT (y)-[:HAS_SHEET]->() DETACH DELETE y")
    db.run("MATCH (x:Area) WHERE NOT (x)-[:HAS_SYSTEM]->() DETACH DELETE x")
    db.run("MATCH (p:Plant) WHERE NOT (p)-[:HAS_AREA]->() DETACH DELETE p")

    # ---- main equipment and off-page connectors
    by_label = defaultdict(list)
    for u, n in concept["nodes"].items():
        row = _row({**n, "tag": short_tag(n.get("name") or u)})
        by_label[(GROUP_LABEL.get(n["group"], "Equipment"), pl.clean_label(n.get("type") or "Unknown"))].append(row)

    # ---- loops: one node each, with its functions and alarms
    members, alarms = defaultdict(list), defaultdict(list)
    loop_of = {}
    for src, rels in export["adjacency"].items():
        for r in rels:
            if r["type"] == "IS_A_COLLECTION_INCLUDING" and group.get(src) == "loop":
                loop_of[r["to"]] = src
                if group.get(r["to"]) == "instrument":
                    members[src].append(gl.short(nodes[r["to"]].get("fullTag") or nodes[r["to"]].get("name")))
    for src, rels in export["adjacency"].items():
        for r in rels:
            if r["type"] == "HAS_ALARM" and src in loop_of:
                alarms[loop_of[src]].append(str(nodes[r["to"]].get("name")))
    loops = []
    for u, n in nodes.items():
        if group[u] == "loop":
            loops.append({**_row(n, ("uid", "type", "description", "sheets")),
                          "name": gl.short(n.get("name") or u), "tag": n.get("fullTag") or n.get("name") or u,
                          "functions": sorted(set(members[u])), "alarms": sorted(set(alarms[u]))})

    # ---- what each loop measures or drives
    measures, controls, valves = [], [], {}
    kept = concept["nodes"]
    for sig, targets in concept.get("loopTargets", {}).items():
        if group.get(sig) != "loop":
            continue
        for t in targets:
            target = t["target"]
            if t["acts"] and group.get(target) == "valve":
                valves[target] = {**_row(nodes[target]), "tag": short_tag(nodes[target].get("name") or target)}
                controls.append({"loop": sig, "target": target, "rel": t["rel"]})
            elif target in kept:
                (controls if t["acts"] else measures).append({"loop": sig, "target": target, "rel": t["rel"]})
            # a loop measuring a pipe (target: a valve or fitting on it) is on the FLOWS_TO links through it (loops)

    for (glabel, tlabel), rows in by_label.items():
        db.run(f"UNWIND $rows AS row CREATE (a:Asset:{glabel}:{tlabel}) SET a = row", rows=rows)
    for v in valves.values():
        db.run(f"CREATE (v:Valve:{pl.clean_label(v.get('type') or 'Valve')}) SET v = $row", row=v)
    if loops:
        db.run("UNWIND $rows AS row CREATE (l:Loop) SET l = row", rows=loops)
    db.run("""MATCH (n) WHERE (n:Asset OR n:Loop OR n:Valve)
              UNWIND n.sheets AS sheet MATCH (s:Sheet {name: sheet}) MERGE (s)-[:DEPICTS]->(n)""")

    # ---- FLOWS_TO
    flows = []
    for src, rels in concept["adjacency"].items():
        for r in rels:
            if r["type"] in gl.FLOW_RELS:
                p = r["properties"]
                props = {k: p[k] for k in LINK_PROPS if p.get(k) not in (None, "", [])}
                props["crossSheet"] = bool(p.get("crossSheet"))
                props["source"] = p.get("source") or "dexpi"
                props["kind"] = r["type"]
                flows.append({"src": src, "dst": r["to"], "props": props})
    db.run("""UNWIND $rows AS row MATCH (a:Asset {uid: row.src}), (b:Asset {uid: row.dst})
              CREATE (a)-[f:FLOWS_TO]->(b) SET f = row.props""", rows=flows)
    db.run("""UNWIND $rows AS row MATCH (l:Loop {uid: row.loop}), (a:Asset {uid: row.target})
              CREATE (l)-[:MEASURES {via: row.rel}]->(a)""", rows=measures)
    db.run("""UNWIND $rows AS row MATCH (l:Loop {uid: row.loop}) MATCH (t {uid: row.target}) WHERE t:Valve OR t:Asset
              CREATE (l)-[:CONTROLS {via: row.rel}]->(t)""", rows=controls)
    db.run("""MATCH (s:Sheet) OPTIONAL MATCH (s)-[:DEPICTS]->(a:Asset:Equipment) WHERE NOT (a)-[:FLOWS_TO]-()
              WITH s, count(a) AS n SET s.unconnectedEquipment = n""")


def conceptual_view(db, loops=False):
    """The conceptual level as stored in Neo4j, in graph_levels.build's format (for the graph view and its export).
    loops: also the Loop and Valve nodes with their MEASURES / CONTROLS links (drawn dotted); otherwise the loops are
    listed on the equipment they measure or drive (instruments)."""
    nodes = {r["a"]["uid"]: {**r["a"], "group": "boundary" if "Boundary" in r["labels"] else "main_equipment"}
             for r in db.run("MATCH (a:Asset) RETURN properties(a) AS a, labels(a) AS labels")}
    adjacency = {u: [] for u in nodes}
    for r in db.run("""MATCH (a:Asset)-[f:FLOWS_TO]->(b:Asset)
                       RETURN a.uid AS src, b.uid AS dst, b.name AS name, properties(f) AS p ORDER BY src, dst"""):
        adjacency[r["src"]].append({"to": r["dst"], "toName": r["name"], "type": "FLOWS_TO", "properties": r["p"]})
    signal = db.run("""MATCH (l:Loop)-[x:MEASURES|CONTROLS]->(t)
                       RETURN l.uid AS loop, l.name AS name, l.functions AS f, type(x) AS rel, t.uid AS target,
                              t.name AS tname, labels(t) AS labels ORDER BY name""")
    if loops:
        for r in db.run("MATCH (n) WHERE n:Loop OR n:Valve RETURN properties(n) AS n, labels(n) AS labels"):
            n = r["n"]
            loop = "Loop" in r["labels"]
            nodes[n["uid"]] = {**n, "group": "loop" if loop else "valve",
                               "category": "Instrumentation" if loop else n.get("category"),
                               "name": n.get("name") if not loop or not n.get("functions")
                               else f"{n['name']} {'/'.join(n['functions'])}"}
            adjacency.setdefault(n["uid"], [])
        for r in signal:
            adjacency[r["loop"]].append({"to": r["target"], "toName": r["tname"], "type": r["rel"],
                                         "properties": {"loopLink": True}})
    else:
        for r in signal:
            if r["target"] in nodes:
                label = f"{r['name']} {'/'.join(r['f'] or [])}".strip()
                nodes[r["target"]].setdefault("instruments", []).append(label)
    n_rels = sum(len(v) for v in adjacency.values())
    return {"format": "pid-kg-adjacency", "version": 1, "exported": datetime.now().isoformat(timespec="seconds"),
            "plant": [r["name"] for r in db.run("MATCH (p:Plant) RETURN p.name AS name ORDER BY name")],
            "level": "Conceptual", "origin": "Neo4j", "directed": True,
            "counts": {"nodes": len(nodes), "relationships": n_rels}, "nodes": nodes, "adjacency": adjacency,
            "representative": {u: u for u in nodes}}




def label_counts(db):
    """Node counts by label and relationship counts by type, as Neo4j Browser's database panel shows them."""
    labels = db.run("""MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY n DESC, label""")
    rels = db.run("""MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC, type""")
    return labels, rels
