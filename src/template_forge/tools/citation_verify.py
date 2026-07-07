#!/usr/bin/env python3
"""Merge live-verification findings into an authority table, classify fixes,
and emit the citation audit + a reproducible fix set.

Consumes:
  - citations/distinct_citations.json   (from citation_lint.py: occurrences + lint flags)
  - /tmp/authority_merged.json          (live web/Context7 verification by subagents:
                                         citation -> {status, current_form, source_url,
                                         verified_on, note})

Produces:
  - citations/authority.jsonl   one record per distinct citation (lint + verification merged)
  - citations/citation_fixes.json   block-keyed AUTO fixes (safe, mechanical) that
        derive_blocks.py applies reproducibly: body text replacements + metadata
        statutory_citation rewrites. Section-renumbering / content changes are NOT
        auto-applied — they go to the review queue.
  - citations/CITATION_AUDIT.md   human-readable report: auto-applied vs. attorney-review.

Auto-fix policy (mechanical, no legal judgment):
  * Title prefix bump 18-A -> 18-C when the SECTION NUMBER is unchanged (pure recodification).
  * M.R.S.A. -> M.R.S. (Maine dropped the 'Annotated' short form).
  * M.R. Crim. P. -> M.R.U.Crim.P. (Unified Criminal Procedure, statewide 2015-07-01).
  * In the statutory POA notice-form bodies, 'Title 18-A' -> 'Title 18-C' (the current
    18-C s. 5-905 notice text was verified to print 18-C).
Everything else (section renumber e.g. 2-504->2-503, 5-804->5-805; scrambled POA authority
labels; repealed notarial/anatomical sections; CTA/BOI regulatory change) is REVIEW-only —
EXCEPT the block-keyed ADJUDICATED_META_FIXES below: POA authority-label re-pins that the
2026-06-20 adversarial audit adjudicated (your citation-audit notes remediation tier 4;
citations/your citation root-cause analysis), each verified against the local statute cache.
Those ARE legal-judgment section re-pins, applied deliberately and logged in their own
audit-report section (not under "mechanical").
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from template_forge.engine import paths as _pack

PACK = _pack.pack_root()
DISTINCT = (PACK / "citations") / "distinct_citations.json"
# Live-verification findings, committed so the pipeline runs offline; /tmp is the
# fresh-run fallback while a verification pass is in flight.
AUTH_IN = ((PACK / "citations") / "the authority-findings file")
if not AUTH_IN.exists():
    AUTH_IN = Path("/tmp/authority_merged.json")
# Original clause library — citation analysis runs against source, not derived blocks.
CLAUSES_SRC = Path(os.environ.get("TEMPLATE_FORGE_CLAUSE_DIR", "./clause_library")) / "clauses.cleaned.jsonl"
BLOCKS = PACK / "blocks" / "blocks.jsonl"
AUTH_OUT = (PACK / "citations") / "authority.jsonl"
FIXES_OUT = (PACK / "citations") / "citation_fixes.json"
AUDIT_OUT = (PACK / "citations") / "CITATION_AUDIT.md"

# Two per-pack adjudication tables customize the verifier for a specific clause
# library. They are **not baked in** — a pack's citation judgment (which block's
# body prints which statute) is private drafting metadata, so they load from an
# optional JSON file (``$TEMPLATE_FORGE_CITATION_FIXES`` or ``<pack>/citation_fixes_config.json``)
# and default to empty. Shape:
#     {"notice_body_rewrites": ["<block_id>", ...],
#      "adjudicated_meta_fixes": {"<block_id>": "<statutory_citation>", ...}}
def _load_pack_citation_config() -> dict:
    cfg_path = os.environ.get("TEMPLATE_FORGE_CITATION_FIXES")
    p = Path(cfg_path) if cfg_path else (PACK / "citation_fixes_config.json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


_PACK_CIT_CFG = _load_pack_citation_config()
# Blocks whose statutory notice-form body needs the current-title rewrite.
NOTICE_BODY_18C = set(_PACK_CIT_CFG.get("notice_body_rewrites", []))
# Adjudicated (non-mechanical) block -> statutory_citation re-pins for a pack.
ADJUDICATED_META_FIXES = dict(_PACK_CIT_CFG.get("adjudicated_meta_fixes", {}))

SECT = re.compile(r"§+\s*([\d]+-[\d]+(?:-?[A-Z])?|[\d]+(?:-[A-Z]?\d+)?)")


def section_of(s: str):
    m = SECT.search(s or "")
    return m.group(1) if m else None


def mech_rewrite(cit: str) -> str:
    """Apply the purely-mechanical normalizations to a citation string."""
    out = cit
    out = re.sub(r"\bM\.R\.S\.A\.", "M.R.S.", out)
    out = re.sub(r"Maine Revised Statutes Annotated", "Maine Revised Statutes", out)
    out = re.sub(r"\bM\.\s?R\.\s?Crim\.\s?P\.", "M.R.U.Crim.P.", out)
    out = re.sub(r"\b18-A M\.R\.S\.", "18-C M.R.S.", out)
    out = re.sub(r"\bTitle 18-A\b", "Title 18-C", out)
    return out


def main():
    distinct = {r["citation"]: r for r in json.loads(DISTINCT.read_text())}
    auth = json.loads(AUTH_IN.read_text())
    blocks = [json.loads(l) for l in BLOCKS.read_text().splitlines() if l.strip()]
    # Original clause source (stable basis for both body- and metadata-fix detection).
    src = {c["clause_id"]: c
           for c in (json.loads(l) for l in CLAUSES_SRC.read_text().splitlines() if l.strip())}
    src_body = {k: (v.get("representative_text") or "") for k, v in src.items()}
    src_cit = {k: (v.get("statutory_citation") or "") for k, v in src.items()}

    # --- merged authority.jsonl ---
    records = []
    for cit, d in distinct.items():
        a = auth.get(cit, {})
        records.append({
            "citation": cit,
            "blocks": d["blocks"],
            "occurs_in": d["occurs_in"],
            "lint_flags": d["lint_flags"],
            "status": a.get("status", "unverified"),
            "current_form": a.get("current_form"),
            "source_url": a.get("source_url"),
            "verified_on": a.get("verified_on"),
            "note": a.get("note"),
        })
    AUTH_OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")

    # --- classify auto vs review ---
    fixes: dict[str, dict] = {}          # block_id -> {statutory_citation, body_replace[]}
    auto_log, review_log = [], []

    for r in records:
        cit = r["citation"]
        status = r["status"]
        cur = r["current_form"] or ""
        is_meta = "metadata" in r["occurs_in"]
        # candidate mechanical rewrite of this exact metadata string
        mech = mech_rewrite(cit)

        # AUTO metadata fix: only when mechanical rewrite lands on the same section
        # the verifier confirmed (or verifier left it current and rewrite is cosmetic).
        if is_meta and mech != cit:
            same_section = (section_of(mech) == section_of(cur)) if cur else True
            cosmetic_only = section_of(mech) == section_of(cit)  # number unchanged
            if cosmetic_only and (same_section or not cur):
                touched = []
                for bid in r["blocks"]:
                    # rewrite only when `cit` IS the block's full ORIGINAL citation
                    # (not a sub-pattern of it); source-based -> stable across re-runs.
                    if src_cit.get(bid) == cit:
                        fixes.setdefault(bid, {})["statutory_citation"] = mech
                        touched.append(bid)
                if touched:
                    auto_log.append((cit, mech, touched, "mechanical metadata normalize"))
            else:
                review_log.append(r)
        elif status in ("superseded", "repealed", "changed", "uncertain"):
            # section renumber / content change / scrambled label -> attorney review
            if not (is_meta and mech != cit and section_of(mech) == section_of(cit)):
                review_log.append(r)

    # AUTO body fix: POA notice forms print current 18-C
    for r in records:
        if r["citation"] == "Title 18-A":
            for bid in r["blocks"]:
                if bid in NOTICE_BODY_18C:
                    fixes.setdefault(bid, {}).setdefault("body_replace", []).append(
                        ["Title 18-A", "Title 18-C"])
                    auto_log.append(("Title 18-A (body)", "Title 18-C", [bid],
                                     "current s.5-905 notice form verified to print 18-C"))
    # AUTO body fix: M.R. Crim. P. -> M.R.U.Crim.P. in criminal discovery bodies
    for b in blocks:
        body = src_body.get(b["block_id"], "")
        if re.search(r"\bM\.\s?R\.\s?Crim\.\s?P\.", body):
            fixes.setdefault(b["block_id"], {}).setdefault("body_replace", []).append(
                ["M.R. Crim.P.", "M.R.U.Crim.P."])
            fixes[b["block_id"]]["body_replace"].append(["M.R. Crim. P.", "M.R.U.Crim.P."])
            auto_log.append(("M.R. Crim.P. (body)", "M.R.U.Crim.P.", [b["block_id"]],
                             "Unified Criminal Procedure, statewide 2015-07-01"))

    # --- adjudicated re-pins (audit-verified legal judgment, logged separately) ---
    adjudicated_log = []
    for bid, new_cite in ADJUDICATED_META_FIXES.items():
        fixes.setdefault(bid, {})["statutory_citation"] = new_cite
        adjudicated_log.append((bid, src_cit.get(bid) or "(none)", new_cite))

    FIXES_OUT.write_text(json.dumps(fixes, indent=1, ensure_ascii=False))

    # --- audit report ---
    write_audit(records, auto_log, review_log, fixes, adjudicated_log)
    print(f"authority.jsonl: {len(records)} citations")
    print(f"auto-fixes: {len(fixes)} blocks touched ({len(auto_log)} rewrite ops, "
          f"{len(adjudicated_log)} adjudicated re-pins)")
    print(f"review queue: {len({r['citation'] for r in review_log})} distinct citations")
    print(f"-> {AUDIT_OUT}")


def write_audit(records, auto_log, review_log, fixes, adjudicated_log=()):
    from collections import Counter
    tally = Counter(r["status"] for r in records)
    L = []
    L.append("# Citation audit — block library\n")
    L.append("Generated by `tools/citation_lint.py` (deterministic) + "
             "`tools/citation_verify.py` (live web/Context7 verification by subagents, "
             "verified 2026-06-01).\n")
    L.append(f"**{len(records)} distinct citations.** Status: " +
             ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) + ".\n")
    L.append("Auto-fixes below are applied reproducibly by `derive_blocks.py` via "
             "`citations/citation_fixes.json`. The review queue requires attorney "
             "sign-off — section renumberings and content changes are **not** auto-applied.\n")

    L.append("## Auto-applied (mechanical, no legal judgment)\n")
    seen = set()
    for old, new, blks, why in auto_log:
        key = (old, new, tuple(blks))
        if key in seen:
            continue
        seen.add(key)
        L.append(f"- `{old}` → `{new}`  — {why}  _(blocks: {', '.join(blks)})_")
    if not auto_log:
        L.append("- _none_")

    L.append("\n## Adjudicated re-pins (applied — audit-verified, NOT mechanical)\n")
    L.append("POA authority labels where the labeling LLM mapped uniform-act numbering "
             "onto Maine's enactment (your citation root-cause analysis). Re-pinned per "
             "your citation-audit notes; each target section verified against the local "
             "statute cache and the `the authority-findings file` recommendation. "
             "`statutory_citation_original` preserves the pre-fix label on each block.\n")
    for bid, old, new in adjudicated_log:
        L.append(f"- `{bid}`: `{old}` → `{new}`")
    if not adjudicated_log:
        L.append("- _none_")

    L.append("\n## Attorney-review queue (NOT auto-applied)\n")
    # group review items by theme
    groups = {
        "Probate recodification — SECTION RENUMBERED (number changed, verify content)": [],
        "POA authority labels — section/label mismatch": [],
        "Repealed / replaced statutes": [],
        "Corporate Transparency Act / BOI — regulatory change (content out of date)": [],
        "Other / uncertain": [],
    }
    for r in sorted({rr["citation"]: rr for rr in review_log}.values(),
                    key=lambda x: x["citation"]):
        cit, cur, note = r["citation"], r["current_form"] or "", (r["note"] or "")
        if "2-503" in cur or "5-805" in cur or ("§ 2-504" in cit and "2-503" in cur):
            groups["Probate recodification — SECTION RENUMBERED (number changed, verify content)"].append(r)
        elif re.search(r"5-9\d\d|5-916|durability|real property|stocks|insurance|retirement|claims",
                       cur.lower()) and "Power of Attorney" in (cit + cur + note):
            groups["POA authority labels — section/label mismatch"].append(r)
        elif r["status"] == "repealed" or "REPEALED" in note:
            groups["Repealed / replaced statutes"].append(r)
        elif "5336" in cit or "1010.380" in cit or "Transparency" in cit:
            groups["Corporate Transparency Act / BOI — regulatory change (content out of date)"].append(r)
        else:
            groups["Other / uncertain"].append(r)

    for title, items in groups.items():
        if not items:
            continue
        L.append(f"\n### {title}\n")
        for r in items:
            blks = ", ".join(r["blocks"])
            L.append(f"- **`{r['citation']}`**  _(blocks: {blks})_")
            L.append(f"  - → recommended: `{r['current_form']}`")
            if r["blocks"] and set(r["blocks"]) <= set(ADJUDICATED_META_FIXES):
                L.append("  - status: RE-PINNED (see 'Adjudicated re-pins' above)")
            if r["note"]:
                L.append(f"  - {r['note'].strip()}")
            if r["source_url"]:
                L.append(f"  - source: {r['source_url']}")
    AUDIT_OUT.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
