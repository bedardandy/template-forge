# template-forge — operating guide

**template-forge** is a firm-agnostic framework for turning **your own executed
documents** into a reusable, PII-free **template pack**, and then assembling new
drafts from that pack with a full, per-component provenance trail.

The public framework ships **no** real content. It bundles one **synthetic**
`example_pack` (fictional names, generic scaffolding text) for tests and the
end-to-end demo. Everything firm-specific lives in a **private pack** you build
and keep outside this repository.

> **Experimental drafting scaffolding — not legal advice.** template-forge
> composes text you supplied and records why. An attorney must review every
> assembled document. No clause language is invented: a missing clause is left
> as a visible `[GAP:]` marker, never fabricated.

---

## 1. The template-pack concept

The engine operates over a **template pack** — a self-contained directory tree:

```
<pack>/
  manifests/<template_id>.json      one manifest per document type
  blocks/blocks.jsonl               block metadata + variant structure (one JSON object per line)
  blocks/partials/<block_id>.md.j2  each block body as a Jinja2 partial
  facts/fact_keys.json              the fact/slot registry (typed keys → slots)
```

- **manifests/** — one manifest per document type (`<practice_area>__<document_type>.json`).
  A manifest is an *ordered list of block refs*, each with a `mode`
  (`required` / `add` / `remove` / `substitute`) and optional `when` guards and
  `variant_select` rules. See [STRUCTURE.md](STRUCTURE.md).
- **blocks/blocks.jsonl** — one line per block: its `function`, `jurisdiction`,
  `slots`, `statutory_citation`, structural role, and the *structure* of any
  variants. The block **body** is not here — it is a partial.
- **blocks/partials/** — the actual clause text, one Jinja2 template per block.
  This is where your language lives.
- **facts/fact_keys.json** — the typed fact registry: role- and time-qualified
  keys, each declaring its type and the engine slot(s) it feeds. See
  [FACTS.md](FACTS.md).

### The multi-tenant seam

The public framework ships only the synthetic `example_pack`. To operate over
your own documents, you build a **private pack** and point the engine at it —
the pack never enters this repository. Two equivalent selectors:

```bash
# environment variable (persists for the session)
export TEMPLATE_FORGE_PACK=/path/to/your/private_pack
python3 -m template_forge.engine.assemble --manifest ...

# or per-invocation flag (wins for that call)
python3 -m template_forge.engine.assemble --pack /path/to/your/private_pack --manifest ...
```

Precedence is `--pack` > `TEMPLATE_FORGE_PACK` > the bundled `example_pack`.
Nothing about your pack is baked into the framework; the engine is firm-agnostic
and your drafting judgment stays in your pack. This is the seam that keeps the
public code clean and your private library private.

### Jurisdiction is a parameter, not an assumption

A pack targets whatever jurisdiction its manifests and blocks declare, via
manifest `context_defaults` (e.g. `governing_state`) and block `when`
conditions. The bundled example uses **Maine (`ME`) as one example jurisdiction
profile** — it is a parameter, not a hardcoded assumption. Public statutory
citations for any jurisdiction are fine to reference as public law; the engine
never invents a citation.

---

## 2. Creating templates from your own documents

You turn executed documents into a private pack with two offline pipelines. The
modules live under `src/template_forge/mining/` and
`src/template_forge/generator/` (some stages are still under construction —
the *shape* below is stable). **Run both locally; nothing leaves your machine.**

### 2a. The mining pipeline — corpus → candidate clauses → labeled blocks

Point the miner at a directory of your executed `.docx` / `.pdf` documents. It
distills the recurring boilerplate into candidate clause blocks:

1. **Ingest & segment.** Parse each document into paragraphs / clause-sized
   spans. (`generator/normalize.py` also handles the single-document normalize
   case: strip metadata, accept tracked changes, parametrize dates/years.)
2. **Candidate extraction.** Collect clause-sized text spans as candidates.
3. **Near-duplicate dedup (MinHash / LSH).** Cluster candidates that are the
   "same clause" up to minor edits, so one canonical block represents each
   recurring clause instead of thousands of near-copies. The most frequent
   member of a cluster becomes the **dominant** text.
4. **Functional labeling (LLM).** Label each cluster with a `function`
   (`granting_clause`, `governing_law`, `signature_block`, `notice`, …) and
   `jurisdiction`, so blocks are retrievable by role rather than by document.
5. **Variant mining.** Within each cluster, analyze how members deviate from the
   dominant text and classify each deviation by `signal_category`
   (`fact_pattern`, `party_role`, `jurisdiction`, `citation_update`,
   `scope_expansion`, `scope_restriction`). Each actionable deviation gets an
   `assembly_rule` (a natural-language condition) and a `confidence`. Purely
   `stylistic` and `typo` deviations are filtered out.
6. **Typo / noise cleanup.** Remove typo-only variants and normalize whitespace
   so the surviving variants are meaningful choices, not noise.

The output is your `blocks/blocks.jsonl` + `blocks/partials/*.md.j2`. **This
text is yours** — mining distills your language, it never fabricates clauses.
Frequency thresholds (e.g. keep clauses seen at least *N* times) keep singletons
out of the dominant path; treat any surviving singleton variant as a hypothesis
to verify, not a rule to trust.

### 2b. The deidentify pipeline — strip PII before anything is packed

Every mined block must be PII-free before it enters a pack. The deidentify
pipeline (`generator/deidentify.py`) turns an executed document into a
scrubbed, slot-bearing template:

1. **Entity extraction (LLM).** Extract the named entities in each document —
   parties, addresses, dates, account numbers, file paths — with their exact
   text and location.
2. **Content-Control replacement.** Replace each extracted entity with a named
   fill-in placeholder (a Word Content Control / slot), so the identifier
   becomes a typed slot instead of a literal value.
3. **PII audit (safety-net sweep).** A deterministic regex pass catches PII the
   extractor may have missed (filesystem paths, notary county names, residual
   address fragments, manual-redaction artifacts) and wraps them too, then emits
   a **PII audit** listing what was replaced and any residuals.

Nothing reaches your pack until the PII audit is clean. Fixtures and examples
must use **fictional** parties (Doe / Roe / Public), never a real matter.

### From pipelines to a pack

The mining + deidentify passes produce the four pack components. Assemble them
under one directory, add a `facts/fact_keys.json` that maps your typed facts to
slots, and point the engine at it with `--pack`. The `example_pack` is the shape
to copy; your pack replaces its contents with your own.

---

## 3. Operating: the assemble workflow

Once you have a pack, you assemble a document by selecting the right blocks and
variants for a fact pattern, then rendering. There are two ways to drive it: the
**engine CLI** (manifest-driven, fully audited) and the **manual clause-selection
workflow** (agent picks variants). Both follow the same five steps.

### Step 1 — figure out what clause functions the document needs

Decide which clause `function`s the document requires (e.g. for a power of
attorney: `granting_clause`, `notice`, `signature_block`, `notary_block`, plus
any document-specific bundles). If unsure, list what your pack offers first —
the manifest for the document type already names its ordered blocks, and
`blocks.jsonl` is indexed by `function` and `jurisdiction`.

### Step 2 — pull candidate blocks + variants

For each needed function, pull the candidate block(s) from the pack. Each
candidate carries:

- **dominant text** — the most-frequent clause (the boilerplate default), and
- **variants** — alternative texts, each with an `assembly_rule`,
  `signal_category`, and `confidence`.

The retrieval layer does **not** pick variants for you. It surfaces the choices.

### Step 3 — select variants per fact pattern

For each candidate block:

1. Start with the **dominant** text as the default.
2. For each variant, read its `assembly_rule` / `trigger` and decide whether the
   fact pattern (jurisdiction, execution date, party roles, …) matches.
3. If it matches, swap the dominant for that variant.
4. **Record every swap** — you will surface it in step 5.

Do **not** apply `typo` or `stylistic` variants (the engine filters them). Treat
low-confidence / singleton variants as hypotheses to verify.

Manifest-driven equivalent: the engine evaluates each block ref's `mode` and
`when` guard against the fact context, and applies `variant_select` rules
automatically — so a `substitute` block swaps to its variant when the guard is
true (e.g. a post-2019 recodification citation). Same decision, mechanized and
recorded.

### Step 4 — assemble in order

Concatenate the selected blocks in a sensible instrument order (recital →
granting → operative provisions → boilerplate → signatures → notary /
attestation). Fill slot placeholders with the supplied real values; leave any
unsupplied slot as a visible `[[ slot ]]` marker for the reviewer, and any
missing clause as a `[GAP:]` marker — **never invent language**.

With the engine, this is one call:

```bash
python3 -m template_forge.engine.assemble \
    --pack /path/to/your/private_pack \
    --manifest manifests/example__services_agreement.json \
    --slots slots.json \
    --out draft.md \
    --decision-record draft.decisions.json \
    --audit draft.audit.md
```

The slots file is ONE JSON object that is both the Jinja render context (slot
values like `party_a_name`) and the fact-pattern context for block choices
(flags like `has_successor_agent`). Derived flags (e.g. a `post_2019` computed
from an execution date) are filled in automatically.

### Step 5 — surface what you swapped (the auditability requirement)

**This step is mandatory.** In the final output, state which variants you chose
and why, so the reviewing attorney gets auditable provenance instead of
reverse-engineering the draft. A short trail, for example:

> _Kept the dominant governing-law block (matter is in-state). Swapped the
> notice block to its updated-citation variant because the execution date is
> after the recodification. Added the sole-benefit sentence because the named
> agent is not the principal's spouse._

The engine formalizes this: it emits a **decision record** (machine-readable,
one entry per block ref — *including blocks NOT included*) and an **audit
companion** (human-readable). See [AUDIT.md](AUDIT.md). Every add / remove /
substitute is recorded both ways; an omission is a recorded decision, never a
silent drop.

---

## 4. The fact object

A pack's *facts* are validated by the published **`legal-facts-schema`** package,
which owns the shared fact object — `matter` / `parties` / `party` / `facts`.
template-forge does **not** redefine that object: it *builds on top* of it. The
pack's `facts/fact_keys.json` maps typed, role-qualified fact keys onto engine
**slots**, and manifests consume those slots and the flags/enums derived from
them.

Keep the two layers straight: **legal-facts-schema owns the facts; template-forge
owns the templates, blocks, and slots that consume them.** For how the two relate
in detail, see **[RELATION.md](RELATION.md)**.

---

## 5. Companion forms and deadlines

Some documents require companion official forms, evidence, or external records
alongside the instrument (e.g. a transfer-tax declaration filed with a deed).
template-forge carries this as **structured `paired_requirements` metadata** on a
manifest — it is checklist metadata for the matter, never inlined into the
document body. See [PAIRED_REQUIREMENTS.md](PAIRED_REQUIREMENTS.md).

---

## See also

- [STRUCTURE.md](STRUCTURE.md) — block / manifest / pack structure and the
  segment (typed-structure) layer.
- [FACTS.md](FACTS.md) — the fact/slot model and the shared fact registry.
- [AUDIT.md](AUDIT.md) — decision records and the attorney audit companion.
- [PAIRED_REQUIREMENTS.md](PAIRED_REQUIREMENTS.md) — companion-form / checklist metadata.
- [RELATION.md](RELATION.md) — how template-forge relates to `legal-facts-schema`.
