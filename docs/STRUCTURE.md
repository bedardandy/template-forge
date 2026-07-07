# Block / manifest / pack structure

This is the structure reference for a **template pack** and for the typed
**segment layer** the engine produces. For how a pack is created and operated,
see [OPERATING.md](OPERATING.md).

## Pack shape

```
<pack>/
  manifests/<template_id>.json      one manifest per document type
  blocks/blocks.jsonl               block metadata + variant structure (one JSON object per line)
  blocks/partials/<block_id>.md.j2  each block body as a Jinja2 partial
  facts/fact_keys.json              the typed fact/slot registry
```

### Manifests

A manifest is a declarative description of one document type, named
`<practice_area>__<document_type>.json`. It carries:

- `template_id`, `practice_area`, `document_type`, `title`, `description`;
- `context_defaults` — default context values (e.g. `governing_state`) merged
  under the supplied slots;
- `required_slots` — slots the matter must supply (a bare name is always
  required; an object `{"slot": "...", "when": "<guard>"}` is required only when
  the guard is truthy);
- `paired_forms` / `paired_requirements` — companion-form metadata (see
  [PAIRED_REQUIREMENTS.md](PAIRED_REQUIREMENTS.md));
- `discriminators` — the family selectors (enums) that route which manifest / block set applies;
- `blocks` — the ordered list of block refs.

Each block ref has a `mode`:

| mode | behavior |
|------|----------|
| `required`   | always rendered |
| `add`        | rendered only if `when` (a boolean over the fact context) is true |
| `remove`     | rendered by default, dropped if `when` is true |
| `substitute` | rendered; if a `variant_select` rule matches, swap the dominant text for a **variant** |

`when` expressions are short booleans over context keys (e.g. `post_2019`,
`governing_state == 'Maine'`, `has_minor_children and not agreed`), evaluated by a
restricted AST walker — **no code execution**.

Repo-authored structural scaffolding (titles, signature lines) uses `literal_md`
refs and is kept strictly separate from your authored clause language.

### Blocks

`blocks.jsonl` holds one JSON object per block: `block_id`, `function`,
`subtype`, `scope`, `jurisdiction`, `statutory_citation`, `triggers`,
`confidence`, `slots`, the `partial` path, and a `variants` list (each variant
carries a `variant_id`, `signal_category`, `trigger`, and count). The block
**body** lives only in its partial, so `blocks.jsonl` stays a metadata index.

---

# Document structure / segment layer

The assembled body is flat Markdown (paragraphs joined by blank lines, one `#`
title). That is fine as delivery text but loses the *structure* a rich-text or
hierarchical consumer needs — alignment, indentation, list nesting, where a
signature block begins. The **segment layer** (`engine/segments.py`) adds that
structure back as **declared data**, so consumers stop guessing it from
punctuation.

## What a segment is

`assemble_record()` returns `record["segments"]`: an ordered list of typed
segments derived from the decision record.

```json
{ "role": "numbered_item", "number": 4, "list_style": "decimal",
  "block_id": "trust_certification",
  "text": "4. The Trust is revocable by the Settlor, who holds the power to revoke it." }
```

Get it from the CLI:

```bash
python3 -m template_forge.engine.assemble \
    --manifest manifests/example__services_agreement.json \
    --slots slots.json --segments /tmp/segs.json
```

### Roles

| role | meaning |
|---|---|
| `title` | the manifest title (one, level 1) |
| `heading` | a section heading (level N) |
| `body` | a normal body paragraph |
| `recital` | an indented recital paragraph |
| `numbered_item` | one item of a numbered list (carries `number`, `list_style`) |
| `signature_block` | execution / signature geometry |
| `notary_block` | acknowledgment / jurat geometry |
| `caption` | a 2-column court caption (rows/cells) |
| `table` | a general grid (schedules, fee tables) |
| `divider` | a centered separator |
| `chrome` | repo-authored structural scaffolding (record-and-return, etc.) |

