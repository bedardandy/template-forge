#!/usr/bin/env python3
"""Clause-mining pipeline — clean-room implementation.

Turns a corpus of *your own* legal documents into a candidate block library with
the same on-disk shape the assembly engine consumes (``blocks.jsonl`` +
``partials/*.md.j2``). The stages are:

    1. segment       docx/pdf/txt -> candidate clause segments
    2. dedup         MinHash + LSH near-duplicate collapse into clause groups
    3. label         functional label per clause group (LLM hook; heuristic
                     fallback when no model is configured)
    4. variant_mine  within a group, surface the actionable text deviations
    5. typo_clean    drop stylistic/typo-only deviations; canonicalize typography

This module is **firm-agnostic and configuration-driven**: it takes an input
directory and an output pack directory, and depends on no firm paths, hostnames,
or corpora. Person-name / quasi-identifier handling for the *bodies* is the job
of :mod:`template_forge.generator.deidentify`; this pipeline works on already
de-identified or synthetic input and emits STRUCTURE plus parametrized bodies.

The output ``blocks.jsonl`` records mirror the engine's schema
(see ``data/schema/block.schema.json``). No attorney clause language is invented:
a block body is your document's own text with quasi-identifiers blanked to slots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Stage 1 — segmentation
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.:;])\s+(?=[A-Z0-9])")


def read_document_text(path: Path) -> str:
    """Extract plain text from a supported document.

    Supports ``.txt``/``.md`` natively; ``.docx`` when ``python-docx`` is
    installed; ``.pdf`` when ``pypdf`` is installed. Unsupported/absent
    dependencies raise a clear error rather than guessing.
    """
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return path.read_text(errors="ignore")
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as e:  # pragma: no cover - optional dep
            raise RuntimeError("python-docx required to read .docx") from e
        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:  # pragma: no cover - optional dep
            raise RuntimeError("pypdf required to read .pdf") from e
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
    raise ValueError(f"unsupported document type: {path.suffix}")


def segment_clauses(text: str, min_words: int = 6) -> list[str]:
    """Split a document into candidate clause segments.

    Paragraph-first, then sentence-grouped for long paragraphs. Segments shorter
    than ``min_words`` are dropped as noise (headings, page numbers).
    """
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para.split()) <= 60:
            candidates = [para]
        else:
            candidates = _SENT_SPLIT.split(para)
        for c in candidates:
            c = re.sub(r"\s+", " ", c).strip()
            if len(c.split()) >= min_words:
                out.append(c)
    return out


# ---------------------------------------------------------------------------
# Stage 2 — MinHash + LSH near-duplicate dedup
# ---------------------------------------------------------------------------


def _shingles(text: str, k: int = 5) -> set[str]:
    """Word-level k-shingles, lowercased and whitespace-normalized."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _minhash(shingles: set[str], num_perm: int = 64) -> tuple[int, ...]:
    """A pure-stdlib MinHash signature: for each of ``num_perm`` salted hashes,
    keep the minimum over the shingle set. Deterministic; no external deps."""
    if not shingles:
        return tuple(0 for _ in range(num_perm))
    sig = []
    for seed in range(num_perm):
        mn = min(
            int.from_bytes(
                hashlib.blake2b(sh.encode(), digest_size=8,
                                salt=seed.to_bytes(2, "little")).digest(),
                "little",
            )
            for sh in shingles
        )
        sig.append(mn)
    return tuple(sig)


