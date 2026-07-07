# Shared fact registry — one matter, many artifacts

`facts/fact_keys.json` + `engine/facts.py`: a matter's facts are recorded
**once**, typed and provenance-carrying, then projected into every artifact of
the transaction. A single fact flows into every slot and every companion form
that needs it — recorded once, used everywhere.

For example, a **post-closing grantee mailing address** (the address that belongs
on the deed, the transfer-tax declaration, and the 1099-S — *not* the grantee's
current address) flows from one fact into the deed's `grantee_address` slot and
into an external form's `transferee.mailing_address` / `_city` / `_state` / `_zip`
keys. The fact registry declares those form mappings alongside the slot, so the
same value lands consistently in the instrument and in every paired form.

The published **`legal-facts-schema`** package owns the fact object itself
(`matter` / `parties` / `party` / `facts`); this registry maps those typed facts
onto template-forge **slots**. See [RELATION.md](RELATION.md).

## Matter file

```json
{
  "matter_id": "EXAMPLE-2026-0042",
  "facts": {
    "party.grantee.mailing_address.post_closing": {
      "value": {"street": "12 Meadow Lane", "city": "Anytown", "state": "ME", "zip": "00000"},
      "source": "client_intake", "date": "2026-06-01",
      "basis": "grantees will occupy the property after closing"
    },
    "instrument.covenant_type": {
      "value": "quitclaim_covenant",
      "source": "attorney_determination", "author": "Reviewing Attorney",
      "date": "2026-06-09", "basis": "standard residential conveyance"
    }
  }
}
```

Every fact carries a **source**: `attorney_determination`, `client_intake`,
`document_extraction` (must name the `document`; pair with char spans when an
extractor provides them), or `derived`. An unattributed fact is a validation
error — nothing reaches a recorded instrument without a recorded origin. The
provenance flows automatically into `_meta`, so the decision record and audit
companion show who asserted each fact without any extra wiring.

## Typed keys

Key names are role- and time-qualified (`party.grantee.mailing_address.
post_closing`), and the registry validates by type: `recording_ref` objects
require registry/book/page with registry-shaped book/page numbers; dates must
parse; enums (e.g. `party.grantor.capacity`) are enforced — and an enum value
then hits the manifests' discriminators, so a trustee grantor recorded at intake
routes to the trustee manifest at render time. Unknown keys are rejected: extend
`fact_keys.json` rather than free-forming.

Each key declares:

- its `type` (`party`, `string`, `enum`, `flag`, `address`, `date`, `money`,
  `longtext`, `recording_ref`, `list`, …);
- the engine `slot` (or `slots` object for multi-part keys) it feeds — **slot
  names are engine-global**, so any manifest that uses the slot is served;
- an optional `forms` mapping to external form fact-keys it carries to;
- an optional `implies_flag` (see below) and free-form `_note`.

A manifest's `required_slots` entry is normally a bare slot name (always
required). It may also be an object `{"slot": "...", "when": "<guard>"}` that is
required only when the guard is truthy — same evaluator as block `when` clauses —
so a capacity branch can hard-require its own slots without forcing them on the
others (e.g. `trustee_name` / `trust_name` only when
`declarant_capacity == 'trustee'`). See `assemble.required_slots_for`.

## CLI

```bash
# validate + derive the intake question list (unsatisfied required_slots)
python3 -m template_forge.engine.facts --matter M.json --check --template real_estate__deed

# project to template-forge slots (+_meta) and render with full audit
python3 -m template_forge.engine.facts --matter M.json --slots-out slots.json
python3 -m template_forge.engine.assemble --manifest manifests/real_estate__deed.json \
    --slots slots.json --out deed.md --audit deed.audit.md

# project to an external form's fact keys
python3 -m template_forge.engine.facts --matter M.json --form ME-RETTD --out rettd_facts.json
```

`missing_required()` doubles as the intake driver: it reports exactly which facts
the matter still needs for a chosen template, instead of discovering gaps as
`[[ markers ]]` in a draft.

Longtext facts with companion flags (`encumbrances.subject_to`,
`rights.appurtenant`, `transfer_tax.*`) set their `implies_flag` automatically —
supplying the substance turns the clause on.

Use a fixture matter with **fictional** parties (Doe / Roe / Public), exercised
end-to-end by the test suite. Never commit a real matter.

> Any external form ids or statutory references above are illustrative of **one
> example jurisdiction profile**; a pack targets whatever jurisdiction its keys
> and manifests declare.