## How structure is declared

Each block declares its role in `blocks.jsonl`:

```json
"structure": { "role": "numbered_list", "list": {"style": "decimal"} }
```

Optional keys: `align` (`left|center|right|justify`) and `level` (for headings).
The descriptor is **per block** and flows onto each decision and into the segment
list. A block with no descriptor defaults to `body` (or `chrome` for `literal_md`
refs) — so annotation is incremental, not all-or-nothing.

Segmentation is **declarative on purpose**: the only pattern-based refinement is
numbered-item detection, and it fires *only inside a block that declared*
`role: "numbered_list"`. A `body` block is never reinterpreted by accident, and
nothing is inferred from undeclared blocks.

## Separation of concerns

Blocks declare **semantics** (the role). Presentation — alignment, indent, list
geometry, font — is a **consumer** concern:

- `engine/export_docx.py` owns `ROLE_PRESENTATION` (role → Word paragraph style,
  alignment, indent). It renders from the declared segments, not from `#`/`-`/`>`.
- An HTML or JSON-document exporter would map the same roles to `<h1>` / `<p>` /
  `<ol><li>` / `<section>` with no change to the blocks.

So the same structured instrument can be delivered as a styled `.docx`, as
sectioned HTML, or as a document-model JSON, all from one declared layer.

## Formatting & spacing (DOCX)

`export_docx.py` targets a conventional legal house style, which you can tune to
match the documents your pack was mined from (measure your own corpus rather than
assuming): a serif body face, justified body, centered titles/captions,
`space_after = 0`, line spacing **double for court filings / single otherwise**,
and tabs for column alignment. The exporter follows that and fixes three
legibility problems:

- **No excessive spacing.** Separation between paragraphs is carried by paragraph
  spacing and (for court filings) line spacing — never by stacked empty
  paragraphs. The `Normal` style sets `space_after = 0`; single-spaced instruments
  get a small `space_after` between body paragraphs; court filings (pleading /
  motion / divorce-complaint, and litigation/criminal/family affidavits — see
  `is_double_spaced()`) are double-spaced with no space-after.
- **Keep-with-next.** `title`/`heading` roles carry keep-with-next, so a heading
  is never stranded at the foot of a page. Signature/notary/caption blocks carry
  keep-together (and caption table rows are marked `cantSplit`) so an execution
  block never breaks across a page.
- **Tabs, not spaces.** Any run of 3+ spaces in authored text (column padding) is
  converted to a real tab against declared tab stops — a 2-space run (sentence
  spacing) is left alone. Captions render as borderless 2-column tables, so a
  witness line `____  Address: ____` aligns on a tab stop instead of collapsing
  in a proportional font.

The role → presentation map (`ROLE_PRESENTATION`) and `is_double_spaced()` are
the two knobs; line-spacing policy is one function if you want, say, all
single-spaced filings. The DOCX smoke test asserts each guarantee.

## Captions and tables (grid blocks)

A block with `structure.role` of `caption` or `table` is authored as a grid: rows
split on `@@ROW@@`, cells on `@@COL@@` (cells may be multi-line). The engine
parses it into `rows` carried on the segment, renders a clean aligned-column view
into the flat Markdown body, and the DOCX exporter emits a real Word table —
borderless for `caption`, `Table Grid` for `table`. So a court caption is a true
2-column table, not space-padded text that collapses in a proportional font. The
example pack's caption partial is the worked example.

## Auto-numbering

