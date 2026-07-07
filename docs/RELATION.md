# Relationship to `legal-facts-schema`

template-forge deliberately **does not define its own fact object**. The shared
fact object — `matter`, `parties`, `party`, and `facts` — is owned by the
published, standalone package
[`legal-facts-schema`](https://github.com/bedardandy/legal-facts-schema)
(Apache-2.0). template-forge **complements and builds on top of it**; it does not
duplicate or fork it.

## Division of responsibility

| Concern | Owner | Where |
|---|---|---|
| The fact object: `matter` / `parties` / `party` / `facts` | **legal-facts-schema** | its JSON Schema + `validate` / `resolve` |
| Dotted-key resolution (`items[0]`, `MISSING` sentinel) | **legal-facts-schema** | `resolve()` |
| Jurisdiction geography reference data (county/town/ZIP) | **legal-facts-schema** | its vendored reference data |
| **Document** metadata | template-forge | `contracts/document.py` |
| **Detected form fields** (PDF/DOCX field detection) | template-forge | `contracts/form_field.py` |
| **Classification** results | template-forge | `contracts/classification.py` |
| **Block / variant / slot / manifest** structure | template-forge | `contracts/template.py` + `data/schema/` |
| The assembly **engine** (choices, render, segments, export) | template-forge | `engine/` |
| The mining / de-identification **pipeline** | template-forge | `mining/`, `generator/` |

## How they fit together at runtime

1. A matter's **facts** are represented and validated by `legal-facts-schema`
   (the shared fact object).
2. template-forge's `engine/facts.py` projects that fact object into the flat
   **slots** a manifest's blocks render against, carrying `_meta` provenance.
3. template-forge's **manifests and blocks** (described by `contracts/template.py`
   and `data/schema/`) say *which* language composes a document and *how* facts
   select variants.

So: **legal-facts-schema answers "what are the facts?"; template-forge answers
"which document, built from which blocks, do those facts produce?"** The two are
intended to be used together, with template-forge depending on / referencing
legal-facts-schema for the shared fact object rather than restating it.

## Overlap that was intentionally dropped

The private source that template-forge graduated from carried a `matter/`
consolidation model and a Maine-court-forms `case_data` schema that overlapped
the fact-object domain (and referenced firm-internal systems). Those were **not**
brought into template-forge — they belong to the `legal-facts-schema` domain. If
you need the shared fact object, depend on `legal-facts-schema`.
