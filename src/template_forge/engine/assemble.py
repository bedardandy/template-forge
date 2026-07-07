#!/usr/bin/env python3
"""Assemble a Maine legal document from a manifest + a slots/fact-pattern file.

    python3 engine/assemble.py --manifest manifests/estate_planning__poa.json \
            --slots fixtures/poa.json [--out draft.md] [--no-trail]

The slots file is a single JSON object that is BOTH the Jinja render context
(slot values like {"principal_name": "Jane Doe", ...}) AND the fact-pattern
context for block choices (flags like {"post_2019": true, "jurisdiction": "ME"}).
Derived flags (e.g. post_2019 from execution_date) are computed automatically.

Output: the rendered document, followed by a provenance trail explaining every
add/remove/substitute decision — mirroring the operating guide's step-5 auditability requirement.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import choices  # noqa: E402
import paths  # noqa: E402
import render  # noqa: E402
import segments  # noqa: E402

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def parse_date(value) -> _dt.date | None:
    """Parse the date formats deeds actually use into a real date.
    Handles ISO, M/D/Y(YYYY|YY), 'June 9, 2026', and '9th day of June, 2026'."""
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
        else:
            m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", s)
            if m and m.group(1).lower() in _MONTHS:
                mo, d, y = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
            else:
                m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+([A-Za-z]+),?\s+(\d{4})", s)
                if m and m.group(2).lower() in _MONTHS:
                    d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
                else:
                    return None
    try:
        return _dt.date(y, mo, d)
    except ValueError:
        return None


def derive_flags(ctx: dict) -> dict:
    """Compute convenience flags from raw slot values."""
    ctx = dict(ctx)
    ed = ctx.get("execution_date") or ctx.get("signing_date") or ctx.get("date")
    if ed:
        parsed = parse_date(ed)
        if parsed is not None:
            ctx.setdefault("post_2019", parsed >= _dt.date(2019, 7, 1))
        else:
            # full date unparseable — a bare year settles it except 2019 itself
            # (the POA Act cutoff is 2019-07-01); ambiguous stays unset rather
            # than silently picking a statutory variant.
            ym = re.search(r"\b(19|20)(\d{2})\b", str(ed))
            if ym:
                y = int(ym.group(0))
                if y != 2019:
                    ctx.setdefault("post_2019", y > 2019)
    # federal lead-disclosure gate (42 U.S.C. § 4852d "target housing" = built
    # prior to 1978): derive the pre_1978_housing selector from a supplied
    # year_built. NO year at all is NOT derived — the manifest discriminator
    # hard-fails instead, so the gate is never silently guessed.
    yb = ctx.get("year_built")
    if yb is not None and "pre_1978_housing" not in ctx:
        ym = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", str(yb))
        if ym:
            ctx["pre_1978_housing"] = "yes" if int(ym.group(0)) < 1978 else "no"
    # summary-judgment contradiction-guard input (litigation__motion): the
    # M.R.Civ.P. 7(b)(1)(B)/56(h) notice is MANDATORY for a summary-judgment
    # motion, so the manifest hard-fails when the attorney-supplied motion_rule
    # invokes Rule 56 / summary judgment but the summary_judgment flag that
    # gates the notice block is unset. The mention is derived here (regex,
    # case-insensitive) rather than trusted to the optional flag alone.
    mr = ctx.get("motion_rule")
    if mr is not None and "motion_rule_invokes_rule56" not in ctx:
        ctx["motion_rule_invokes_rule56"] = bool(re.search(
            r"\brule\s*56\b|\b56\s*\(|summary\s+judgment", str(mr), re.I))
    ctx.setdefault("jurisdiction", "ME")
    return ctx


def required_slots_for(manifest: dict, ctx: dict) -> list:
    """Slot names this matter must satisfy. A required_slots entry is either a
    bare string (always required) or an object {"slot", "when"} that is only
    required when its guard is truthy against ctx — reusing the same guard
    evaluator as block `when` clauses. Lets a capacity branch (e.g. a trustee
    declarant) hard-require its own slots (trust_name, trustee_name) without
    forcing them on the other branches. A guard that errors fails safe to
    required (better a spurious intake question than a silent blank)."""
    out = []
    for entry in manifest.get("required_slots", []):
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict) and entry.get("slot"):
            when = entry.get("when")
            if when is None:
                out.append(entry["slot"])
                continue
            try:
                ok = bool(choices.safe_eval(when, ctx))
            except Exception:
                ok = True
            if ok:
                out.append(entry["slot"])
    return out


def enforce_discriminators(manifest: dict, slots: dict, ctx: dict) -> None:
    """Family-selector slots (covenant_type, pr_deed_type, ...) get strict
    handling: a wrong or missing discriminator silently renders the WRONG
    INSTRUMENT (e.g. a deed titled WARRANTY whose granting clause says
    QUITCLAIM), so fail loudly instead of defaulting."""
    for slot, spec in (manifest.get("discriminators") or {}).items():
        pin = spec.get("pin")
        if pin is not None:
            if slot in slots and slots[slot] != pin:
                raise SystemExit(
                    f"error: {manifest.get('template_id')} pins {slot}={pin!r} "
                    f"but the slots file sets {slot}={slots[slot]!r} — "
                    f"use the base manifest for other {slot} values")
            ctx[slot] = pin
        if spec.get("required") and slot not in ctx:
            raise SystemExit(
                f"error: required discriminator '{slot}' missing — "
                f"set one of {spec.get('enum')}")
        if spec.get("enum") and slot in ctx and ctx[slot] not in spec["enum"]:
            hint = f" ({spec['hint']})" if spec.get("hint") else ""
            raise SystemExit(
                f"error: {slot}={ctx[slot]!r} is not a valid value — "
                f"expected one of {spec['enum']}{hint}")
        # fact-conditioned constraint: when a fact pattern holds (e.g. a
        # safety_concern flag on a relocation notice), the discriminator MUST
        # be one of the listed values — a manifest default or a contradictory
        # explicit choice would route the document dangerously, so hard-fail.
        for rule in spec.get("require") or []:
            try:
                hit = bool(choices.safe_eval(rule.get("when"), ctx))
            except Exception:
                hit = True   # unevaluable guard fails safe to enforced
            if hit and ctx.get(slot) not in (rule.get("one_of") or []):
                hint = f" ({rule['hint']})" if rule.get("hint") else ""
                raise SystemExit(
                    f"error: facts match {rule.get('when')!r} but "
                    f"{slot}={ctx.get(slot)!r} — must be explicitly set to one "
                    f"of {rule.get('one_of')}{hint}")


def enforce_constraints(manifest: dict, ctx: dict) -> list:
    """Manifest-declared hard-fail rules beyond single-slot enums: cross-slot
    consent gates (e.g. the 33 M.R.S. § 1602-117(d) unanimity requirement) and
    statutory content checks (e.g. § 1602-105(a)(1) condominium naming). Each
    entry: {"id", "when" guard, ONE requirement, "error"}. Requirements:
      - "require": expr        — under the guard, fail unless the expr is truthy
      - "require_substring": {"slots": [...], "text": "..."} — under the guard,
        the first SET slot must contain the text (case-insensitive)
      - neither                — the guard itself is the failure condition
    These guard void-instrument classes, so a failing OR erroring rule kills
    the render — fail loudly, never default. Returns an evaluation trace for
    the decision record (constraints that passed are auditable facts too)."""
    trace = []
    tid = manifest.get("template_id")
    for c in manifest.get("constraints") or []:
        cid = c.get("id", "?")
        try:
            applies = choices.safe_eval(c.get("when"), ctx)
        except Exception as e:
            raise SystemExit(f"error: {tid} constraint '{cid}' guard "
                             f"{c.get('when')!r} failed to evaluate: {e}")
        entry = {"id": cid, "when": c.get("when"), "applied": bool(applies)}
        if not applies:
            trace.append(entry)
            continue
        sub = c.get("require_substring")
        if sub is not None:
            val = next((ctx[s] for s in sub.get("slots", []) if ctx.get(s)), None)
            entry["require"] = (f"{sub.get('text')!r} in "
                                f"{'/'.join(sub.get('slots', []))}")
            if not (isinstance(val, str)
                    and str(sub.get("text", "")).lower() in val.lower()):
                raise SystemExit(f"error: {tid} constraint '{cid}': {c.get('error')}")
        elif c.get("require") is not None:
            entry["require"] = c["require"]
            try:
                ok = choices.safe_eval(c["require"], ctx)
            except Exception:
                ok = False   # an erroring requirement never passes silently
            if not ok:
                raise SystemExit(f"error: {tid} constraint '{cid}': {c.get('error')}")
        else:
            # bare rule: the guard itself is the failure condition
            raise SystemExit(f"error: {tid} constraint '{cid}': {c.get('error')}")
        trace.append(entry)
    return trace


def enforce_date_constraints(manifest: dict, ctx: dict) -> None:
    """Manifest-declared minimum interval between two date slots, e.g. the
    5 M.R.S. § 213(1-A) UTPA demand for relief must precede suit by >= 30
    days. Each entry: {"from", "until", "min_days", "when"?, "authority"?}.
    A violated interval hard-fails: a short deadline silently forfeits the
    statutory predicate (fees/damages), so fail loudly instead of rendering.
    When the constraint is active, both dates must parse — an unparseable
    date under an active statutory interval also fails (the attorney supplies
    a concrete date rather than prose the engine cannot check)."""
    for c in manifest.get("date_constraints") or []:
        when = c.get("when")
        if when is not None:
            try:
                if not choices.safe_eval(when, ctx):
                    continue
            except Exception:
                pass    # guard error fails safe to ENFORCED
        start, end = ctx.get(c["from"]), ctx.get(c["until"])
        if start is None or end is None:
            continue    # absent slots are the required_slots layer's job
        ds, de = parse_date(start), parse_date(end)
        authority = c.get("authority") or manifest.get("template_id")
        if ds is None or de is None:
            raise SystemExit(
                f"error: {authority} requires {c['until']} to be at least "
                f"{c['min_days']} days after {c['from']}, but "
                f"{c['from']}={start!r} / {c['until']}={end!r} could not be "
                f"parsed as dates — use a full date like 'August 3, 2026'")
        if (de - ds).days < int(c["min_days"]):
            raise SystemExit(
                f"error: {c['until']}={end!r} is only {(de - ds).days} days "
                f"after {c['from']}={start!r} — {authority} requires at "
                f"least {c['min_days']} days")


def _fact_ledger(ctx: dict, slots: dict, derived: dict, defaulted: set,
                 pinned: dict, meta: dict, template_id: str) -> dict:
    """Every context key with WHERE its value came from — the audit substrate.
    Sources: attorney determination / slots file / manifest pin / manifest
    default / derived rule. `_meta` in the slots file upgrades a slots_file
    entry to a richer provenance record (source, author, date, basis...)."""
    facts = {}
    for k in sorted(ctx):
        v = ctx[k]
        if k in slots:
            entry = {"value": v, "source": "slots_file"}
            entry.update(meta.get(k) or {})
            if k in pinned:
                entry["detail"] = (entry.get("detail", "") +
                                   f" matches manifest pin in {template_id}").strip()
        elif k in pinned:
            entry = {"value": v, "source": "manifest_pin",
                     "detail": f"pinned by {template_id}"}
        elif k in derived:
            entry = {"value": v, "source": "derived",
                     "detail": derived[k]}
        elif k in defaulted:
            entry = {"value": v, "source": "manifest_default",
                     "detail": f"context_defaults in {template_id}"}
        else:
            entry = {"value": v, "source": "unknown"}
        facts[k] = entry
    return facts


def assemble(manifest_path: Path, slots: dict, trail=True) -> str:
    doc, _ = assemble_record(manifest_path, slots, trail=trail)
    return doc


def assemble_record(manifest_path: Path, slots: dict, trail=True) -> tuple[str, dict]:
    """Render the document AND a structured decision record: every block
    decision (included or not) with the rule that fired, the facts it read,
    each fact's source, statutory authority, and the rendered text — so an
    attorney can validate WHY each component is present, not guess."""
    manifest = json.loads(manifest_path.read_text())
    blocks = render.load_blocks()
    env = render.make_env()
    template_id = manifest.get("template_id")

    slots = dict(slots)
    meta = slots.pop("_meta", None) or {}
    ctx = derive_flags(slots)

    # record HOW each derived flag was computed, keyed off the input it read
    derived = {}
    if "post_2019" in ctx and "post_2019" not in slots:
        src = next((k for k in ("execution_date", "signing_date", "date")
                    if slots.get(k)), None)
        derived["post_2019"] = (f"from {src}={slots.get(src)!r} vs the "
                                f"2019-07-01 Maine POA Act cutoff")
    if "pre_1978_housing" in ctx and "pre_1978_housing" not in slots:
        derived["pre_1978_housing"] = (
            f"from year_built={slots.get('year_built')!r} vs the federal "
            f"pre-1978 target-housing cutoff (42 U.S.C. § 4852d)")
    if "motion_rule_invokes_rule56" in ctx and "motion_rule_invokes_rule56" not in slots:
        derived["motion_rule_invokes_rule56"] = (
            f"from motion_rule={slots.get('motion_rule')!r} vs a Rule 56 / "
            f"summary-judgment mention (gates the M.R.Civ.P. 7(b)(1)(B)/56(h) "
            f"notice contradiction guard)")
    if "jurisdiction" not in slots:
        derived["jurisdiction"] = "engine default"

    # manifest-level context defaults fill in flags/values the slots omit
    # (slots always win), e.g. a dedicated warranty manifest baking covenant_type.
    defaulted = set()
    if manifest.get("context_defaults"):
        defaulted = {k for k in manifest["context_defaults"] if k not in ctx}
        ctx = {**manifest["context_defaults"], **ctx}
    enforce_discriminators(manifest, slots, ctx)
    enforce_date_constraints(manifest, ctx)
    constraint_trace = enforce_constraints(manifest, ctx)
    pinned = {s: spec["pin"] for s, spec in (manifest.get("discriminators") or {}).items()
              if spec.get("pin") is not None}

    resolved = choices.resolve(manifest, blocks, ctx)
    body_parts, trail_lines, warns, decisions = [], [], [], []
    missing_slots = set()
    num_counter = segments._Counter()   # document-level article auto-numbering

    rendered_title = None
    if manifest.get("title"):
        rendered_title = render.render_text(env, manifest["title"], ctx)
        body_parts.append(f"# {rendered_title}")

    for r in resolved:
        d = {"block_id": r.block_id, "kind": r.kind, "mode": r.mode,
             "included": r.included, "note": r.note}
        if r.when is not None:
            d["when"] = r.when
            d["when_result"] = r.when_result
            d["facts_considered"] = {n: ctx.get(n)
                                     for n in choices.expr_names(r.when)}
        if r.variant_trace:
            d["variant_select"] = r.variant_trace
        if not r.included:
            decisions.append(d)
            trail_lines.append(f"- {r.block_id} [omitted] — {r.note}")
            continue
        if r.text_source == "literal":
            rendered = render.render_text(env, r.literal, ctx)
            # chrome may carry a manifest numbering scheme — e.g. an auto-numbered
            # "ARTICLE I —" heading. Article structure is a document (manifest)
            # decision, not attorney clause language, so it lives here, not in a
            # block. The label is drawn from the same doc-level counter, so it
            # nests with the numbered sections that follow.
            cnum = getattr(r, "number", None)
            if isinstance(cnum, str):
                cnum = (manifest.get("numbering") or {}).get(cnum)
            if cnum:
                label, n = segments._label(cnum, num_counter)
                rendered = label + rendered
                d["structure"] = {"role": "heading", "level": 2, "number": cnum}
                d["number"] = n
            d["rendered"] = rendered
            decisions.append(d)
            body_parts.append(rendered)
            trail_lines.append(f"- {r.block_id} [chrome] — {r.note}")
            continue
        if r.text_source == "MISSING":
            decisions.append(d)
            body_parts.append(f"\n> {r.note}\n")
            trail_lines.append(f"- {r.block_id}: {r.note}")
            continue
        block = blocks[r.block_id]
        raw = render.block_text(block, r.text_source)
        if r.text_source != "dominant":
            warns += [f"{r.block_id}/{r.text_source}: variant reintroduces name '{w}'"
                      for w in render.variant_pii_warn(raw)]
        rendered = render.render_text(env, raw, ctx)
        # tabular blocks (caption/table) carry a grid; flatten to aligned text
        # for the Markdown body, keep the rows for structured consumers
        struct = block.get("structure") or {}
        # Numbering is a DOCUMENT property, not a clause property: a manifest
        # block-ref may carry "number" — an inline spec, or a name into
        # manifest["numbering"] — that overrides the block's own structure.number.
        # This lets ONE shared general-provisions clause (governing law,
        # severability, ...) be "12." in a flat agreement and "9.4" in a nested
        # one, without baking a fixed ordinal into the reusable block. The
        # effective spec flows into d["structure"] so segments carry the right
        # number/list_style metadata for downstream consumers.
        # The override is MERGED over the block's own number spec: the block
        # keeps `scope` (items vs block) and any defaults; the manifest supplies
        # the document scheme (list / level / style / compound). So a flat
        # contract gives {list, level:1, style:decimal} and a nested one
        # {list, level:2, style:decimal, compound:true} — same reusable block.
        ref_num = getattr(r, "number", None)
        if isinstance(ref_num, str):
            ref_num = (manifest.get("numbering") or {}).get(ref_num)
        if ref_num:
            struct = {**struct, "number": {**(struct.get("number") or {}), **ref_num}}
        grid = (segments.parse_grid(rendered)
                if struct.get("role") in ("caption", "table") else None)
        if grid is not None:
            rendered = segments.grid_to_text(grid)
        # auto-numbered article: prepend the engine-assigned ordinal (FIRST:,
        # 3.2, (a)) so add/remove renumbers — replaces hand-fed clause-number slots.
        # scope=items numbers EVERY paragraph in the block (pleading allegations
        # ¶1..N continue across blocks via the shared document counter).
        block_number = None
        if struct.get("number"):
            spec = struct["number"]
            if spec.get("scope") == "items":
                parts = [p.strip() for p in re.split(r"\n\s*\n", rendered.strip())
                         if p.strip()]
                rendered = "\n\n".join(segments._label(spec, num_counter)[0] + p
                                       for p in parts)
            else:
                label, block_number = segments._label(spec, num_counter)
                rendered = label + rendered
        # collect unfilled slots
        for s in block.get("slots", []):
            if s["name"] not in ctx:
                missing_slots.add(s["name"])
        d.update({"function": block.get("function"),
                  "structure": struct,
                  "text_source": r.text_source,
                  "statutory_citation": block.get("statutory_citation"),
                  "corpus_frequency": block.get("frequency"),
                  "slots": [{"name": s["name"], "type": s.get("type"),
                             "required": bool(s.get("required")),
                             "status": ("filled" if s["name"] in ctx else
                                        "marker" if f"[[ {s['name']} ]]" in rendered
                                        else "omitted")}
                            for s in block.get("slots", [])],
                  "rendered": rendered})
        if grid is not None:
            d["grid"] = grid
        if block_number is not None:
            d["number"] = block_number
        decisions.append(d)
        body_parts.append(rendered)
        label = f"{block.get('function')}/{block.get('subtype') or ''}".rstrip("/")
        trail_lines.append(f"- {r.block_id} [{label}] — {r.note}")

    doc = "\n\n".join(p for p in body_parts if p.strip())
    # exact: only slots that actually rendered as [[ ]] markers in the draft;
    # unset optional flags (seal, grantor_plural, ...) are listed separately so
    # the warning attorneys must act on stays trustworthy.
    marked = sorted(set(re.findall(r"\[\[ (\w+) \]\]", doc)))
    unset_flags = sorted(missing_slots - set(marked))

    record = {
        "_schema": "template-forge/decision-record",
        "_schema_version": 1,
        "template_id": template_id,
        "practice_area": manifest.get("practice_area"),
        "document_type": manifest.get("document_type"),
        "manifest": manifest_path.name,
        "title": rendered_title,
        "discriminators": {s: {"value": ctx.get(s), "enum": spec.get("enum"),
                               "pin": spec.get("pin"),
                               "required": bool(spec.get("required"))}
                           for s, spec in (manifest.get("discriminators") or {}).items()},
        "facts": _fact_ledger(ctx, slots, derived, defaulted, pinned, meta,
                              template_id),
        "decisions": decisions,
        "unfilled_markers": marked,
        "unset_optional_flags": unset_flags,
        "paired_forms": manifest.get("paired_forms") or [],
        "constraints": constraint_trace,
        # manifest-declared statutory grounding + hard statutory deadlines
        # (e.g. the 33 M.R.S. § 482(2) 20-day land-installment-contract
        # recording duty) — surfaced as audit-companion warnings so a deadline
        # the document itself cannot satisfy is never silently dropped.
        "statutory_basis": manifest.get("statutory_basis") or {},
        "deadline_notes": manifest.get("deadline_notes") or [],
        "warnings": warns,
    }
    # paired_requirements: evaluate each entry's applies_when guard against the
    # matter facts so the audit companion can surface the APPLICABLE companion
    # forms/filings/deadlines (e.g. the FM-050 child support affidavit that
    # M.R.Civ.P. 108(a)(1) requires WITH a complaint when child support is an
    # issue, or the 33 M.R.S. § 551 60-day discharge recording duty) as
    # checklist items. An unevaluable guard fails safe to applicable — better a
    # spurious checklist line than a silently missing mandatory attachment.
    preqs = []
    for req in manifest.get("paired_requirements") or []:
        entry = dict(req)
        guard = entry.get("applies_when")
        try:
            entry["applicable"] = bool(choices.safe_eval(guard, ctx)) if guard else True
        except Exception:
            entry["applicable"] = True
        preqs.append(entry)
    record["paired_requirements"] = preqs
    # typed structured/hierarchical view (rich-text + document-model target)
    record["segments"] = segments.segment_record(record, title=rendered_title)

    if not trail:
        return doc, record

    out = [doc, "\n\n---\n## Provenance trail\n",
           f"template: {manifest.get('template_id')}  "
           f"({manifest.get('practice_area')}/{manifest.get('document_type')})\n"]
    out += trail_lines
    if manifest.get("paired_forms"):
        out.append(f"\n**Paired official forms:** {', '.join(manifest['paired_forms'])}")
    if manifest.get("deadline_notes"):
        out.append("\n**⚠ Statutory deadlines (calendar these — the document "
                    "cannot satisfy them by itself):**")
        out += [f"  - {n}" for n in manifest["deadline_notes"]]
    if marked:
        out.append(f"\n**Unfilled slots ([[ marked ]] in draft):** {', '.join(marked)}")
    if unset_flags:
        out.append(f"\n**Unset optional flags (rendered without):** {', '.join(unset_flags)}")
    if warns:
        out.append("\n**⚠ Variant PII warnings:**")
        out += [f"  - {w}" for w in warns]
    return "\n".join(out), record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", help="template pack directory "
                    "(default: $TEMPLATE_FORGE_PACK or the bundled example_pack)")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--slots", help="JSON file of slot values + flags "
                    "(optional _meta key adds per-fact provenance)")
    ap.add_argument("--out", help="write draft here instead of stdout")
    ap.add_argument("--no-trail", action="store_true")
    ap.add_argument("--decision-record", metavar="PATH",
                    help="write the structured decision record (JSON) here")
    ap.add_argument("--audit", metavar="PATH",
                    help="write the attorney audit companion (Markdown) here")
    ap.add_argument("--segments", metavar="PATH",
                    help="write the typed structured-segment list (JSON) here")
    a = ap.parse_args()
    if a.pack:
        paths.set_pack(a.pack)

    slots = json.loads(Path(a.slots).read_text()) if a.slots else {}
    doc, record = assemble_record(Path(a.manifest), slots, trail=not a.no_trail)
    if a.decision_record:
        Path(a.decision_record).write_text(
            json.dumps(record, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {a.decision_record}", file=sys.stderr)
    if a.segments:
        Path(a.segments).write_text(
            json.dumps(record["segments"], indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {a.segments}", file=sys.stderr)
    if a.audit:
        import audit
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        Path(a.audit).write_text(audit.record_to_markdown(record, stamp=stamp))
        print(f"wrote {a.audit}", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(doc + "\n")
        print(f"wrote {a.out}")
    else:
        print(doc)


if __name__ == "__main__":
    main()
