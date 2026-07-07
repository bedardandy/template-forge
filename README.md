# template-forge

**Build and operate legal-document templates from _your own_ documents.**

template-forge is a firm-agnostic framework for turning a corpus of executed
legal documents into a reusable, auditable template library — and then assembling
new documents from it, fact-by-fact, with a full provenance trail.

> [!WARNING]
> **Experimental. Not legal advice.** This is alpha-stage software for document
> automation. It does not practice law, does not validate that any assembled
> document is legally correct or complete, and must not be used without review by
> a qualified attorney licensed in the relevant jurisdiction. Outputs are drafts.

## What it does

1. **Mine** — turn your `.docx`/`.pdf`/`.txt` documents into candidate reusable
   *blocks*: segment → MinHash/LSH near-duplicate dedup → functional labeling →
   variant mining → typo/stylistic cleanup. (`template_forge.mining`)
2. **De-identify** — strip client PII from executed documents by replacing named
   entities and quasi-identifiers with Word Content Controls, emitting a PII
   audit. This is what makes it safe to build templates from real documents.
   (`template_forge.generator`)
3. **Assemble** — compose a document from a *manifest* (which blocks, in what
   order, under what conditions) plus a *fact pattern*, selecting the right
   variant per fact and printing a decision trail. (`template_forge.engine`)
4. **Export** — render to Markdown, DOCX, or a tracked-changes redline against a
   base draft.

Nothing about a specific firm is baked in. The framework ships a fully
**synthetic** demo pack and runs end-to-end on synthetic data with zero
proprietary bytes.

## Template packs and the multi-tenant seam

The engine operates over a *template pack*:

```
<pack>/
  manifests/<template_id>.json      which blocks compose each document
  blocks/blocks.jsonl               block metadata + variant structure
  blocks/partials/<block_id>.md.j2  each block body (Jinja2)
  facts/fact_keys.json              the fact/slot registry
```

The public framework ships **only** the synthetic
[`example_pack/`](src/template_forge/example_pack/). To operate over your own
material, build a **private pack** with the mining + de-identification pipeline
and point the engine at it:

```bash
export TEMPLATE_FORGE_PACK=/path/to/your/private/pack
# or per-invocation:  template-forge-assemble --pack /path/to/pack ...
```

This is the multi-tenant seam: **the framework is firm-agnostic; a firm's
drafting judgment lives entirely in its private pack and never enters this
project.** A firm augments the public framework privately by keeping its
manifests, block bodies, and variant assembly-rules in its own pack — the
public code is consumed as a pinned dependency, never forked. (The manifests,
variant assembly-rules, and citation→block linkage that encode a firm's drafting
judgment are, by design, *not* part of this repository — only the empty/synthetic
schema and an example pack are.)

## Quickstart (synthetic, end-to-end)

```bash
pip install -e ".[dev]"

# Assemble a synthetic document from the bundled example pack:
python -m template_forge.engine.assemble \
    --manifest src/template_forge/example_pack/manifests/example__services_agreement.json \
    --slots examples/services_agreement.json

# Mine a candidate pack from your own documents:
python -m template_forge.mining.pipeline --input-dir ./my_docs --pack-dir ./my_pack

# Generate a synthetic matter to test with:
python -m template_forge.generator.synthetic --seed 1
```

## Layout

```
src/template_forge/
  engine/        assembly engine (assemble, choices, render, segments,
                 facts, audit, export_docx, export_redline, paths)
  contracts/     data contracts (document, form_field, classification, template)
  mining/        clause-mining pipeline + docx TF-IDF template extractor
  generator/     de-identification engine + synthetic matter generator
  example_pack/  the bundled SYNTHETIC demo pack (no firm content)
data/
  citations/     public Maine statutory-citation reference table (attributed)
  schema/        block / manifest / slot JSON Schemas + clause-function taxonomy
docs/            OPERATING guide, STRUCTURE, FACTS, AUDIT, PAIRED_REQUIREMENTS,
                 RELATION (relationship to legal-facts-schema)
fixtures/        152 synthetic demo fixtures (fictional data only)
```

## Relationship to `legal-facts-schema`

template-forge does **not** define its own fact object. The shared
matter/parties/party/facts model is owned by the published
[`legal-facts-schema`](https://github.com/bedardandy/legal-facts-schema); this
project complements it. See [docs/RELATION.md](docs/RELATION.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
