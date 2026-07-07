#!/usr/bin/env python3
"""De-identify an executed legal document using Opus-extracted entity list.

Input:  input.docx, entities.json (with paragraph_index + exact_text per entity)
Output: output.docx (entities replaced with Content Controls),
        output.pii_audit.yaml (what was replaced + residuals)

Usage: deidentify.py <input.docx> <entities.json> <out_base>
"""
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WQ = f"{{{W}}}"
XMLSPACE = "{http://www.w3.org/XML/1998/namespace}space"

audit_log: list[dict] = []

# Safety-net patterns: catch PII-like strings the Opus entity extractor may miss.
# Each entry is (regex, field_name). Matched substrings are replaced with a
# Content Control bearing the field name.
RESIDUAL_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # UNC paths to Word/PDF/RTF files (e.g., \\fileserver\share\...\doc.docx)
    (re.compile(r"\\\\[^<>\s\"]+\.(?:docx?|wpd|rtf|pdf)\b", re.I), "Source_File_Path"),
    # Windows drive paths to document files (e.g., C:\Users\foo\bar.docx)
    (re.compile(r"\b[A-Za-z]:\\[^<>\s\"]+\.(?:docx?|wpd|rtf|pdf)\b"), "Source_File_Path"),
    # Notary county (e.g., "COUNTY OF YORK" inside a jurat block) — hardcoded
    # state/county names in notary blocks; extractor sometimes treats them as
    # statutory so we sweep them here.
    (re.compile(r"\bCOUNTY\s+OF\s+([A-Z][A-Z\s]*[A-Z])\b"), "Notary_County"),
    # Residual street address text immediately preceding a Witness_N_Street SDT —
    # happens when the source has two witnesses' addresses concatenated in one
    # paragraph and the extractor returned overlapping strings.
    (re.compile(r"(\b\d+\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)(?=\s+«Witness_\d+_Street»)"),
     "Witness_1_Street"),
    # Residual "City, ST" text immediately before a Witness_N_City SDT.
    (re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2}(?:\s+\d{5})?)(?=\s+«Witness_\d+_City»)"),
     "Witness_1_City"),
    # "(XXXX)" or "(xxx)" manual redaction artifact (client crossed out answer
    # by hand or with keyboard). Replace with a generic choice placeholder.
    (re.compile(r"\(\s*[Xx]{3,}\s*\)"), "Manual_Redaction_Choice"),
]


def sweep_residual_pii_patterns(doc: etree._Element) -> tuple[int, list[dict]]:
    """Regex safety-net pass: after entity-driven replacement, scan every
    paragraph for PII patterns the extractor may have missed (filesystem paths,
    etc.) and wrap matches in a Content Control SDT."""
    swept: list[dict] = []
    for p in doc.iter(f"{WQ}p"):
        for pat, field in RESIDUAL_PII_PATTERNS:
            # Flat text of paragraph
            flat = "".join(t.text or "" for t in p.iter(f"{WQ}t"))
            m = pat.search(flat)
            if not m:
                continue
            # If the pattern has a capture group, replace only that group (e.g.,
            # "COUNTY OF YORK" → keep "COUNTY OF " static, swap "YORK" for SDT).
            exact = m.group(1) if m.groups() else m.group(0)
            display = f"«{field}»"
            if replace_in_paragraph(p, exact, display, field):
                swept.append({
                    "field": field,
                    "paragraph": flat[:120],
                    "matched_length": len(exact),
                    "matched_hash": hashlib.sha256(exact.encode()).hexdigest()[:16],
                })
    return len(swept), swept


def build_content_control(tag: str, display_text: str) -> etree._Element:
    sdt = etree.Element(f"{WQ}sdt", nsmap={"w": W})
    sdtPr = etree.SubElement(sdt, f"{WQ}sdtPr")
    alias = etree.SubElement(sdtPr, f"{WQ}alias")
    alias.set(f"{WQ}val", tag.replace("_", " "))
    tag_el = etree.SubElement(sdtPr, f"{WQ}tag")
    tag_el.set(f"{WQ}val", tag)
    etree.SubElement(sdtPr, f"{WQ}showingPlcHdr")
    etree.SubElement(sdtPr, f"{WQ}text")
    sdtContent = etree.SubElement(sdt, f"{WQ}sdtContent")
    r = etree.SubElement(sdtContent, f"{WQ}r")
    t = etree.SubElement(r, f"{WQ}t")
    t.text = display_text
    t.set(XMLSPACE, "preserve")
    return sdt


