"""Every DEXPI 1.3 file in the project still loads, and yields at least as many links as before the dexpi_links pass
(tests/baseline_links.json). DEXPI 1.3 = <PlantInformation Application="Dexpi" ApplicationVersion="1.3...">; other
Proteus / XML exports are not checked. Files found that are not in the baseline must load too.

    python -m pytest tests/test_generality.py -q -s        (-s prints which rules triggered on each file)
"""
import glob
import os

import pytest
from defusedxml import ElementTree as ET

import plant_loader as pl
from conftest import PROJECT, load_json

BASELINE = load_json("baseline_links.json")["links"]
FOLDERS = ("..", "", "uploads", "test_sheets")   # the project's parent holds the source COMOS exports


def is_dexpi_13(path):
    with open(path, "rb") as fh:
        if not pl.looks_like_dexpi(fh.read(4000)):
            return False
    info = ET.fromstring(pl.read_xml_text(path)).find("PlantInformation")
    return (info is not None and (info.get("Application") or "").lower() == "dexpi"
            and (info.get("ApplicationVersion") or "").startswith("1.3"))


def dexpi_files():
    found = set()
    for folder in FOLDERS:
        for f in glob.glob(os.path.join(PROJECT, folder, "*")):
            if os.path.isfile(f) and not f.lower().endswith((".csv", ".pdf", ".png", ".jpg", ".jpeg", ".json", ".md")) \
                    and is_dexpi_13(f):
                found.add(os.path.relpath(f, PROJECT).replace(os.sep, "/"))
    return sorted(found)


@pytest.mark.parametrize("rel", dexpi_files())
def test_loads_and_keeps_links(rel):
    before = BASELINE.get(rel, 0)
    details = {}
    assets, edges, issues = pl.parse_sheet(os.path.join(PROJECT, rel), pl.sheet_name_from_file(rel), details)
    rules = {k: v for k, v in details["rules"].items() if v}
    print(f"\n{rel}: {before} -> {len(edges)} links; rules triggered: {rules or 'none'}")
    assert len(edges) >= before
