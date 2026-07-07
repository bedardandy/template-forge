#!/usr/bin/env python3
"""Typed document-segment layer — the structured/hierarchical view of an
assembled instrument.

`assemble.py` produces a flat Markdown body (paragraphs joined by blank lines)
plus a decision record. This module turns that record into an ordered list of
typed *segments* — the representation that maps cleanly to rich text (DOCX
paragraph styles), HTML sectioning (`<h1>`/`<p>`/`<ol><li>`/`<section>`), or a
JSON document model — instead of leaving structure implicit in punctuation.

Each authored block declares a structural `role` (in its `structure` metadata);
the engine splits the block's rendered text into paragraphs and assigns each a
role. The split is DECLARATIVE: presentation (alignment, indent, list style) is
a consumer concern (see ROLE_PRESENTATION in export_docx.py), so blocks stay
semantic, not visual, and nothing is guessed from undeclared blocks. The single
deterministic refinement is numbered-item detection, and it fires ONLY inside a
block that declared `role: "numbered_list"` — so a body block is never
reinterpreted by accident.

A segment is a dict:
    {"role": <one of ROLES>, "text": str, "block_id": str|None,
     "level"?: int, "align"?: str, "list_style"?: str, "number"?: int}
"""
from __future__ import annotations

import re

ROLES = {
    "title", "heading", "body", "recital", "numbered_item", "numbered_list",
    "signature_block", "notary_block", "divider", "chrome", "caption", "table",
}

_NUM = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+")   # "1." / "1)" / compound "1.1 "

# grid mini-syntax for caption/table blocks: rows split on @@ROW@@, cells on
# @@COL@@. Cells may be multi-line. Keeps tabular structure out of the flat
# Markdown body while exposing real rows/cells to structured consumers.
GRID_ROW = "@@ROW@@"
GRID_COL = "@@COL@@"


def parse_grid(text: str) -> list[list[str]]:
    rows = []
    for rowtext in (text or "").split(GRID_ROW):
        cells = [c.strip() for c in rowtext.split(GRID_COL)]
        if any(cells):
            rows.append(cells)
    return rows


