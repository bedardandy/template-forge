# tools — pack-building utilities

These operate on **your own** clause library / template pack; they ship no firm
content and read no data baked into this repository.

- `derive_blocks.py` — build a pack's `blocks/blocks.jsonl` + partials from a
  clause library (set `TEMPLATE_FORGE_CLAUSE_DIR`).
- `citation_lint.py` — scan a pack's block metadata + bodies for legal citations
  and flag stale ones.
- `citation_verify.py` — merge live-verification findings into a pack-local
  citation authority table and classify mechanical vs. review-only fixes. Per-pack
  citation adjudications load from `$TEMPLATE_FORGE_CITATION_FIXES` (or
  `<pack>/citation_fixes_config.json`); nothing is baked in.
- `gap_report.py` — coverage/freshness backlog for a pack.

All inputs and outputs are resolved relative to the active pack
(`TEMPLATE_FORGE_PACK`) or explicit environment variables. The bundled
`data/citations/authority.jsonl` (public Maine law) is a *published dataset*, not
an output of these tools — running them never modifies it.
