"""Tests for the additive jurisdiction hook (engine/jurisdiction.py).

The hook lets template-forge pull citations + a counting/service profile from a
published jurisdiction module (``legal_jurisdictions``) when one is installed for a
requested code, falling back to the bundled ``data/citations/authority.jsonl``. It
must be strictly additive: no code / no module / an uninstalled code → bundled table,
unchanged; an installed-but-unpopulated scaffold → refuse (never silent-wrong).
"""
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "src" / "template_forge"
sys.path.insert(0, str(PKG / "engine"))

import jurisdiction as juris  # noqa: E402


def test_no_jurisdiction_uses_bundled_table():
    """No jurisdiction arg → the bundled reference table, exactly as before."""
    recs = juris.resolve_authority_records()
    assert isinstance(recs, list) and recs
    # Bundled table records carry the 'citation' field.
    assert all("citation" in r for r in recs)


def test_bundled_table_loads():
    recs = juris.load_bundled_authority()
    assert juris.bundled_authority_path().exists()
    assert len(recs) >= 100  # the published table has 172 records


def test_uninstalled_code_falls_back_to_bundled():
    """A code with no installed module → graceful fallback to the bundled table."""
    recs = juris.resolve_authority_records("US-ZZ")
    assert recs == juris.load_bundled_authority()
    assert juris.module_available("US-ZZ") is False


# The tests ABOVE (bundled table / no-jurisdiction / uninstalled-code fallback) MUST
# run even when legal_jurisdictions is absent — they exercise the OPTIONAL-dependency
# degradation path. So we do NOT importorskip at module scope (that would skip the
# whole file, including those). Instead the module-requiring tests below are guarded
# individually with a skipif on this flag.
try:
    import legal_jurisdictions as lj  # noqa: F401

    _HAS_LJ = True
except ImportError:
    _HAS_LJ = False

requires_lj = pytest.mark.skipif(not _HAS_LJ, reason="legal_jurisdictions not installed")


def _module_installed(code: str) -> bool:
    return juris.module_available(code)


@requires_lj
def test_maine_module_supplies_citations_when_installed():
    if not _module_installed("US-ME"):
        pytest.skip("jurisdiction-maine not installed")
    recs = juris.resolve_authority_records("US-ME")
    assert recs and all("citation" in r for r in recs)
    # Every module citation carries a resolving source_url + verified_on (the contract).
    assert all(r.get("source_url", "").startswith("http") for r in recs)


@requires_lj
def test_federal_module_supplies_profile_when_installed():
    if not _module_installed("US"):
        pytest.skip("jurisdiction-federal not installed")
    prof = juris.jurisdiction_profile("US")
    assert prof is not None
    assert prof["code"] == "US"
    assert prof["count_model"] == "count_every_day"


@requires_lj
def test_maine_profile_diverges_from_federal_when_installed():
    if not (_module_installed("US-ME") and _module_installed("US")):
        pytest.skip("both modules required")
    me = juris.jurisdiction_profile("US-ME")
    us = juris.jurisdiction_profile("US")
    assert me["count_model"] == "exclude_intermediate"
    assert us["count_model"] == "count_every_day"
    assert me["count_model"] != us["count_model"]


@requires_lj
def test_unpopulated_module_refused_strict_but_falls_back_lenient():
    """An installed-but-unpopulated (NH-shaped) module refuses in strict mode."""
    from legal_jurisdictions.contract import (
        AuthorityTable,
        CountingProfile,
        CourtCalendar,
        FormRegistry,
        JurisdictionModule,
        RulePack,
        ServiceRules,
    )

    stub = {"_status": "TO_RESEARCH"}
    scaffold = JurisdictionModule(
        code="US-NH",
        name="New Hampshire (scaffold)",
        kind="state",
        verified_as_of=None,
        citations=AuthorityTable.from_records([dict(stub)]),
        deadlines=RulePack({"_status": "TO_RESEARCH", "rules": []}),
        holidays=CourtCalendar(dict(stub)),
        forms=FormRegistry.from_records([dict(stub)]),
        service_rules=ServiceRules(dict(stub)),
        profile=CountingProfile(dict(stub)),
    )
    assert scaffold.is_populated is False
    # Passed as a module object directly: strict refuses, lenient falls back.
    with pytest.raises(juris.NotPopulatedJurisdictionError):
        juris.resolve_authority_records(scaffold, strict=True)
    assert juris.resolve_authority_records(scaffold, strict=False) == juris.load_bundled_authority()
    assert juris.jurisdiction_profile(scaffold) is None
