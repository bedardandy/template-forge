"""End-to-end engine tests over the bundled synthetic example_pack."""
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "template_forge"
sys.path.insert(0, str(PKG / "engine"))

import assemble  # noqa: E402
import paths  # noqa: E402
import render  # noqa: E402

PACK = PKG / "example_pack"
MANIFESTS = PACK / "manifests"


def test_default_pack_is_example_pack():
    assert paths.pack_root() == PACK
    assert paths.blocks_jsonl().exists()
    assert paths.manifests_dir().exists()


def test_load_blocks_no_duplicates():
    blocks = render.load_blocks()
    assert len(blocks) >= 5
    assert "signature_block" in blocks


def test_assemble_services_agreement():
    slots = {
        "party_a_name": "Jamie Q. Doe",
        "party_b_name": "Robin R. Roe",
        "execution_date": "June 16, 2026",
        "governing_state": "Maine",
    }
    doc, record = assemble.assemble_record(
        MANIFESTS / "example__services_agreement.json", slots, trail=False)
    assert "Jamie Q. Doe" in doc
    assert "Robin R. Roe" in doc
    assert "State of Maine" in doc
    assert record["template_id"] == "example__services_agreement"


def test_variant_selection_switches_jurisdiction():
    base = {
        "party_a_name": "Jamie Q. Doe",
        "party_b_name": "Robin R. Roe",
        "execution_date": "June 16, 2026",
    }
    me_doc, _ = assemble.assemble_record(
        MANIFESTS / "example__services_agreement.json",
        {**base, "governing_state": "Maine"}, trail=False)
    nh_doc, nh_record = assemble.assemble_record(
        MANIFESTS / "example__services_agreement.json",
        {**base, "governing_state": "New Hampshire"}, trail=False)
    # the jurisdiction variant fires: NH body differs from the Maine default
    assert "conflict-of-laws" in nh_doc
    assert "conflict-of-laws" not in me_doc
    # and the decision record shows a non-dominant text source for that block
    gl = [d for d in nh_record["decisions"] if d.get("block_id") == "governing_law"]
    assert gl and gl[0].get("text_source") not in (None, "dominant")


def test_discriminator_enum_rejects_bad_value():
    slots = {
        "party_a_name": "Jamie Q. Doe",
        "party_b_name": "Robin R. Roe",
        "county": "Cumberland",
        "execution_date": "June 16, 2026",
        "officer_kind": "wizard",  # not in enum
    }
    try:
        assemble.assemble_record(
            MANIFESTS / "example__acknowledged_instrument.json", slots, trail=False)
    except SystemExit as e:
        assert "officer_kind" in str(e)
    else:
        raise AssertionError("expected SystemExit on invalid discriminator value")


def test_unfilled_slot_renders_visible_marker():
    doc, _ = assemble.assemble_record(
        MANIFESTS / "example__services_agreement.json", {}, trail=False)
    assert "[[ party_a_name ]]" in doc
