# Maine Statutory-Citation Reference Table

`authority.jsonl` is a public reference table of Maine (and closely related federal)
statutory and rule citations, one JSON record per line. Every citation in it points to
**public law** — the Maine Revised Statutes, the Maine Rules of Court, and the small set
of federal authorities (e.g. the Bankruptcy Code, the Corporate Transparency Act /
FinCEN rules) that Maine practice cross-references. It carries **no firm content and no
client PII**; it is the public-law research product, published on its own.

## What each record contains

| field          | meaning |
|----------------|---------|
| `citation`     | The citation as it appears in source usage (the "as-found" form). |
| `current_form` | The correct/canonical current form of the citation, when a fix or confirmation was authored. May be `null` if unverified. |
| `status`       | `current`, `superseded`, `unverified`, or a similar verification state. |
| `source_url`   | Authoritative public URL the citation was verified against (see *Source attribution*). |
| `verified_on`  | The date (ISO `YYYY-MM-DD`) the citation was last checked against its `source_url`. |
| `note`         | Free-text explanation of the verification finding (why the current form is what it is, recodification history, etc.). |
| `lint_flags`   | Machine lint markers, if any. |

## Source attribution

Each record's `source_url` is the primary authority it was checked against. The hosts are
all official public legal-information sites:

- `legislature.maine.gov`, `mainelegislature.org`, `lldc.mainelegislature.org` — Maine
  Revised Statutes and legislative documents (Maine Legislature).
- `www.courts.maine.gov` — Maine Judicial Branch (Maine Rules of Court).
- `www.courts.nh.gov` — New Hampshire Judicial Branch (for the handful of NH cross-references).
- `www.govinfo.gov`, `www.ecfr.gov`, `www.federalregister.gov`, `www.fincen.gov` — federal
  statutes, the Code of Federal Regulations, the Federal Register, and FinCEN.

Citations are the work of the U.S. and Maine governments and are not themselves
copyrightable; this table is a research aid and is **not legal advice**. Always confirm a
citation against its `source_url` before relying on it — statutes are amended and
recodified.

## `verified_on` / `verified_as_of` semantics

`verified_on` is a point-in-time assertion: "as of this date, this citation was confirmed
against `source_url`." It is **not** a freshness guarantee going forward. A citation
verified on an older date may since have been amended; treat `verified_on` as the date to
re-verify *from*, not a claim that the law is unchanged today. (Some tooling refers to this
same value as `verified_as_of`; they mean the same thing — the date the record was checked.)

## What is intentionally excluded

A citation table can additionally link each citation to the specific drafting **blocks**
it appears in (a `blocks` field of block IDs, plus an `occurs_in` field marking where in a
block — metadata vs. body — the citation occurred). That citation→block linkage encodes
drafting judgment about *which clauses rely on which authority* and is treated as
private-pack content — it is **not published** here. Those fields have been stripped from
every record; only the public-law citation research is published, without any linkage.

A small number of source records that were entirely unverified (null `current_form`/
`source_url`/`verified_on`) and whose `citation` string carried a stray case-caption
fragment (an apparent personal name) were dropped during the clean-room copy, as they
carried no public-law value and would have leaked identifying text. This table therefore
has **172** records.
