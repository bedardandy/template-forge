#!/usr/bin/env python3
"""Jinja2 rendering layer + block library loader.

Loads blocks.jsonl, resolves a block's text (dominant partial OR a selected
variant), and renders it through a Jinja2 environment whose Undefined renders
unfilled slots as a visible `[[ slot ]]` marker (so the attorney sees exactly
what still needs a value, rather than getting a silent blank).
"""
from __future__ import annotations

import json
import re

import paths as _paths
from jinja2 import BaseLoader, Environment, Undefined

_NAME_RUN = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:[A-Z]\.|[A-Z][a-z]+)){1,3}\b")
_SAFE_CAPS = re.compile(r"State|County|Court|Maine|Will|Power|Personal|Free|Now|"
                        r"Know|Witness|Health|Real|Notary|United|District|Superior")


class SlotUndefined(Undefined):
    """Render an unfilled {{ slot }} as a visible [[ slot ]] fill-marker."""
    def __str__(self):
        return f"[[ {self._undefined_name} ]]"
    __repr__ = __str__


def load_blocks() -> dict:
    out: dict = {}
    for b in (json.loads(l) for l in _paths.blocks_jsonl().read_text().splitlines() if l.strip()):
        if b["block_id"] in out:
            raise SystemExit(
                f"error: duplicate block_id {b['block_id']!r} in blocks.jsonl — "
                f"a duplicate silently shadows clause text; re-run tools/derive_blocks.py")
        out[b["block_id"]] = b
    return out


def make_env() -> Environment:
    return Environment(loader=BaseLoader(), undefined=SlotUndefined,
                       trim_blocks=False, lstrip_blocks=False,
                       keep_trailing_newline=False)


def block_text(block: dict, text_source: str) -> str:
    """Return the raw template text for a block given the chosen source.

    A selected variant's body may be supplied inline (``text``) or as a partial
    file (``partials/<variant_id>.md.j2``). If a structure-only pack provides
    neither, fall back to the block's dominant body so assembly never crashes on
    a missing variant body (the decision trail still records the selection)."""
    dominant = (_paths.partials_dir() / f"{block['block_id']}.md.j2").read_text().rstrip("\n")
    if text_source == "dominant":
        return dominant
    for v in block.get("variants", []):
        if v["variant_id"] == text_source:
            if v.get("text"):
                return v["text"]
            vpart = _paths.partials_dir() / f"{text_source}.md.j2"
            if vpart.exists():
                return vpart.read_text().rstrip("\n")
            break
    return dominant


def render_text(env: Environment, text: str, ctx: dict) -> str:
    return env.from_string(text).render(**ctx)


def variant_pii_warn(text: str) -> list[str]:
    """Flag residual proper-name PII a substituted variant may reintroduce."""
    out = []
    for m in _NAME_RUN.finditer(text):
        run = m.group(0)
        if not _SAFE_CAPS.search(run):
            out.append(run)
    return out
