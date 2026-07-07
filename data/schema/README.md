# Template-pack schemas

These JSON Schema files (draft 2020-12) describe the **structure** of a template pack —
the shape of a block, a slot, and a manifest. They describe SHAPE, not content: no clause
text, no variant bodies, no firm-specific manifests. They mirror the dataclasses in
`src/template_forge/contracts/template.py` (`SlotDef`, `VariantDef`, `BlockDef`,
`BlockRef`, `ManifestDef`) and are the machine-checkable form of that contract.

A pack's *facts* (matter / parties / party) are validated separately by the published
`legal-facts-schema`; these schemas cover a pack's *blocks and manifests*.

## Files

### `slot.schema.json` — a fill-in slot
A slot names a value the engine fills at render time. It declares:
- `name` — the snake_case token as it appears in the body (e.g. between `{{ }}`).
- `slot_type` — one of `text, name, date, amount, address, email, phone, case_number,
  registry_ref, enum, flag`. Drives structured quasi-identifier detection and validation.
- `required` — whether a value is mandatory for completeness.
- `pii_class` — `none | pii_parametrized | pii_residual` (see below).
- `enum` — allowed values, when `slot_type == "enum"`.
- `description` — a human hint that MUST NOT contain example values or real content.

### `block.schema.json` — a reusable clause block
Metadata for one block. **Structure only**: the clause body is never inline — it lives in
a separate partial file referenced by `partial`. Fields: `block_id`, `function` (a label
from `clause_functions.json`; enumeration is optional so a pack may add functions),
`subtype`, `scope`, `jurisdiction`, `statutory_citation`, `triggers`, `confidence`,
`slots` (array of slots), `partial` (path), and `variants`.

Each `variant` ships `variant_id`, `signal_category` (one of `fact_pattern, party_role,
jurisdiction, citation_update, scope_expansion, scope_restriction`), `trigger`,
`confidence`, and `count`. **The variant body (`text`) is intentionally absent from this
public schema.** A published pack ships variant *structure*; the drafted variant *text* is
supplied only by a private pack. Do not add a `text` property to a public variant.

### `manifest.schema.json` — a template manifest
Describes the SHAPE of a manifest (not any firm's specific manifest): `template_id`,
`practice_area`, `document_type`, `title`, `description`, `context_defaults`,
`required_slots`, `paired_forms`, `blocks` (ordered `{block_id, mode, when,
variant_select}` assembly wiring), and `discriminators` (named guards that enum-check
routing inputs so a wrong value is rejected rather than producing a false document). The
`blocks` array is assembly *wiring* — block ids and inclusion modes — never clause text.

## PII conventions: `pii_parametrized` vs `pii_residual`

Every slot declares a `pii_class` describing how it relates to PII in the source corpus:

- **`none`** — a structural slot with no PII relationship (e.g. a flag or an enum choice
  that routes assembly).
- **`pii_parametrized`** — the slot *replaced* a quasi-identifier that was blanked out of
  the block body during de-identification. The body is safe to publish; the identifying
  value is filled back in at render time through this slot. This is the desired end state:
  the PII lives only in the fill value, never in the shipped body.
- **`pii_residual`** — a slot on a block whose body *may still* carry residual identifying
  phrasing that a re-audit must clear before the body is published. It flags "not yet
  proven clean" — a block with any `pii_residual` slot is not publish-ready until re-audited
  and its residual phrasing parametrized away or confirmed generic.

In short: `pii_parametrized` = PII was pulled out into the slot and the body is clean;
`pii_residual` = the body might still leak and needs another pass. Only `none` and
`pii_parametrized` slots belong to a body that is cleared for publication.
