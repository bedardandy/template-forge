"""Clause-mining pipeline tests (clean-room, no external services)."""
from template_forge.mining import pipeline


def test_segment_clauses_drops_short():
    text = "Short.\n\nThis instrument shall be governed by the laws of the State of Maine."
    segs = pipeline.segment_clauses(text)
    assert any("governed" in s for s in segs)
    assert all(len(s.split()) >= 6 for s in segs)


def test_dedup_collapses_near_duplicates():
    segments = [
        "This instrument shall be governed by the laws of the State of Maine.",
        "This instrument shall be governed by the laws of the State of Maine.",
        "This instrument shall be governed by the laws of the State of New Hampshire.",
        "The parties agree to indemnify and hold each other harmless from all claims.",
    ]
    groups = pipeline.dedup_clauses(segments, threshold=0.6)
    # the three governing-law lines collapse; indemnity stands alone
    sizes = sorted(len(g.members) for g in groups)
    assert sizes[-1] >= 2
    assert len(groups) <= 3


def test_label_and_variant_and_cleanup():
    segments = [
        "This instrument shall be governed by the laws of the State of Maine.",
        "This instrument shall be governed by the laws of the State of New Hampshire.",
    ]
    groups = pipeline.dedup_clauses(segments, threshold=0.5)
    groups = pipeline.label_groups(groups)
    groups = pipeline.mine_variants(groups)
    groups = pipeline.typo_clean(groups)
    g = max(groups, key=lambda x: len(x.members))
    assert g.function == "choice_of_law"
    # the NH form is an actionable jurisdiction variant, kept after cleanup
    assert any(v["signal_category"] in pipeline._ACTIONABLE for v in g.variants)


def test_classify_deviation_both_citations_not_citation_update():
    # Both sides carry a citation-shaped token ("5 A") but differ elsewhere:
    # this is NOT a citation change and must fall through to scope/fact/stylistic.
    dominant = "Damages are capped at 5 A under this agreement for the year."
    variant = "Damages are capped at 5 A under this contract for the period."
    cat = pipeline._classify_deviation(dominant, variant)
    assert cat != "citation_update"
    assert cat in {"scope_expansion", "scope_restriction", "fact_pattern",
                   "stylistic"}


def test_classify_deviation_one_sided_citation_is_citation_update():
    # A citation token present on exactly one side IS a citation change.
    dominant = "The fee is set out in the schedule attached to this agreement."
    variant = "The fee is set out in 5 A of the schedule attached to this deal."
    assert pipeline._classify_deviation(dominant, variant) == "citation_update"


def test_write_pack_structure_only(tmp_path):
    segments = ["The parties agree to indemnify and hold harmless from all claims and losses."]
    groups = pipeline.dedup_clauses(segments)
    groups = pipeline.label_groups(groups)
    groups = pipeline.mine_variants(groups)
    pipeline.write_pack(groups, tmp_path, include_bodies=False)
    import json
    line = (tmp_path / "blocks" / "blocks.jsonl").read_text().splitlines()[0]
    rec = json.loads(line)
    assert "block_id" in rec
    for v in rec["variants"]:
        assert "text" not in v  # structure-only: no bodies
