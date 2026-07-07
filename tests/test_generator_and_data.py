"""Synthetic generator, fixture, citation, and schema-validity tests."""
import glob
import json
import re
from pathlib import Path

import pytest

from template_forge.generator import synthetic

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
DATA = ROOT / "data"

# fiction-reserved / allowed tokens
ALLOWED_NAME_TOK = {
    "doe", "roe", "poe", "public", "coe", "loe", "noe", "moe",
    "acme", "widget", "example", "placeholder", "sample", "fictional",
    "anytown", "standard",
}
FIRST_TOK = {f.lower() for f in synthetic._FIRST}
GEO_TOK = {
    "maine", "hampshire", "new", "massachusetts", "cumberland", "york",
    "penobscot", "kennebec", "knox", "waldo", "brunswick", "berwick",
    "registry", "deeds", "county", "state", "court", "superior", "district",
    "probate", "street", "avenue", "road", "lane", "drive", "way", "boulevard",
    "place", "north", "south", "east", "west", "llc", "inc", "trust",
    "association", "cooperative", "company", "holdings", "llp", "notary",
    "officer", "clerk", "attorney", "representative", "personal", "real",
    "estate", "of", "the", "and", "town", "city",
}


def test_synthetic_matter_uses_fiction_conventions():
    m = synthetic.generate_matter(seed=3)
    assert re.search(r"555-01\d\d", m["party_a_phone"])
    assert m["party_a_email"].endswith("@example.com")
    assert m["party_a_ssn"].startswith("900-")
    assert "Book" in m["registry_ref"] and "Page" in m["registry_ref"]
    last = m["party_a_name"].split()[-1].lower()
    assert last in ALLOWED_NAME_TOK


def test_synthetic_is_deterministic():
    assert synthetic.generate_matter(seed=7) == synthetic.generate_matter(seed=7)


def test_fixtures_are_valid_json_and_name_safe():
    files = glob.glob(str(FIXTURES / "**" / "*.json"), recursive=True)
    assert len(files) >= 150
    name_run = re.compile(r"[A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+)+")
    suspicious = []
    for fn in files:
        data = json.loads(Path(fn).read_text())

        def walk(o, key=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, k)
            elif isinstance(o, list):
                for v in o:
                    walk(v, key)
            elif isinstance(o, str) and "name" in key.lower():
                for run in name_run.findall(o):
                    toks = set(re.findall(r"[a-z]+", run.lower()))
                    if not (toks & (ALLOWED_NAME_TOK | FIRST_TOK)) and not (toks <= GEO_TOK):
                        suspicious.append((Path(fn).name, run))

        walk(data)
    assert not suspicious, f"non-fictional names in fixtures: {suspicious[:10]}"


def test_no_firm_markers_in_fixtures():
    marker = re.compile(r"bedard|bobrow|207-?439|3813|/home/user|/mnt|192\.168", re.I)
    for fn in glob.glob(str(FIXTURES / "**" / "*.json"), recursive=True):
        assert not marker.search(Path(fn).read_text()), fn


def test_citation_table_is_public_law_only():
    lines = (DATA / "citations" / "authority.jsonl").read_text().splitlines()
    assert len(lines) > 100
    for line in lines:
        rec = json.loads(line)
        # firm citation->block linkage must NOT ship
        assert "blocks" not in rec
        assert "occurs_in" not in rec
        # public-law attribution present
        assert rec.get("source_url")
        assert rec.get("verified_on")


def test_json_schemas_are_valid_draft202012():
    jsonschema = pytest.importorskip("jsonschema")
    for name in ("block", "manifest", "slot"):
        schema = json.loads((DATA / "schema" / f"{name}.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)


def test_clause_function_taxonomy_present():
    tax = json.loads((DATA / "schema" / "clause_functions.json").read_text())
    assert len(tax["functions"]) >= 30
    assert "choice_of_law" in tax["functions"]