def paragraph_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in p.iter(f"{WQ}t"))


_CANON_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'",   # curly single quotes / apostrophes
    "\u201C": '"', "\u201D": '"',   # curly double quotes
    "\u00A0": " ",                   # non-breaking space
    "\u2013": "-", "\u2014": "-",   # en/em dash
    "\u2026": "...",                 # horizontal ellipsis (len-changing — handled below)
})


def _canon(s: str) -> str:
    """Normalize typography so Opus's ASCII extract matches Word's typographic text.
    All single-character substitutions preserve 1:1 index mapping EXCEPT the
    ellipsis; strip that replacement if present to keep positions aligned."""
    # Use explicit char-by-char to preserve index correspondence
    out = []
    for c in s:
        out.append({
            "\u2018": "'", "\u2019": "'",
            "\u201C": '"', "\u201D": '"',
            "\u00A0": " ",
            "\u2013": "-", "\u2014": "-",
        }.get(c, c))
    return "".join(out)


def replace_in_paragraph(p: etree._Element, exact: str, display: str, field: str) -> bool:
    """Within paragraph p, find the first literal occurrence of `exact` spanning
    w:t children, and insert an SDT where it was. Adjusts w:t contents to
    preserve text before/after the match. Typography-tolerant: curly quotes,
    apostrophes, nbsp, and en/em dashes are canonicalized for matching."""
    # Collect (w:t, start, end) positions across the flat text of the paragraph
    runs_t: list[tuple[etree._Element, int, int]] = []
    cursor = 0
    for r in p.findall(f"{WQ}r"):
        for t in r.findall(f"{WQ}t"):
            s = t.text or ""
            runs_t.append((t, cursor, cursor + len(s)))
            cursor += len(s)
    flat = "".join((t.text or "") for t, _, _ in runs_t)
    # Try exact match first; fall back to typography-canonical match (1:1 char mapping)
    idx = flat.find(exact)
    if idx == -1:
        canon_flat = _canon(flat)
        canon_exact = _canon(exact)
        idx = canon_flat.find(canon_exact)
    if idx == -1:
        return False
    start_pos = idx
    end_pos = idx + len(exact)

    # Find which w:t contains start_pos
    first_t = None
    for t, a, b in runs_t:
        if a <= start_pos < b:
            first_t = (t, a)
            break
    if first_t is None:
        return False

    # Find which w:t contains end_pos (exclusive — last char position end_pos-1)
    last_t = None
    for t, a, b in runs_t:
        if a < end_pos <= b:
            last_t = (t, a, b)
            break
    if last_t is None:
        return False

    # We'll split at first_t: keep prefix; insert SDT after its parent run;
    # then clear any w:t strictly between first_t and last_t; then set last_t to suffix only.
    first_el, first_start = first_t
    last_el, last_start, last_end = last_t

    prefix = (first_el.text or "")[: start_pos - first_start]
    suffix = (last_el.text or "")[end_pos - last_start:]

    # Write prefix back on first_t
    first_el.text = prefix if prefix else None
    if prefix and (prefix.endswith(" ") or prefix.startswith(" ")):
        first_el.set(XMLSPACE, "preserve")

    # Clear any intermediate runs between first_t's parent run and last_t's parent run
    first_run = first_el.getparent()
    last_run = last_el.getparent()
    p_children = list(p)
    first_run_idx = p_children.index(first_run)
    last_run_idx = p_children.index(last_run)

    # Insert SDT right after first_run (before intermediates)
    sdt = build_content_control(field, display)
    p.insert(first_run_idx + 1, sdt)
    # Recompute indices (p shifted by 1)
    p_children = list(p)
    last_run_idx = p_children.index(last_run)
    # Now remove intermediate runs (between first_run and last_run, exclusive)
    to_remove = []
    for i in range(first_run_idx + 2, last_run_idx):  # SDT is at first_run_idx+1
        to_remove.append(p_children[i])
    for el in to_remove:
        p.remove(el)

    # Set last_t to suffix
    if first_el is not last_el:
        last_el.text = suffix if suffix else None
        if suffix and (suffix.startswith(" ") or suffix.endswith(" ")):
            last_el.set(XMLSPACE, "preserve")
    else:
        # Same w:t as start and end: split into prefix + suffix handled differently
        # first_el.text already holds prefix; insert a NEW w:r after SDT with suffix
        if suffix:
            new_r = etree.Element(f"{WQ}r")
            new_t = etree.SubElement(new_r, f"{WQ}t")
            new_t.set(XMLSPACE, "preserve")
            new_t.text = suffix
            # Insert right after SDT (which is at first_run_idx+1)
            p.insert(first_run_idx + 2, new_r)
    return True


