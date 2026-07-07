"""Data-contract round-trip tests."""
from template_forge.contracts import (
    BlockDef,
    ManifestDef,
    PIIClass,
    SlotDef,
    SlotType,
    VariantDef,
)


def test_slot_roundtrip():
    s = SlotDef(name="party_a_name", slot_type=SlotType.NAME, required=True,
                pii_class=PIIClass.PII_PARAMETRIZED)
    d = s.to_dict()
    assert d["slot_type"] == "name"
    assert d["pii_class"] == "pii_parametrized"
    assert SlotDef.from_dict(d) == s


def test_variant_has_no_required_text():
    # public schema: variants carry structure, text is optional/absent
    v = VariantDef(variant_id="b1_v1", signal_category="jurisdiction",
                   trigger="NH governing law")
    d = v.to_dict()
    assert "text" not in d
    assert VariantDef.from_dict(d).variant_id == "b1_v1"


def test_block_roundtrip():
    b = BlockDef(block_id="b1", function="choice_of_law",
                 slots=[SlotDef(name="governing_state")],
                 variants=[VariantDef(variant_id="b1_v1")])
    d = b.to_dict()
    b2 = BlockDef.from_dict(d)
    assert b2.block_id == "b1"
    assert b2.slots[0].name == "governing_state"
    assert b2.variants[0].variant_id == "b1_v1"


def test_manifest_roundtrip():
    m = ManifestDef(template_id="t1", practice_area="example",
                    blocks=[])
    d = m.to_dict()
    assert ManifestDef.from_dict(d).template_id == "t1"
