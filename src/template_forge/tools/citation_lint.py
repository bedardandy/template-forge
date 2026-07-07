#!/usr/bin/env python3
"""Deterministic citation staleness lint for the block library.

Scans BOTH the `statutory_citation` metadata AND the rendered body of every
block (partial + each variant) for legal citations, extracts the distinct set,
and applies deterministic staleness rules:

  - stale_18A   : a bare Title 18-A / "18-A M.R.S." probate cite. Maine recodified
                  its probate code from Title 18-A to Title **18-C** effective
                  2019-07-01 (P.L. 2017, c. 402). A reference that says 18-A with
                  no 18-C cross-note is stale for documents executed on/after that
                  date. (A cite that already says "formerly 18-A" / "now 18-C" is OK.)
  - msa_annotated: "M.R.S.A." / "Maine Revised Statutes Annotated" — Maine's
                  official short form is now "M.R.S." (the annotated designation
                  was dropped). Cosmetic normalization, low severity.
  - in_body_stale: the block BODY contains a stale cite even though its metadata
                  citation looks current (e.g. the statutory notice-to-principal
                  form whose text still prints "Title 18-A").

Outputs:
  - citations/distinct_citations.json : the worklist handed to the live-verify
    pass (one entry per distinct normalized citation + where it occurs + lint flags)
  - prints a summary table.

This pass makes NO network calls. The live-verify pass (citation_verify.py)
confirms each distinct citation is still good law.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

from template_forge.engine import paths as _pack

PACK = _pack.pack_root()
# Lint the ORIGINAL clause library, NOT the derived/fixed blocks — keeps the
# analysis stable and idempotent regardless of which fixes have been applied.
CLAUSE_DIR = Path(os.environ.get("TEMPLATE_FORGE_CLAUSE_DIR", "./clause_library"))
CLAUSES = CLAUSE_DIR / "clauses.cleaned.jsonl"
VARIANTS = CLAUSE_DIR / "variants.cleaned.jsonl"
OUT = (PACK / "citations") / "distinct_citations.json"

# Citation surface patterns. Each yields a normalized citation string.
PATTNS = [
    # Maine titles: "18-C M.R.S. § 5-902", "18-A M.R.S.A. § 5-947", "Title 18-A"
    re.compile(r"\bTitle\s+(\d+-[A-Z])\b"),
    re.compile(r"\b(\d+-[A-Z])\s+M\.?R\.?S\.?(?:A\.?)?\s*§*\s*[\d\-]+(?:\([^)]*\))?"),
    re.compile(r"\b(\d+)\s+M\.?R\.?S\.?(?:A\.?)?\s*§+\s*[\d\-]+(?:\([^)]*\))?"),
    # Rules of procedure
    re.compile(r"\bM\.?\s?R\.?\s?(?:U\.?)?\s?Civ\.?\s?P\.?\s*[\d]+[A-Za-z()0-9./ ]*"),
    re.compile(r"\bM\.?\s?R\.?\s?U\.?\s?Crim\.?\s?P\.?\s*[\d]+[A-Za-z()0-9./ ]*"),
    re.compile(r"\bM\.?\s?R\.?\s?Crim\.?\s?P\.?\s*[\d]+[A-Za-z()0-9./ ]*"),
    # Federal
    re.compile(r"\b\d+\s+U\.?S\.?C\.?\s*§+\s*[\d\-]+[A-Za-z]?"),
    re.compile(r"\b\d+\s+C\.?F\.?R\.?\s*(?:Part|§)?\s*[\d.\-]+"),
    # Case law
    re.compile(r"\b[A-Z][a-z]+ v\. [A-Z][a-z]+, \d+ U\.S\. \d+ \(\d{4}\)"),
]

PROBATE_CONTEXT = re.compile(r"probate|power of attorney|health-?care|advance|"
                             r"will|testament|trust|guardian|estate|fiduciary|"
                             r"personal representative", re.I)


def extract(text: str) -> set[str]:
    found = set()
    for rx in PATTNS:
        for m in rx.finditer(text or ""):
            found.add(re.sub(r"\s+", " ", m.group(0)).strip().rstrip(",.;"))
    return found


def main():
    clauses = [json.loads(l) for l in CLAUSES.read_text().splitlines() if l.strip()]
    vclusters = {v["cluster_id"]: v
                 for v in (json.loads(l) for l in VARIANTS.read_text().splitlines() if l.strip())}
    ACTIONABLE = {"fact_pattern", "party_role", "jurisdiction",
                  "citation_update", "scope_expansion", "scope_restriction"}
    # Blocks whose in-body stale cite is intentionally handled by an execution-date
    # gated citation_update substitute variant (e.g. a block: dominant=18-A for
    # pre-2019 execution, variant=18-C for post-2019). Not a defect.
    handled_by_substitute = {
        cid for cid, v in vclusters.items()
        if any(d.get("signal_category") == "citation_update" for d in v.get("deviations", []))
    }
    # citation -> {sources: set, contexts: set}
    occ = defaultdict(lambda: {"blocks": set(), "in": set()})

    for c in clauses:
        bid = c["clause_id"]
        body = c.get("representative_text") or ""
        meta_cit = c.get("statutory_citation") or ""
        for cc in extract(meta_cit) | ({meta_cit} if meta_cit else set()):
            occ[cc]["blocks"].add(bid); occ[cc]["in"].add("metadata")
        for cc in extract(body):
            occ[cc]["blocks"].add(bid); occ[cc]["in"].add("body")
        for d in vclusters.get(bid, {}).get("deviations", []):
            if d.get("signal_category") not in ACTIONABLE:
                continue
            vid = f"{bid}_v{d['variant_idx']}"
            for cc in extract(d.get("text", "")):
                occ[cc]["blocks"].add(bid); occ[cc]["in"].add(f"variant:{vid}")

    rows = []
    for cit, d in sorted(occ.items()):
        flags = []
        low = cit.lower()
        # stale 18-A: a Title/section in 18-A with no 18-C cross-note in the SAME string
        if re.search(r"\b18-A\b", cit) and "18-c" not in low and \
           not re.search(r"formerly|now |successor|cf\.", low):
            flags.append("stale_18A")
        if re.search(r"M\.?R\.?S\.?A\.?\b", cit) or "annotated" in low:
            flags.append("msa_annotated")
        # body-stale: a stale cite that appears in a block body (not just a note),
        # unless that block resolves it via a date-gated citation_update substitute.
        if "stale_18A" in flags and "body" in d["in"]:
            if d["blocks"] <= handled_by_substitute:
                flags.append("handled_by_substitute")
            else:
                flags.append("in_body_stale")
        rows.append({
            "citation": cit,
            "blocks": sorted(d["blocks"]),
            "occurs_in": sorted(d["in"]),
            "lint_flags": flags,
            "verify_status": "pending",
        })

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=1, ensure_ascii=False))

    stale = [r for r in rows if "stale_18A" in r["lint_flags"]]
    body_stale = [r for r in rows if "in_body_stale" in r["lint_flags"]]
    msa = [r for r in rows if "msa_annotated" in r["lint_flags"]]
    print(f"distinct citations: {len(rows)}  ->  {OUT}")
    print(f"  stale_18A (probate, no 18-C note): {len(stale)}")
    for r in stale:
        print(f"    - {r['citation']}   blocks={r['blocks']}  in={r['occurs_in']}")
    print(f"  in_body_stale (stale cite in rendered text): {len(body_stale)}")
    for r in body_stale:
        print(f"    - {r['citation']}   blocks={r['blocks']}")
    print(f"  msa_annotated (M.R.S.A.->M.R.S.): {len(msa)} citations")


if __name__ == "__main__":
    main()
