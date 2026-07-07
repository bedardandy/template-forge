# example_pack — the bundled synthetic template pack

This directory is a **fully synthetic** template pack used by the tests and the
end-to-end demo. It contains **no firm content**: every block body is generic,
author-written scaffolding text, every fixture uses fictional names, and the
manifests are illustrative examples — not any firm's drafting judgment.

A *template pack* has this shape:

```
manifests/<template_id>.json      one manifest per document type
blocks/blocks.jsonl               block metadata + variant STRUCTURE (one JSON/line)
blocks/partials/<block_id>.md.j2  each block body as a Jinja2 partial
facts/fact_keys.json              the fact/slot registry (schema)
```

The public framework ships **only** this synthetic pack. To operate the engine
over your own documents, build a private pack with the mining/deid pipeline (see
`docs/OPERATING.md`) and point the engine at it via `TEMPLATE_FORGE_PACK` or the
`--pack` flag. Your pack never enters this repository — that is the multi-tenant
seam described in the README.

**Jurisdiction:** the manifests here use Maine (`ME`) as one *jurisdiction
profile*. Maine is a parameter, not a hardcoded assumption — a pack may target
any jurisdiction by setting the manifest `context_defaults` and block `when`
conditions accordingly.