def grid_to_text(rows: list[list[str]], gap: int = 6) -> str:
    """Render a grid as aligned monospace columns — the clean flat-text view for
    the Markdown body (the DOCX/HTML consumers render a real table from rows)."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], max((len(ln) for ln in c.split("\n")), default=0))
    out = []
    for r in rows:
        cells = [c.split("\n") for c in r] + [[""]] * (ncols - len(r))
        for li in range(max(len(c) for c in cells)):
            parts = []
            for j in range(ncols):
                seg = cells[j][li] if li < len(cells[j]) else ""
                parts.append(seg.ljust(widths[j] + gap) if j < ncols - 1 else seg)
            out.append("".join(parts).rstrip())
    return "\n".join(out)


def _default_role(decision: dict) -> str:
    return "chrome" if decision.get("kind") == "chrome" else "body"


# ---- auto-numbering -------------------------------------------------------
# A block declares structure.number = {list, level, style, compound?, prefix?,
# suffix?}. The engine keeps a counter per (list, level) across the whole
# document and prepends the computed ordinal to the block's first paragraph, so
# adding/removing a block renumbers the rest — no hand-fed clause-number slots.
_WORDS = ["", "FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH",
          "EIGHTH", "NINTH", "TENTH", "ELEVENTH", "TWELFTH", "THIRTEENTH",
          "FOURTEENTH", "FIFTEENTH", "SIXTEENTH", "SEVENTEENTH", "EIGHTEENTH",
          "NINETEENTH", "TWENTIETH"]
_DEFAULT_AFFIX = {
    "upper_word": ("", ": "), "decimal": ("", ". "), "upper_alpha": ("", ". "),
    "upper_roman": ("", ". "), "lower_alpha": ("(", ") "), "lower_roman": ("(", ") "),
}


def _roman(n: int) -> str:
    out, vals = "", [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
                     (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
                     (5, "V"), (4, "IV"), (1, "I")]
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _ordinal(style: str, n: int) -> str:
    if style == "upper_word":
        return _WORDS[n] if n < len(_WORDS) else str(n)
    if style == "decimal":
        return str(n)
    if style == "lower_alpha":
        return chr(96 + n)
    if style == "upper_alpha":
        return chr(64 + n)
    if style == "lower_roman":
        return _roman(n).lower()
    if style == "upper_roman":
        return _roman(n)
    return str(n)


class _Counter:
    def __init__(self):
        self.c: dict = {}

    def bump(self, lst: str, level: int) -> int:
        d = self.c.setdefault(lst, {})
        d[level] = d.get(level, 0) + 1
        for L in list(d):           # a shallower level resets deeper ones
            if L > level:
                d[L] = 0
        return d[level]

    def chain(self, lst: str, level: int) -> list[int]:
        d = self.c.get(lst, {})
        return [d.get(L, 0) for L in range(1, level + 1)]


def _label(num: dict, counter: _Counter) -> str:
    style, level, lst = num["style"], num.get("level", 1), num["list"]
    n = counter.bump(lst, level)
    dpre, dsuf = _DEFAULT_AFFIX.get(style, ("", ". "))
    pre = dpre if num.get("prefix") is None else num["prefix"]
    suf = dsuf if num.get("suffix") is None else num["suffix"]
    if num.get("compound") and style == "decimal":
        body = ".".join(str(x) for x in counter.chain(lst, level))
    else:
        body = _ordinal(style, n)
    return f"{pre}{body}{suf}", n


def _split_paragraphs(text: str) -> list[str]:
    """Block-internal paragraphs are separated by a blank line (the same
    convention assemble.py uses to join blocks)."""
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def segment_record(record: dict, title: str | None = None) -> list[dict]:
    """Ordered typed segments for a decision record. `title` defaults to the
    record's rendered title."""
    segs: list[dict] = []

    t = title if title is not None else record.get("title")
    if t:
        segs.append({"role": "title", "level": 1, "text": t, "block_id": None})

    for d in record.get("decisions", []):
        if not d.get("included"):
            continue
        struct = d.get("structure") or {}
        base = struct.get("role") or _default_role(d)
        if base not in ROLES:
            base = "body"
        # tabular block (caption / table): one structured segment with rows
        if d.get("grid") is not None:
            segs.append({"role": base if base in ("caption", "table") else "table",
                         "rows": d["grid"], "block_id": d.get("block_id"),
                         "text": d.get("rendered", "")})
            continue
        align = struct.get("align")
        level = struct.get("level")
        list_style = (struct.get("list") or {}).get("style") or "decimal"
        # the auto-number is already prepended to the rendered text by assemble
        # (so the flat body and segments agree); here we only carry the metadata
        num = struct.get("number")

        first = True
        for para in _split_paragraphs(d.get("rendered", "")):
            seg: dict = {"role": base, "block_id": d.get("block_id")}
            if base == "numbered_list":
                m = _NUM.match(para)
                if m:
                    seg["role"] = "numbered_item"
                    tok = m.group(1)        # int for flat "1", str for compound "1.1"
                    seg["number"] = int(tok) if "." not in tok else tok
                    seg["list_style"] = list_style
                else:
                    # lead-in / closing prose inside a numbered_list block
                    seg["role"] = "body"
            if num and first and d.get("number") is not None:
                seg["number"] = d["number"]
                seg["list_style"] = num["style"]
                first = False
            if align:
                seg["align"] = align
            if level is not None and seg["role"] in ("title", "heading"):
                seg["level"] = level
            seg["text"] = para
            segs.append(seg)

    return segs


def outline(segments: list[dict]) -> str:
    """A compact role/skeleton view — handy for eyeballing structure and for
    test goldens."""
    lines = []
    for s in segments:
        tag = s["role"]
        if s.get("number") is not None:
            tag += f"[{s['number']}]"
        preview = s["text"].replace("\n", " ⏎ ")
        preview = preview if len(preview) <= 60 else preview[:59] + "…"
        lines.append(f"{tag}: {preview}")
    return "\n".join(lines)