A block can declare `structure.number = {list, level, style, compound?, prefix?,
suffix?}`. The engine keeps a counter per `(list, level)` across the whole
document, increments it as each such block renders, resets deeper levels when a
shallower one advances, and prepends the computed ordinal to the block's first
paragraph. Styles: `upper_word` (FIRST, SECOND…), `decimal`, `upper_roman` /
`lower_roman`, `upper_alpha` / `lower_alpha`; `compound` joins the level chain for
decimal sections (`1.1`, `1.2`). Because the number is computed, adding or
removing an article **renumbers** the rest — there are no hand-fed clause-number
slots. A will family authored this way (each article an auto-numbered `upper_word`
block on one list) renumbers automatically when you add a specific-bequests
article. The same primitive nests for contracts (`Article I → Section 1.1 → (a)`).

## Manifest-driven numbering (contract families)

Numbering is a **document** property, not a **clause** property. Shared
general-provisions clauses (governing law, severability, counterparts, …) appear
both in flat agreements ("12. Severability") and in nested ones ("9.4"), so a
fixed ordinal can't be baked into the reusable block. A manifest block-ref may
therefore carry `"number"` — an inline number spec, or a name into a
manifest-level `"numbering": {…}` table — which the engine **merges over** the
block's own `structure.number`: the block keeps its `scope` (items vs block) and
the manifest supplies the document scheme (`list` / `level` / `style` /
`compound` / `suffix`). The same block is "12." in a flat contract and "9.4" in a
nested one, with no change to the block. Article headings, being a document
structural decision, are emitted as **numbered `literal_md` chrome** drawn from
the same doc-level counter.

Two shapes cover the agreement families:

- **Flat** — one continuous counter across operative paragraphs and
  general-provisions clauses (1..N); dropping an optional section renumbers the
  rest.
- **Nested** — `ARTICLE I —` chrome headings interleaved with content blocks
  numbered `1.1`, `1.2`, … reset to `2.1` on the next article. A general-provisions
  article reuses the **same** shared blocks that are flat-numbered elsewhere,
  overridden to the nested ordinals.

Conditional articles (e.g. child-support articles gated on `has_children`) use
`mode: add` on the numbered `literal_md` heading, so a `when` drops the *whole*
numbered unit and the remaining articles renumber (an excluded block never
reaches the counter). A `required` block always renders (its `when` is recorded
but not gated), so any heading or section that should drop-and-renumber must be
`mode: add`.

## Differently-shaped families (affidavit / notice / letter / certificate)

Not every document is an article/section instrument. Sworn statements, short
notices, correspondence, and recordable certificates reuse the shared blocks
(caption, jurat, acknowledgment) and self-number their statements locally (a loop
or explicit ordinals inside one block), so they need no document-level numbering
scheme:

- **Affidavits** — venue/court caption → affiant intro ("NOW COMES … duly
  sworn") → one statements block → signature → jurat (the affiant-aware
  "subscribed and sworn", distinct from the free-act-and-deed acknowledgment).
  Statements blocks may render an attorney-supplied `statements` **list** as
  numbered sworn paragraphs.
- **Notices** — short structural notices. A notice type may carry an **enforced
  discriminator** (e.g. a `quit_ground` value routing to the right notice period
  and statutory authority); a wrong value hard-fails rather than reciting the
  wrong law.
- **Letters** — correspondence-shaped on a shared letterhead / closing chrome,
  with one body block each. A letter may carry an **enforced discriminator** (e.g.
  a `demand_kind` selecting which statutory overlay applies).
- **Certificates** — reuse the recordable acknowledgment, with an **enforced
  discriminator** (e.g. an `entity_kind` branching the capacity and statutory
  basis for corporation / LLC / trust).

Enforced discriminators (values that *fail* rather than defaulting to a wrong
result) should carry negative tests.

## Coverage note

The role vocabulary covers the common instrument families (recordable + estate +
litigation + family + contract + correspondence). Each block defaults to `body`,
so coverage scales by annotating the small shared set of non-body blocks
(signature / notary / caption / numbered) rather than every manifest.

> Any statutory citations above (Maine or otherwise) are illustrative of **one
> example jurisdiction profile**; public statutory citations are public law. The
> engine never invents a citation — an attorney supplies and reviews them.
