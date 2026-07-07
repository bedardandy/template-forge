# Decision records & the attorney audit companion

Every render can emit, alongside the draft, a machine-readable **decision record**
and a human-readable **audit companion** — so the reviewing attorney validates
*why each component is in the document* instead of reverse-engineering the output.

This is the mechanized form of the **step-5 auditability requirement** in the
assemble workflow ([OPERATING.md](OPERATING.md)): whether you assemble manually
or drive the engine, the final output must **surface what was swapped and why**.
The decision record and audit companion make that trail exhaustive and byte-exact.

```bash
python3 -m template_forge.engine.assemble \
    --manifest manifests/real_estate__deed.json \
    --slots slots.json \
    --out draft.md \
    --decision-record draft.decisions.json \
    --audit draft.audit.md
```

## The decision record (`template-forge/decision-record`, schema v1)

One JSON object per render:

| Key | What it answers |
|---|---|
| `facts` | Every context value with its **source**: `attorney_determination`, `slots_file`, `manifest_pin`, `manifest_default`, or `derived` (with the derivation rule, e.g. how `post_2019` was computed and from which date). |
| `discriminators` | The family selectors (e.g. `covenant_type`), their allowed enum, and whether the value was required-from-facts or pinned by a wrapper manifest. |
| `decisions` | **One entry per manifest block ref — including blocks that were NOT included.** Each carries the `when` expression, its result, `facts_considered` (the exact context keys the rule read, with values), the variant-selection trace, the block's `statutory_citation`, corpus `frequency`, per-slot fill status (`filled` / `marker` / `omitted`), and the rendered text. |
| `unfilled_markers` | Slots rendered as `[[ marker ]]` — values an attorney must supply. |
| `unset_optional_flags` | Optional flags left unset (their clause text was omitted). |
| `warnings` | Variant PII warnings. |

Invariant (enforced by the test suite): the `rendered` parts of the included
decisions reassemble byte-for-byte into the draft body, and `facts_considered`
always matches the names in the `when` expression. An omission is recorded as a
decision, never silently dropped.

## Fact provenance: `_meta` in the slots file

A slots file may carry an optional `_meta` object (stripped before rendering — it
never reaches Jinja) that upgrades any fact from "value in a file" to a recorded
determination:

```json
{
  "_meta": {
    "covenant_type": {
      "source": "attorney_determination",
      "author": "Reviewing Attorney",
      "date": "2026-06-10",
      "basis": "standard residential conveyance"
    }
  },
  "covenant_type": "quitclaim_covenant"
}
```

Attorney determinations sort to the top of the audit companion's fact ledger.
This is the seam where an intake/determinations workflow plugs in: the workflow
records who decided what and why; the engine just carries it through.

## The audit companion (Markdown)

Five sections, in review order:

1. **Fact ledger** — every fact, value, source, and detail; attorney
   determinations first.
2. **Family discriminators** — what instrument family was selected and whether it
   came from a determination or a manifest pin (and that a wrong value *fails*
   rather than defaulting — see the discriminator-enforcement design).
3. **Component decisions** — ✔/✘ per block: the rule, the facts it read,
   statutory authority, corpus support, variant trace, slot status, and the
   rendered text as a blockquote. Excluded components appear with the same rigor.
4. **Requires attorney action** — checkboxes for unfilled markers, GAP blocks
   (language to be authored, never invented), errors, and PII warnings.
5. **Paired requirements & deadlines** — the applicable companion-form /
   deadline checklist for this matter (see
   [PAIRED_REQUIREMENTS.md](PAIRED_REQUIREMENTS.md)).

Freeze the companion's format with a golden file and change it deliberately
(`--update` in the test runner).

## Provenance trail

The inline trail at the bottom of a full render also lists omitted blocks
(`- subject_to [omitted] — omitted (when: has_permitted_encumbrances → False)`),
so even the plain-text output records decisions both ways — the auditable
provenance the workflow's step 5 demands.

> AI/LLM-assisted metadata; the legal dispositions, citations, and every
> assembled document require attorney review. This is drafting scaffolding, not
> legal advice.
