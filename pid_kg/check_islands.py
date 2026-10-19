"""
Find stand-alone islands in an adjacency export (tab 4 · Graph, "Download adjacency list (JSON)", or
plant_store.PlantStore.export: the complete graph from the sheets' snapshots).

An island is a group of assets connected to each other, in either direction, by any relationship, and to nothing
else. A healthy plant graph has few, large islands; many small ones usually mean links the loader did not read.

    python check_islands.py adjacency_Whole-plant_....json            (lists every island; small ones in full)
    python check_islands.py export.json --small 3                     (groups of up to 3 nodes are listed in full)

The app's Validation tab uses the same functions (components, small_islands).
"""
import argparse
import json
import sys
from collections import Counter


def components(export):
    """Connected groups of node uids (relationship direction ignored), largest first."""
    nbrs = {u: set() for u in export["nodes"]}
    for src, rels in export["adjacency"].items():
        for r in rels:
            if src in nbrs and r["to"] in nbrs:
                nbrs[src].add(r["to"])
                nbrs[r["to"]].add(src)
    seen, groups = set(), []
    for start in sorted(nbrs):
        if start in seen:
            continue
        group, todo = [], [start]
        seen.add(start)
        while todo:
            u = todo.pop()
            group.append(u)
            for v in nbrs[u] - seen:
                seen.add(v)
                todo.append(v)
        groups.append(sorted(group))
    return sorted(groups, key=lambda g: (-len(g), g))


def describe(export, uid):
    n = export["nodes"][uid]
    return {"uid": uid, "type": n.get("type", ""), "name": n.get("name", uid), "sheets": ", ".join(n.get("sheets", []))}


def small_islands(export, max_size=3):
    """Every island of max_size nodes or fewer: [{'island': k, 'size': n, 'type', 'name', 'sheets', 'uid'}, ...]."""
    rows = []
    for k, group in enumerate(components(export), 1):
        if len(group) <= max_size:
            rows += [{"island": k, "size": len(group), **describe(export, u)} for u in group]
    return rows


def main():
    ap = argparse.ArgumentParser(description="List the stand-alone islands in a pid-kg adjacency export.")
    ap.add_argument("export", help="adjacency JSON file")
    ap.add_argument("--small", type=int, default=3, help="list islands of up to this many nodes in full (default 3)")
    args = ap.parse_args()
    with open(args.export, encoding="utf-8") as f:
        export = json.load(f)
    groups = components(export)
    singles = sum(len(g) == 1 for g in groups)
    print(f"{len(export['nodes'])} nodes in {len(groups)} islands, {singles} of them single nodes")
    for k, group in enumerate(groups, 1):
        sheets = sorted({s for u in group for s in export["nodes"][u].get("sheets", [])})
        types = Counter(export["nodes"][u].get("type", "") for u in group)
        print(f"\nisland {k}: {len(group)} nodes, sheets {', '.join(sheets) or '-'}")
        if len(group) <= args.small:
            for u in group:
                d = describe(export, u)
                print(f"    {d['type']}: {d['name']}")
        else:
            equipment = sorted(export["nodes"][u].get("name", u) for u in group
                               if export["nodes"][u].get("category") == "Equipment")
            print("    types: " + ", ".join(f"{t} {n}" for t, n in types.most_common()))
            print("    equipment: " + ", ".join(equipment))
    return 0


if __name__ == "__main__":
    sys.exit(main())
