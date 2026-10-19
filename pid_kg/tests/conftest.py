"""Shared test setup: the project folder on sys.path, a throwaway Neo4j for the tests that load sheets, and a plant
store whose files (sources/, snapshots/) live in a temporary folder.

The database tests DELETE everything the app keeps in Neo4j. They run only against PIDKG_TEST_NEO4J_URI (default
bolt://localhost:7688, a separate test container), never against NEO4J_URI, and they skip if that database holds
manual edits or sheets other than the ones they load. Start a test database with, for example:
    docker run -d --name neo4j-pidkg-test -p 7688:7687 -e NEO4J_AUTH=none neo4j:5
"""
import json
import os
import sys
import warnings
from types import SimpleNamespace

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
warnings.filterwarnings("ignore")

import plant_loader as pl  # noqa: E402
import plant_store as ps  # noqa: E402


PDF_CHECK_PREFIX = "expected_pdf_crosscheck_"   # expected results of the PDF drawing check, not a sheet set


def project_path(rel):
    return os.path.join(PROJECT, rel)


def sheet_sets():
    """tests/expected_<sheetset>.json files (expected results of loading a set of sheets)."""
    return sorted(f for f in os.listdir(os.path.join(PROJECT, "tests"))
                  if f.startswith("expected_") and f.endswith(".json") and not f.startswith(PDF_CHECK_PREFIX))


def load_json(name):
    with open(os.path.join(PROJECT, "tests", name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def test_db():
    uri = os.environ.get("PIDKG_TEST_NEO4J_URI", "bolt://localhost:7688")
    if uri == os.environ.get("NEO4J_URI"):
        pytest.skip("PIDKG_TEST_NEO4J_URI is the main database; the tests only run on a throwaway one")
    try:
        db = pl.Db(SimpleNamespace(uri=uri, user=os.environ.get("PIDKG_TEST_NEO4J_USER", "neo4j"),
                                   password=os.environ.get("PIDKG_TEST_NEO4J_PASSWORD", "test"),
                                   database=None, test_db=None))
    except Exception as exc:
        pytest.skip(f"no test Neo4j at {uri}: {exc}")
    db.setup()
    yield db
    db.close()


def known_test_sheets():
    names = set()
    for f in os.listdir(os.path.join(PROJECT, "tests")):
        if f.startswith("expected_") and f.endswith(".json"):
            names |= set(load_json(f)["sheets"])
    return names


@pytest.fixture
def clean_db(test_db):
    """The test database, emptied; refuses a database that looks like real work."""
    if test_db.run("MATCH (e:ManualEdit) RETURN count(e) AS n")[0]["n"]:
        pytest.skip("test database holds manual edits: not a throwaway database")
    others = {r["n"] for r in test_db.run("MATCH (s:Sheet) RETURN s.name AS n")} - known_test_sheets()
    if others:
        pytest.skip(f"test database holds other sheets ({', '.join(sorted(others))}): not a throwaway database")
    for label in ps.APP_LABELS:
        test_db.run(f"MATCH (n:{label}) DETACH DELETE n")
    return test_db


@pytest.fixture
def store(clean_db, tmp_path):
    """A plant store on the emptied test database, with its sources/ and snapshots/ in a temporary folder."""
    return ps.PlantStore(clean_db, tmp_path)


def load(store, expected, order, pdfs=None):
    """Load the sheets of a set in the given order (pdfs: {sheet: pdf path}); returns {sheet: load result}."""
    pdfs = pdfs or {}
    out = {}
    for sheet in order:
        out[sheet] = store.load_sheet(project_path(expected["sheets"][sheet]), sheet,
                                      pdf_path=project_path(pdfs[sheet]) if sheet in pdfs else None)
    return out


def approve_all(store):
    for c in store.drawing_links():
        if c["status"] == "candidate":
            store.approve(c["src"], c["dst"])