def deidentify(src: Path, entities_json: Path, out_base: Path):
    with open(entities_json) as f:
        data = json.load(f)

    # Copy zip
    out_docx = out_base.with_suffix(".docx")
    shutil.copyfile(src, out_docx)

    with zipfile.ZipFile(src, "r") as zin:
        doc_xml = zin.read("word/document.xml")
    doc = etree.fromstring(doc_xml)

    paragraphs = list(doc.iter(f"{WQ}p"))
    # group entities by paragraph_index to process paragraphs in order
    by_para: dict[int, list[dict]] = {}
    for e in data.get("entities", []):
        pi = e.get("paragraph_index")
        if pi is None:
            continue
        by_para.setdefault(int(pi), []).append(e)

    replaced = 0
    missed = []
    for pi, ents in sorted(by_para.items()):
        if pi >= len(paragraphs):
            for e in ents:
                missed.append({**e, "reason": "paragraph_index out of range"})
            continue
        p = paragraphs[pi]
        # Sort entities within paragraph by exact_text length DESC to prefer longer matches first
        ents.sort(key=lambda x: -len(x.get("exact_text") or ""))
        for e in ents:
            exact = e.get("exact_text", "")
            field = e.get("field_name", "Unknown")
            display = f"«{field}»"
            ok = replace_in_paragraph(p, exact, display, field)
            if ok:
                audit_log.append({
                    "field": field,
                    "role": e.get("role"),
                    "paragraph_index": pi,
                    "replaced_text_hash": hashlib.sha256(exact.encode()).hexdigest()[:16],
                    "replaced_text_length": len(exact),
                })
                replaced += 1
            else:
                missed.append({**e, "reason": "text not found in paragraph"})

    # Safety-net sweep for common PII patterns the Opus extractor may have missed
    swept_count, swept = sweep_residual_pii_patterns(doc)
    if swept_count:
        print(f"safety-net swept {swept_count} residual pattern match(es)")
        audit_log.extend([{"source": "residual_sweep", **s} for s in swept])

    new_doc_xml = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)

    # Write output: copy all other parts
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == "word/document.xml":
                zout.writestr(item, new_doc_xml)
            else:
                zout.writestr(item, zin.read(item.filename))

    # Write audit
    import yaml
    out_audit = out_base.with_suffix(".pii_audit.yaml")
    out_audit.write_text(
        yaml.safe_dump(
            {
                "source": str(src),
                "entities_replaced": replaced,
                "entities_missed": missed,
                "audit": audit_log,
                "statutory_markers_claimed": data.get("statutory_boilerplate_markers", []),
            },
            sort_keys=False,
        )
    )

    print(f"replaced={replaced} missed={len(missed)} out={out_docx}")
    if missed:
        print("MISSED ENTITIES:")
        for m in missed:
            print(f"  p{m.get('paragraph_index')} {m.get('field_name')}: {m.get('reason')} — {m.get('exact_text')!r}")


if __name__ == "__main__":
    deidentify(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
