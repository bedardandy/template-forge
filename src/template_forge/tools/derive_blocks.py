#!/usr/bin/env python3
"""Derive the block library from a clause library.

Transforms the attorney-authored clause corpus
(`clause_library/clauses.cleaned.jsonl` + `variants.cleaned.jsonl`) into:

  - blocks/blocks.jsonl      one record per canonical block (PII-free metadata
                             + slot list + variant alternatives)
  - blocks/partials/<id>.md.j2   the block body as a Jinja2 partial, with
                             quasi-identifiers replaced by {{ slot }} tokens

Slot extraction is two-layered:
  1. Deterministic regex for *structured* quasi-identifiers (dates, dollar
     amounts, docket/case numbers, registry book/page, emails, phones). High
     precision, no model needed.
  2. An optional LLM parametrization map (`tools/parametrize_map.json`) supplies
     PII-free rewrites for clauses that still carry *named-entity* PII (party /
     notary / attorney proper names) after step 1. Produced by a separate
     subagent pass — see --worklist.

Nothing here invents legal language: the parametrized text is the attorney's
own clause with identifiers blanked to slots.

Usage:
    python3 tools/derive_blocks.py            # build blocks.jsonl + partials
    python3 tools/derive_blocks.py --worklist # list clauses needing LLM parametrization
    python3 tools/derive_blocks.py --check    # validate manifests reference real blocks
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from template_forge.engine import paths as _pack

PACK = _pack.pack_root()
CLAUSE_DIR = Path(os.environ.get("TEMPLATE_FORGE_CLAUSE_DIR", "./clause_library"))
CLAUSES = CLAUSE_DIR / "clauses.cleaned.jsonl"
VARIANTS = CLAUSE_DIR / "variants.cleaned.jsonl"
PARAM_MAP = PACK / "tools" / "parametrize_map.json"
CITATION_FIXES = (PACK / "citations") / "citation_fixes.json"
OUT_JSONL = PACK / "blocks" / "blocks.jsonl"
PARTIAL_DIR = PACK / "blocks" / "partials"
# Hand-authored blocks that are NOT derived from the clause corpus (the Maine
# statutory deed / mortgage family — bodies are statutory short-form language,
# not parametrized attorney clauses). Merged in so regenerate is reproducible
# AND non-destructive. Single source of truth lives under blocks/authored/.
AUTHORED_JSONL = PACK / "blocks" / "authored" / "authored_blocks.jsonl"
AUTHORED_PARTIALS = PACK / "blocks" / "authored" / "partials"

# Variant deviation categories that represent a real drafting choice (mirrors
# the engine's actionable categories). stylistic/typo are dropped.
ACTIONABLE = {
    "fact_pattern", "party_role", "jurisdiction",
    "citation_update", "scope_expansion", "scope_restriction",
}

# ---- structured quasi-identifier patterns -> slot name -------------------
# Order matters: most specific first. Each (regex, slot_base, type).
STRUCT_PATTERNS = [
    (re.compile(r"\b[A-Z]{2,5}-[A-Z]{2}-\d{2,4}-\d{2,5}\b"), "case_number", "case_number"),
    (re.compile(r"\bBook\s+\d+,?\s+Page\s+\d+\b", re.I), "registry_ref", "registry_ref"),
    (re.compile(r"\$[\d,]+(?:\.\d{2})?\b"), "amount", "amount"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "email", "email"),
    (re.compile(r"\b(?:\(\d{3}\)\s*|\d{3}[-.])\d{3}[-.]\d{4}\b"), "phone", "phone"),
    (re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+\d{4}\b"), "date", "date"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "date", "date"),
]

# Capitalized proper-noun run (2+ tokens) that is likely a personal/entity name.
# Used only to FLAG clauses for the LLM worklist, not to auto-substitute.
# Middle tokens may be initials ("E.", "N.") or capitalized words.
NAME_RUN = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:[A-Z]\.|[A-Z][a-z]+)){1,3}\b")
# Common capitalized legal phrases that are NOT names (don't flag on these alone).
LEGAL_CAPS = {
    "Last Will", "Personal Representative", "State Of", "County Of", "Of Maine",
    "Now Comes", "Wherefore", "United States", "District Court", "Superior Court",
    "Probate Court", "Family Division", "Free Act", "Power Of", "Health Care",
    "Real Estate", "Notary Public", "Be It", "In Witness", "Know All",
}


def load_jsonl(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def scope_of(clause: dict) -> str:
    """Block scope = how broadly the block applies, per the DocuJSONL rule that
    *diversity* of observed contexts (not frequency) broadens scope."""
    dts = set(clause.get("doc_types_seen") or [])
    juris = clause.get("jurisdiction")
    if clause.get("function") in {"closing_correspondence", "disclaimer", "integration",
                                   "severability", "waiver"}:
        return "universal"
    if juris in (None, "general") and len(dts) >= 3:
        return "cross_practice"
    if clause.get("statutory_citation"):
        return "statutory_short_form"
    return "specific"


def apply_struct_slots(text: str):
    """Replace structured quasi-identifiers with {{ slot }} tokens.
    Returns (new_text, [slot dicts]). Duplicate slot bases get _2, _3 suffixes."""
    slots: list[dict] = []
    counts: dict[str, int] = {}

    def sub(m, base, typ):
        counts[base] = counts.get(base, 0) + 1
        name = base if counts[base] == 1 else f"{base}_{counts[base]}"
        slots.append({"name": name, "type": typ, "required": True})
        return "{{ " + name + " }}"

    for rx, base, typ in STRUCT_PATTERNS:
        text = rx.sub(lambda m, b=base, t=typ: sub(m, b, t), text)
    return text, slots


# ALL-CAPS personal name (court captions print names in caps): a run with a
# middle initial like "ROBERT A. HOYT" or a run adjacent to a Plaintiff/Defendant
# caption marker. Heading phrases ("RISK OF LOSS") lack the initial and the marker.
CAPS_NAME_INITIAL = re.compile(r"\b[A-Z][A-Z'-]+\s+[A-Z]\.\s+[A-Z][A-Z'-]+\b")
CAPS_BY_MARKER = re.compile(r"\b([A-Z][A-Z'’.-]+(?:\s+[A-Z][A-Z'’.-]+){0,2})\s*,?\s*\)?\s*"
                            r"(?:Plaintiff|Defendant)\b")


def name_flags(text: str) -> list[str]:
    """Return proper-name runs that look like residual PII (for the worklist)."""
    out = []
    for m in NAME_RUN.finditer(text):
        run = m.group(0).strip()
        if run in LEGAL_CAPS:
            continue
        if any(run.startswith(p) or run.endswith(p) for p in LEGAL_CAPS):
            continue
        out.append(run)
    for m in CAPS_NAME_INITIAL.finditer(text):
        out.append(m.group(0).strip())
    for m in CAPS_BY_MARKER.finditer(text):
        run = m.group(1).strip()
        if run not in {"STATE OF MAINE", "STATE", "MAINE"} and not run.endswith("OF"):
            out.append(run)
    return list(dict.fromkeys(out))


def variants_for(cluster: dict) -> list[dict]:
    out = []
    for d in cluster.get("deviations", []):
        cat = d.get("signal_category")
        if cat not in ACTIONABLE:
            continue
        out.append({
            "variant_id": f"{cluster['cluster_id']}_v{d['variant_idx']}",
            "signal_category": cat,
            "trigger": d.get("inferred_trigger", ""),
            "text": d["text"],
            "confidence": "high" if not d.get("is_singleton") else "low",
            "count": d.get("count", 1),
        })
    return out


def build(check_only=False, worklist=False):
    clauses = load_jsonl(CLAUSES)
    variants = {v["cluster_id"]: v for v in load_jsonl(VARIANTS)}
    param_map = json.loads(PARAM_MAP.read_text()) if PARAM_MAP.exists() else {}
    cite_fixes = json.loads(CITATION_FIXES.read_text()) if CITATION_FIXES.exists() else {}

    worklist_rows = []
    blocks = []
    PARTIAL_DIR.mkdir(parents=True, exist_ok=True)

    for c in clauses:
        cid = c["clause_id"]
        raw = c["representative_text"].strip()

        # Prefer an LLM-parametrized, PII-free rewrite when available.
        pm = param_map.get(cid)
        if pm:
            text = pm["text"]
            slots = pm.get("slots", [])
        else:
            text, slots = apply_struct_slots(raw)

        # Apply reproducible citation fixes (from tools/citation_verify.py).
        fix = cite_fixes.get(cid, {})
        for a, repl in fix.get("body_replace", []):
            text = text.replace(a, repl)
        cite = fix.get("statutory_citation", c.get("statutory_citation"))

        residual = name_flags(text)
        if residual and not pm:
            worklist_rows.append({"clause_id": cid, "function": c["function"],
                                  "subtype": c.get("subtype"), "names": residual,
                                  "text": raw})

        block = {
            "block_id": cid,
            "function": c["function"],
            "subtype": c.get("subtype"),
            "scope": scope_of(c),
            "jurisdiction": c.get("jurisdiction"),
            "statutory_citation": cite,
            "statutory_citation_original": c.get("statutory_citation")
            if cite != c.get("statutory_citation") else None,
            "triggers": c.get("triggers"),
            "confidence": c.get("confidence"),
            "frequency": c.get("frequency"),
            "default_in": c.get("doc_types_seen") or [],
            "slots": slots,
            "partial": f"partials/{cid}.md.j2",
            "variants": variants_for(variants.get(cid, {})),
            "pii_parametrized": bool(pm),
            "pii_residual": residual if not pm else [],
        }
        blocks.append((block, text))

    # The clause corpus carries duplicate clause_ids (a block appear
    # twice with DIFFERING payloads); last-wins silently shadowed the earlier
    # record's text/citation. Dedupe deterministically: keep the record with a
    # statutory_citation (more informative), else the first seen.
    by_id, order, dropped = {}, [], []
    for block, text in blocks:
        bid = block["block_id"]
        if bid not in by_id:
            by_id[bid] = (block, text)
            order.append(bid)
        else:
            if not by_id[bid][0].get("statutory_citation") and block.get("statutory_citation"):
                by_id[bid] = (block, text)
            dropped.append(bid)
    blocks = [by_id[b] for b in order]
    if dropped:
        print(f"  deduped {len(dropped)} duplicate clause ids: "
              f"{', '.join(sorted(set(dropped)))}")

    # Merge hand-authored statutory blocks (deed/mortgage family). Their bodies
    # live as partials under blocks/authored/partials/; metadata in authored_blocks.jsonl.
    if AUTHORED_JSONL.exists():
        for ab in load_jsonl(AUTHORED_JSONL):
            pf = AUTHORED_PARTIALS / f"{ab['block_id']}.md.j2"
            text = pf.read_text().rstrip("\n") if pf.exists() else ""
            ab.setdefault("statutory_citation_original", None)
            ab.setdefault("pii_parametrized", False)   # statutory text, no PII to parametrize
            ab.setdefault("pii_residual", [])
            blocks.append((ab, text))

    if worklist:
        print(json.dumps(worklist_rows, indent=2, ensure_ascii=False))
        print(f"\n# {len(worklist_rows)} clauses need LLM name-parametrization",
              flush=True)
        return

    if check_only:
        cnt = {}
        for b, _ in blocks:
            cnt[b["block_id"]] = cnt.get(b["block_id"], 0) + 1
        dup = sorted(k for k, v in cnt.items() if v > 1)
        if dup:
            print(f"DUPLICATE block_ids after dedup (authored/clause collision?): {dup}")
            raise SystemExit(1)
        ids = {b["block_id"] for b, _ in blocks}
        dangling, gaps = [], []
        nman = 0
        for mf in sorted((PACK / "manifests").glob("*.json")):
            nman += 1
            man = json.loads(mf.read_text())
            for ref in man.get("blocks", []):
                if "literal_md" in ref:
                    continue
                bid = ref["block_id"]
                if bid in ids:
                    continue
                # GAP_* refs are intentional, library-not-yet-authored gaps.
                (gaps if bid.startswith("GAP_") else dangling).append((mf.name, bid))
        if dangling:
            print("DANGLING block refs (unexpected):")
            for fn, bid in dangling:
                print(f"  {fn}: {bid}")
            raise SystemExit(1)
        print(f"OK: {nman} manifests, all non-GAP block refs resolve "
              f"({len(ids)} blocks).")
        if gaps:
            print(f"  {len(gaps)} intentional GAP refs (scaffolded, awaiting library clause):")
            for fn, bid in gaps:
                print(f"    {fn}: {bid}")
        return

    # write partials + blocks.jsonl
    for block, text in blocks:
        (PARTIAL_DIR / f"{block['block_id']}.md.j2").write_text(text + "\n")
    with OUT_JSONL.open("w") as f:
        for block, _ in blocks:
            f.write(json.dumps(block, ensure_ascii=False) + "\n")

    n_resid = sum(1 for b, _ in blocks if b["pii_residual"])
    n_param = sum(1 for b, _ in blocks if b["pii_parametrized"])
    print(f"Wrote {len(blocks)} blocks -> {OUT_JSONL}")
    print(f"  partials: {PARTIAL_DIR}")
    print(f"  LLM-parametrized: {n_param}   regex-only w/ residual names: {n_resid}")
    if n_resid:
        print("  (run --worklist, fill tools/parametrize_map.json, re-run to clear)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate manifest block refs")
    ap.add_argument("--worklist", action="store_true", help="emit LLM parametrization worklist")
    a = ap.parse_args()
    build(check_only=a.check, worklist=a.worklist)
