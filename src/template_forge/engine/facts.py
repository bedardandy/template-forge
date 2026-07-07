#!/usr/bin/env python3
"""Shared fact registry: one typed, provenance-carrying record of the matter's
facts, consumed by every artifact in the transaction.

A *matter file* records each fact once — value + source (who determined it,
when, on what basis). This module validates it against the typed key registry
(facts/fact_keys.json) and projects it into:

  - template slots (+ `_meta` provenance, so the decision record and
    audit companion show WHO asserted each fact) for any manifest, and
  - external form fact-dicts (e.g. ME-RETTD's transferee.* keys in the
    transactional-tax-forms repo) — the deed, the transfer tax declaration,
    and the 1099-S all read the SAME post-closing grantee address.

    python3 engine/facts.py --matter fixtures/matters/closing_doe_sale.json \
        --slots-out /tmp/slots.json [--template real_estate__deed]
    python3 engine/facts.py --matter M.json --form ME-RETTD --out facts.json
    python3 engine/facts.py --matter M.json --check

Sources vocabulary (extends the decision-record one): attorney_determination,
client_intake, document_extraction (cite the doc; pair with spans when the
extractor provides them), derived. A fact with no source is an error — the
registry exists so nothing reaches a recorded instrument unattributed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as _assemble  # noqa: E402  (parse_date)
import paths as _paths  # noqa: E402

FACT_SOURCES = {"attorney_determination", "client_intake",
                "document_extraction", "derived"}

_BOOK_PAGE = re.compile(r"^\d{1,6}[A-Z]?$")


def load_registry() -> dict:
    return json.loads(_paths.fact_keys_path().read_text())["keys"]


def load_matter(path: Path) -> dict:
    m = json.loads(Path(path).read_text())
    if "facts" not in m:
        raise SystemExit(f"error: {path} has no 'facts' object")
    return m


def _err(errors: list, key: str, msg: str):
    errors.append(f"{key}: {msg}")


def validate(matter: dict, registry: dict | None = None) -> list[str]:
    """Return a list of validation errors (empty = clean)."""
    registry = registry or load_registry()
    errors: list[str] = []
    for key, fact in matter["facts"].items():
        spec = registry.get(key)
        if spec is None:
            _err(errors, key, "unknown fact key (add it to facts/fact_keys.json "
                 "rather than free-forming — typed keys are the contract)")
            continue
        if not isinstance(fact, dict) or "value" not in fact:
            _err(errors, key, "fact must be {value, source, ...}")
            continue
        src = fact.get("source")
        if src not in FACT_SOURCES:
            _err(errors, key, f"source {src!r} not in {sorted(FACT_SOURCES)}")
        if src == "document_extraction" and not fact.get("document"):
            _err(errors, key, "document_extraction requires a 'document' reference")
        v = fact["value"]
        t = spec["type"]
        if t == "recording_ref":
            if not isinstance(v, dict):
                _err(errors, key, "recording_ref must be an object")
            else:
                for req in ("registry", "book", "page"):
                    if not v.get(req):
                        _err(errors, key, f"recording_ref missing '{req}'")
                for bp in ("book", "page"):
                    if v.get(bp) and not _BOOK_PAGE.match(str(v[bp])):
                        _err(errors, key, f"{bp}={v[bp]!r} is not a registry "
                             f"book/page number")
                if v.get("date") and _assemble.parse_date(v["date"]) is None:
                    _err(errors, key, f"unparseable date {v['date']!r}")
        elif t == "address":
            if isinstance(v, dict):
                for req in ("street", "city", "state"):
                    if not v.get(req):
                        _err(errors, key, f"address missing '{req}'")
            elif not (isinstance(v, str) and v.strip()):
                _err(errors, key, "address must be an object "
                     "{street, city, state, zip} or a non-empty string")
        elif t == "date":
            if _assemble.parse_date(v) is None:
                _err(errors, key, f"unparseable date {v!r}")
        elif t == "enum":
            if v not in spec.get("enum", []):
                _err(errors, key, f"{v!r} not in {spec.get('enum')}")
        elif t == "flag":
            if not isinstance(v, bool):
                _err(errors, key, "flag must be true/false")
        elif t == "list":
            if not isinstance(v, list):
                _err(errors, key, "must be a list")
    return errors


def _join_address(v) -> str:
    if isinstance(v, str):
        return v
    parts = [v.get("street"), v.get("city"),
             " ".join(x for x in (v.get("state"), v.get("zip")) if x)]
    return ", ".join(p for p in parts if p)


def _provenance(fact: dict) -> dict:
    return {k: fact[k] for k in ("source", "author", "date", "basis", "document")
            if fact.get(k)}


def to_slots(matter: dict, registry: dict | None = None) -> dict:
    """Project the matter into a slots dict with _meta provenance.
    Slot names are engine-global, so the result feeds any manifest; assemble's
    discriminator enforcement still guards family selection."""
    registry = registry or load_registry()
    errors = validate(matter, registry)
    if errors:
        raise SystemExit("error: matter fails validation:\n  - "
                         + "\n  - ".join(errors))
    slots: dict = {}
    meta: dict = {}

    def set_slot(name, value, fact):
        slots[name] = value
        meta[name] = _provenance(fact)

    for key, fact in matter["facts"].items():
        spec = registry[key]
        v = fact["value"]
        if spec["type"] == "recording_ref":
            for field, slot in spec["slots"].items():
                if v.get(field):
                    set_slot(slot, v[field], fact)
        elif spec["type"] == "address":
            set_slot(spec["slot"], _join_address(v), fact)
        else:
            set_slot(spec["slot"], v, fact)
        flag = spec.get("implies_flag")
        if flag and v:
            set_slot(flag, True, dict(fact, basis=f"implied by {key}"))
    slots["_meta"] = meta
    return slots


def to_form_facts(matter: dict, form_id: str,
                  registry: dict | None = None) -> dict:
    """Project the matter into an external form's fact keys (e.g. ME-RETTD's
    transferee.* / transferor.* / property.*)."""
    registry = registry or load_registry()
    out: dict = {}
    for key, fact in matter["facts"].items():
        spec = registry.get(key) or {}
        target = (spec.get("forms") or {}).get(form_id)
        if target is None:
            continue
        v = fact["value"]
        if isinstance(target, dict):           # component split (addresses)
            comp = v if isinstance(v, dict) else {"street": v}
            for field, form_key in target.items():
                if comp.get(field):
                    out[form_key] = comp[field]
        else:
            out[target] = _join_address(v) if spec.get("type") == "address" else v
    return out


def missing_required(matter: dict, template_id: str,
                     registry: dict | None = None) -> list[str]:
    """Which of a manifest's required_slots the matter does not satisfy —
    the intake question list, derived rather than guessed."""
    manifest = json.loads((_paths.manifests_dir() / f"{template_id}.json").read_text())
    slots = to_slots(matter, registry)
    ctx = dict(manifest.get("context_defaults") or {})
    ctx.update({k: v for k, v in slots.items() if k != "_meta"})
    for s, spec in (manifest.get("discriminators") or {}).items():
        if spec.get("pin") is not None:
            ctx[s] = spec["pin"]
    ctx = _assemble.derive_flags(ctx)
    return [s for s in _assemble.required_slots_for(manifest, ctx) if s not in ctx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matter", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--template", help="warn about unsatisfied required_slots")
    ap.add_argument("--slots-out", help="write slots (+_meta) JSON here")
    ap.add_argument("--form", help="external form id (e.g. ME-RETTD)")
    ap.add_argument("--out", help="output path for --form")
    a = ap.parse_args()

    matter = load_matter(Path(a.matter))
    errors = validate(matter)
    if errors:
        print("INVALID:\n  - " + "\n  - ".join(errors))
        raise SystemExit(1)
    print(f"matter {matter.get('matter_id', '?')}: "
          f"{len(matter['facts'])} facts valid")
    if a.template:
        miss = missing_required(matter, a.template)
        if miss:
            print(f"required slots not yet satisfied for {a.template} "
                  f"(intake questions): {', '.join(miss)}")
        else:
            print(f"all required slots satisfied for {a.template}")
    if a.slots_out:
        Path(a.slots_out).write_text(
            json.dumps(to_slots(matter), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {a.slots_out}")
    if a.form:
        facts = to_form_facts(matter, a.form)
        text = json.dumps(facts, indent=2, ensure_ascii=False) + "\n"
        if a.out:
            Path(a.out).write_text(text)
            print(f"wrote {a.out} ({len(facts)} {a.form} keys)")
        else:
            print(text)


if __name__ == "__main__":
    main()
