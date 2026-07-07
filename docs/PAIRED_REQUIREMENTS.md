# `paired_requirements` — structured companion / checklist metadata

Some documents don't stand alone: they require companion official forms, evidence
artifacts, or external records **alongside** the instrument. A deed may need a
transfer-tax declaration filed with it; a divorce complaint may need a companion
affidavit when a given fact is in issue; a recordable may carry a statutory
deadline that the drafter must be warned about. A manifest captures all of this as
an optional `paired_requirements` array — the structured successor to a
string-only `paired_forms` field.

**Backward compatible.** `paired_forms` is unchanged and still rendered in the
provenance trail. `paired_requirements` is *additive metadata for companions,
never for the instrument*: the assemble engine does **not** render it into the
document body, so adding it never changes the rendered instrument or its golden
(it does add the paired-requirements section to the audit companion). Consumers:

- `assemble_record()` evaluates each entry's `applies_when` guard against the
  matter facts and carries the entries — each with an `applicable` boolean — on
  the decision record. An unevaluable guard **fails safe to applicable**: better a
  spurious checklist line than a silently missing mandatory attachment.
- the **attorney audit companion** renders the applicable entries as a per-matter
  checklist with severity, timing, and citations. An entry whose guard evaluated
  **falsy** is omitted (e.g. a child-support affidavit required *with* a divorce
  complaint only when child support is at issue). This is also how
  deadline-bearing recordables warn the drafter without putting advice language
  into the recorded instrument.
- a downstream closing / checklist tool can evaluate the same metadata.

Companion documents are always assembled from their **own** manifests; companion
text is never inlined into this document.

## Entry schema

| field | req | notes |
|---|---|---|
| `requirement_id` | ✓ | stable machine key |
| `label` | ✓ | human-readable checklist label |
| `kind` | ✓ | `companion_template` \| `official_form` \| `evidence` \| `external_record` \| `checklist_only` |
| `applies_when` | | restricted boolean expression in the same style as block `when` guards; omitted = always applies. Evaluated into an `applicable` flag on the decision record (an unevaluable guard fails safe to applicable = kept visible); the audit checklist omits inapplicable entries. |
| `companion_template_id` | | template id to assemble when `kind == companion_template` |
| `official_form_id` | | form code when `kind == official_form` |
| `artifact_type` | | e.g. `certificate_good_standing` \| `registry_copy` \| `title_commitment_item` \| `other` |
| `disposition` | ✓ | `record` \| `deliver` \| `retain` \| `file` \| `review` |
| `timing` | ✓ | `before_drafting` \| `before_execution` \| `at_execution` \| `before_recording` \| `with_recording` \| `after_recording` \| `closing_file` \| `with_filing` \| `with_service` |
| `severity` | ✓ | `info` \| `recommended` \| `required` \| `blocking` |
| `required_slots` | | extra slots the companion/evidence workflow needs |
| `notes` | | privacy / registry-format / authority guidance |
| `citations` | | supporting statutory/rule citations |

## Jurisdiction profiles and official forms

The `official_form_id` and `citations` fields are inherently jurisdiction-specific
— an official form and its filing rule belong to one jurisdiction. Model these as
part of a **jurisdiction profile** in your pack, not as a global assumption:
gate them with `applies_when` on the matter's jurisdiction so a manifest can serve
more than one profile. **Maine is one example jurisdiction profile** (e.g. a
transfer-tax declaration paired with a deed, or a companion family-division
affidavit paired with a complaint); public statutory / court-rule citations for
any jurisdiction are public law and fine to reference. The engine never invents a
citation or a form pairing — an attorney supplies and reviews them.

> AI/LLM-assisted metadata; the legal dispositions and citations require attorney
> review. This is drafting scaffolding, not legal advice.