def _lsh_bands(sig: tuple[int, ...], bands: int = 16) -> list[tuple[int, int]]:
    """Split a MinHash signature into bands; two segments that share any
    (band_index, band_hash) bucket are LSH candidate near-duplicates."""
    rows = max(1, len(sig) // bands)
    out = []
    for b in range(bands):
        chunk = sig[b * rows:(b + 1) * rows]
        h = hashlib.blake2b(repr(chunk).encode(), digest_size=8).hexdigest()
        out.append((b, int(h, 16)))
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass
class ClauseGroup:
    """A cluster of near-duplicate clause segments (one canonical + variants)."""

    group_id: str
    members: list[str] = field(default_factory=list)
    dominant: str = ""
    function: str = "unlabeled"
    variants: list[dict] = field(default_factory=list)


def dedup_clauses(segments: Iterable[str], threshold: float = 0.7,
                  num_perm: int = 64, bands: int = 16) -> list[ClauseGroup]:
    """MinHash/LSH near-duplicate collapse.

    Segments are bucketed by LSH band; within colliding buckets, pairs above the
    Jaccard ``threshold`` are unioned into a group. The most frequent surface
    form becomes the group's ``dominant`` text.
    """
    segs = list(segments)
    shingle_sets = [_shingles(s) for s in segs]
    sigs = [_minhash(sh, num_perm) for sh in shingle_sets]

    # LSH candidate buckets
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, sig in enumerate(sigs):
        for key in _lsh_bands(sig, bands):
            buckets.setdefault(key, []).append(i)

    # union-find over verified near-duplicates
    parent = list(range(len(segs)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for members in buckets.values():
        if len(members) < 2:
            continue
        for idx_a in range(len(members)):
            for idx_b in range(idx_a + 1, len(members)):
                i, j = members[idx_a], members[idx_b]
                if find(i) == find(j):
                    continue
                if _jaccard(shingle_sets[i], shingle_sets[j]) >= threshold:
                    union(i, j)

    grouped: dict[int, list[int]] = {}
    for i in range(len(segs)):
        grouped.setdefault(find(i), []).append(i)

    groups: list[ClauseGroup] = []
    for n, (_, idxs) in enumerate(sorted(grouped.items())):
        member_texts = [segs[i] for i in idxs]
        counts: dict[str, int] = {}
        for t in member_texts:
            counts[t] = counts.get(t, 0) + 1
        dominant = max(counts, key=counts.get)
        gid = "b" + hashlib.blake2b(dominant.encode(), digest_size=4).hexdigest()
        groups.append(ClauseGroup(group_id=gid, members=member_texts,
                                  dominant=dominant))
    return groups


# ---------------------------------------------------------------------------
# Stage 3 — functional labeling
# ---------------------------------------------------------------------------

# Heuristic fallback labels (used when no LLM labeler is configured). These are
# generic English cues, not firm taxonomy — the LLM labeler is preferred.
_HEURISTIC_LABELS = [
    (re.compile(r"\bgovern|construed in accordance\b", re.I), "choice_of_law"),
    (re.compile(r"\bgrant|convey|transfer\b", re.I), "granting_clause"),
    (re.compile(r"\bnotice\b", re.I), "notice"),
    (re.compile(r"\bwitness whereof|executed\b", re.I), "signature_block"),
    (re.compile(r"\bnotary|acknowledged|commission\b", re.I), "notary_block"),
    (re.compile(r"\bsincerely|very truly yours|do not hesitate\b", re.I),
     "closing_correspondence"),
    (re.compile(r"\bindemnif", re.I), "indemnification"),
    (re.compile(r"\bconfidential", re.I), "confidentiality"),
]

# A functional labeler: (clause_text) -> function label. Plug an LLM call here.
Labeler = Callable[[str], str]


def heuristic_labeler(text: str) -> str:
    for pat, label in _HEURISTIC_LABELS:
        if pat.search(text):
            return label
    return "unlabeled"


def label_groups(groups: list[ClauseGroup],
                 labeler: Optional[Labeler] = None) -> list[ClauseGroup]:
    """Assign a functional label to each group. ``labeler`` may be an LLM-backed
    callable; defaults to a transparent keyword heuristic so the pipeline runs
    with zero external services."""
    fn = labeler or heuristic_labeler
    for g in groups:
        g.function = fn(g.dominant)
    return groups


# ---------------------------------------------------------------------------
# Stage 4 — variant mining
# ---------------------------------------------------------------------------

_ACTIONABLE = {
    "fact_pattern", "party_role", "jurisdiction",
    "citation_update", "scope_expansion", "scope_restriction",
}


def _classify_deviation(dominant: str, variant: str) -> str:
    """Coarse category for how a variant deviates from the dominant text.

    Heuristic and intentionally conservative — a downstream LLM pass can refine.
    Returns one of the actionable categories, or ``stylistic``/``typo``.
    """
    d_words = re.findall(r"[a-z0-9]+", dominant.lower())
    v_words = re.findall(r"[a-z0-9]+", variant.lower())
    if d_words == v_words:
        return "typo"  # differs only in punctuation/case/whitespace
    added = set(v_words) - set(d_words)
    removed = set(d_words) - set(v_words)
    juris_cues = {"maine", "hampshire", "massachusetts", "state"}
    if added & juris_cues or removed & juris_cues:
        return "jurisdiction"
    cite_pat = r"\b\d+[- ]?[A-Z]\b"
    if bool(re.search(cite_pat, variant)) != bool(re.search(cite_pat, dominant)):
        return "citation_update"
    if len(v_words) > len(d_words) * 1.15:
        return "scope_expansion"
    if len(v_words) < len(d_words) * 0.85:
        return "scope_restriction"
    if added or removed:
        return "fact_pattern"
    return "stylistic"


def mine_variants(groups: list[ClauseGroup]) -> list[ClauseGroup]:
    """For each group, record the distinct non-dominant surface forms as variants
    with a deviation category and a stable variant id."""
    for g in groups:
        seen: set[str] = {g.dominant}
        n = 0
        for m in g.members:
            if m in seen:
                continue
            seen.add(m)
            n += 1
            g.variants.append({
                "variant_id": f"{g.group_id}_v{n}",
                "signal_category": _classify_deviation(g.dominant, m),
                "trigger": "",  # left for the labeler / attorney to fill
                "text": m,
                "confidence": "medium",
            })
    return groups


# ---------------------------------------------------------------------------
# Stage 5 — typo / stylistic cleaning
# ---------------------------------------------------------------------------

_TYPO_CANON = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "–": "-", "—": "-",
}


def _canon_typography(s: str) -> str:
    for a, b in _TYPO_CANON.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def typo_clean(groups: list[ClauseGroup]) -> list[ClauseGroup]:
    """Drop stylistic/typo-only variants and canonicalize typography on the text
    that survives — mirroring the engine's actionable-category filter."""
    for g in groups:
        g.dominant = _canon_typography(g.dominant)
        kept = []
        for v in g.variants:
            if v["signal_category"] not in _ACTIONABLE:
                continue
            v["text"] = _canon_typography(v["text"])
            if v["text"] == g.dominant:
                continue
            kept.append(v)
        g.variants = kept
    return groups


# ---------------------------------------------------------------------------
# Orchestration + pack emission
# ---------------------------------------------------------------------------


def run_pipeline(input_dir: Path, labeler: Optional[Labeler] = None,
                 dedup_threshold: float = 0.7) -> list[ClauseGroup]:
    """Run all stages over every supported document under ``input_dir``."""
    segments: list[str] = []
    for path in sorted(input_dir.rglob("*")):
        if path.suffix.lower() not in (".txt", ".md", ".docx", ".pdf"):
            continue
        try:
            segments.extend(segment_clauses(read_document_text(path)))
        except Exception:  # skip unreadable inputs, keep going
            continue
    groups = dedup_clauses(segments, threshold=dedup_threshold)
    groups = label_groups(groups, labeler=labeler)
    groups = mine_variants(groups)
    groups = typo_clean(groups)
    return groups


def write_pack(groups: list[ClauseGroup], pack_dir: Path,
               include_bodies: bool = True) -> None:
    """Emit a pack: ``blocks/blocks.jsonl`` + ``blocks/partials/*.md.j2``.

    With ``include_bodies=False`` the dominant/variant *text* is omitted — the
    STRUCTURE-ONLY form suitable for publishing a schema example. A private pack
    keeps ``include_bodies=True``.
    """
    blocks_dir = pack_dir / "blocks"
    partials = blocks_dir / "partials"
    partials.mkdir(parents=True, exist_ok=True)
    with (blocks_dir / "blocks.jsonl").open("w") as fh:
        for g in groups:
            rec = {
                "block_id": g.group_id,
                "function": g.function,
                "subtype": "",
                "scope": "universal",
                "jurisdiction": "general",
                "statutory_citation": None,
                "triggers": "",
                "confidence": "medium",
                "slots": [],
                "partial": f"partials/{g.group_id}.md.j2",
                "variants": [
                    {k: v for k, v in var.items()
                     if include_bodies or k != "text"}
                    for var in g.variants
                ],
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if include_bodies:
                (partials / f"{g.group_id}.md.j2").write_text(g.dominant + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Clause-mining pipeline")
    ap.add_argument("--input-dir", required=True,
                    help="directory of YOUR (de-identified/synthetic) documents")
    ap.add_argument("--pack-dir", required=True, help="output pack directory")
    ap.add_argument("--dedup-threshold", type=float, default=0.7)
    ap.add_argument("--structure-only", action="store_true",
                    help="omit clause bodies (emit block STRUCTURE only)")
    a = ap.parse_args()
    groups = run_pipeline(Path(a.input_dir), dedup_threshold=a.dedup_threshold)
    write_pack(groups, Path(a.pack_dir), include_bodies=not a.structure_only)
    print(f"mined {len(groups)} candidate blocks -> {a.pack_dir}")


if __name__ == "__main__":
    main()
