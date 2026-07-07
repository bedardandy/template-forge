#!/usr/bin/env python3
"""Block-choice resolution: add / remove / substitute.

A *manifest* lists ordered block references, each with a `mode` and optional
`when` / `variant_select` conditions. This module evaluates those conditions
against a *fact-pattern context* (slot values + derived flags) and returns the
concrete, ordered list of blocks to render, with a provenance note per decision.

Conditions are short boolean expressions over context keys, evaluated by a
restricted AST walker (`safe_eval`) — no function calls, attribute access, or
arbitrary code. Supported: names, literals, and/or/not, comparisons, `in`.
Examples:  "post_2019"   "jurisdiction == 'ME'"   "has_minor_children and not agreed"
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub}
_CMP = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


def safe_eval(expr: str, ctx: dict) -> bool:
    """Evaluate a restricted boolean expression against ctx. Unknown names
    resolve to None (so `when: "some_flag"` is False unless set truthy)."""
    if expr is None or expr == "":
        return True
    if isinstance(expr, bool):
        return expr
    if isinstance(expr, (int, float)):
        return bool(expr)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not ev(node.operand)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                fn = _CMP.get(type(op))
                if fn is None:
                    raise ValueError(f"unsupported comparison operator: {type(op).__name__}")
                if not fn(left, ev(comp)):
                    return False
                left = ev(comp)
            return True
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Name):
            return ctx.get(node.id)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [ev(e) for e in node.elts]
        raise ValueError(f"disallowed expression element: {ast.dump(node)}")

    return bool(ev(ast.parse(expr, mode="eval")))


def expr_names(expr) -> list[str]:
    """The context keys a `when` expression reads — so an audit can show the
    attorney exactly which facts each inclusion decision turned on."""
    if not isinstance(expr, str) or not expr:
        return []
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return []
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})


@dataclass
class Resolved:
    block_id: str
    mode: str
    text_source: str          # "dominant" | variant_id | "literal"
    note: str                 # provenance line
    literal: str = ""         # body for chrome (literal_md) blocks
    included: bool = True     # exclusions are recorded, not dropped
    kind: str = "block"       # "block" | "chrome" | "gap" | "error"
    when: object = None
    when_result: object = None
    variant_trace: list = field(default_factory=list)
    number: object = None     # per-placement numbering override (spec or scheme name)


def resolve(manifest: dict, blocks: dict, ctx: dict) -> list[Resolved]:
    """Return the ordered, resolved block list for a manifest given ctx."""
    out: list[Resolved] = []

    def _eval(expr, where: str):
        """safe_eval that degrades to a visible error instead of killing the
        document (e.g. `amount > 100000` with the key unset raises TypeError)."""
        try:
            return safe_eval(expr, ctx), None
        except Exception as e:
            return None, f"[ERROR] {where} expression {expr!r} failed: {e}"

    for ref in manifest.get("blocks", []):
        # Literal chrome block: repo-authored structural scaffolding (title,
        # signature lines, section dividers) — NOT attorney clause language.
        if "literal_md" in ref:
            cid = ref.get("block_id", "chrome")
            mode = ref.get("mode", "required")
            when = ref.get("when")
            ok, err = _eval(when, "when")
            if err:
                out.append(Resolved(cid, mode, "MISSING", err,
                                    kind="error", when=when))
                continue
            if mode == "add" and not ok:
                out.append(Resolved(cid, mode, "literal",
                                    f"omitted (when: {when} → {ok!r})",
                                    included=False, kind="chrome",
                                    when=when, when_result=ok))
                continue
            if mode == "remove" and ok:
                out.append(Resolved(cid, mode, "literal",
                                    f"removed (when: {when})",
                                    included=False, kind="chrome",
                                    when=when, when_result=ok))
                continue
            r = Resolved(cid, "chrome", "literal", "structural chrome",
                         kind="chrome", when=when, when_result=ok,
                         number=ref.get("number"))
            r.literal = ref["literal_md"]  # type: ignore[attr-defined]
            out.append(r)
            continue
        bid = ref["block_id"]
        mode = ref.get("mode", "required")
        block = blocks.get(bid)
        if block is None:
            out.append(Resolved(bid, mode, "MISSING",
                                 f"[GAP] block {bid} not in library",
                                 kind="gap"))
            continue
        when = ref.get("when")
        ok, err = _eval(when, "when")
        if err:
            out.append(Resolved(bid, mode, "MISSING", err,
                                kind="error", when=when))
            continue

        if mode == "add":
            if not ok:
                out.append(Resolved(bid, mode, "dominant",
                                    f"omitted (when: {when} → {ok!r})",
                                    included=False, when=when, when_result=ok))
                continue
            note = f"added (when: {when})"
        elif mode == "remove":
            if ok:
                out.append(Resolved(bid, mode, "dominant",
                                    f"removed (when: {when})",
                                    included=False, when=when, when_result=ok))
                continue
            note = "kept (default-present)"
        elif mode == "required":
            note = "required"
        elif mode == "substitute":
            note = "substitute-eligible"
        else:
            note = mode

        text_source = "dominant"
        trace = []
        if mode in ("substitute", "required", "add", "remove"):
            for rule in ref.get("variant_select", []) or []:
                vwhen = rule.get("when")
                vok, verr = _eval(vwhen, "variant_select")
                if verr:
                    trace.append({"variant": rule.get("variant"), "when": vwhen,
                                  "error": verr})
                    note += f"; {verr} — kept dominant text"
                    break
                entry = {"variant": rule.get("variant"), "when": vwhen,
                         "result": vok, "applied": False}
                if vok:
                    vid = rule["variant"]
                    if any(v["variant_id"] == vid for v in block.get("variants", [])):
                        text_source = vid
                        trig = next(v["trigger"] for v in block["variants"]
                                    if v["variant_id"] == vid)
                        entry["applied"] = True
                        entry["trigger"] = trig
                        note += f"; substituted {vid} (trigger: {trig[:80]})"
                    trace.append(entry)
                    break
                trace.append(entry)

        out.append(Resolved(bid, mode, text_source, note,
                            when=when, when_result=ok, variant_trace=trace,
                            number=ref.get("number")))
    return out
