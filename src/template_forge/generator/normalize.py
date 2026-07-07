#!/usr/bin/env python3
"""Normalize a legal template .docx: strip metadata, accept tracked changes,
parametrize dates/years, and inject fill-in placeholders as Word Content Controls.

Produces:
  <out>.docx
  <out>.field_bindings.yaml
  <out>.signing.yaml
  <out>.changelog.md

Usage: normalize.py <input.docx> <out_base>
"""
import re
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DC = "http://purl.org/dc/elements/1.1/"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DCTERMS = "http://purl.org/dc/terms/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

NS = {"w": W, "dc": DC, "cp": CP, "dcterms": DCTERMS, "xsi": XSI}
WQ = f"{{{W}}}"

changelog: list[str] = []
bindings: list[dict] = []


def log(msg: str):
    changelog.append(msg)
    print(f"  {msg}", file=sys.stderr)


def make(tag: str, **attrs) -> etree._Element:
    el = etree.SubElement
    # helper returns an element, attrs use w: prefix namespaces
    e = etree.Element(f"{WQ}{tag}", nsmap={"w": W})
    for k, v in attrs.items():
        e.set(f"{WQ}{k}", v)
    return e


# --- Transforms ---


OPTIONAL_FIELD_ANCHORS = [
    # (regex matched against paragraph text, target field name). The trigger
    # is the statutory sentence whose trailing colon implies a fill-in space.
    (re.compile(r"AGENTS?[\u2019']?s?\s+AUTHORITY\s*:.*except as I state here\s*:", re.I | re.DOTALL),
     "Agent_Authority_Limitations"),
    (re.compile(r"RELIEF\s+FROM\s+PAIN\s*:.*hastens my death\s*:", re.I | re.DOTALL),
     "Pain_Relief_Exceptions"),
    (re.compile(r"limit the purposes for which my organs and tissues may be used\s*,", re.I | re.DOTALL),
     "Organ_Donation_Limitations"),
]


def inject_missing_address_street(doc: etree._Element) -> int:
    """For each person block (Agent, Alternate_Agent, Alternate_Agent_2, Principal),
    if a {Prefix}_Name SDT paragraph is directly followed by a {Prefix}_City SDT
    paragraph with no {Prefix}_Street anywhere in the doc, inject a new paragraph
    bearing {Prefix}_Street between them."""
    def sdt_tags_in(p: etree._Element) -> set[str]:
        tags = set()
        for sdt in p.findall(f".//{WQ}sdt"):
            tag_el = sdt.find(f".//{WQ}tag")
            if tag_el is not None and tag_el.get(f"{WQ}val"):
                tags.add(tag_el.get(f"{WQ}val"))
        return tags

    body = doc.find(f"{WQ}body")
    # Collect all SDT tags in whole document for duplicate check
    all_tags: set[str] = set()
    for sdt in doc.iter(f"{WQ}sdt"):
        tag_el = sdt.find(f".//{WQ}tag")
        if tag_el is not None and tag_el.get(f"{WQ}val"):
            all_tags.add(tag_el.get(f"{WQ}val"))

    def is_blank_like(p: etree._Element) -> bool:
        """Empty or consists only of whitespace / underscore fill-in lines
        (e.g., '_________________' used as a signature address placeholder)."""
        if sdt_tags_in(p):
            return False
        flat = paragraph_text(p)
        return not re.search(r"[A-Za-z0-9«»]", flat)

    injected = 0
    paragraphs = list(body.iter(f"{WQ}p"))
    for i in range(len(paragraphs) - 1):
        cur = paragraphs[i]
        cur_tags = sdt_tags_in(cur)
        for prefix in ("Principal", "Agent", "Alternate_Agent", "Alternate_Agent_2"):
            name_tag = f"{prefix}_Name"
            city_tag = f"{prefix}_City"
            street_tag = f"{prefix}_Street"
            if name_tag not in cur_tags:
                continue
            if street_tag in all_tags:
                continue
            # Walk forward through blank-like paragraphs to find the City paragraph
            target = None
            for k in range(i + 1, min(len(paragraphs), i + 5)):
                cand = paragraphs[k]
                cand_tags = sdt_tags_in(cand)
                if city_tag in cand_tags:
                    target = cand
                    break
                if not is_blank_like(cand):
                    break  # hit non-blank content that isn't the City line — abort
            if target is None:
                continue
            # Insert new paragraph with Street SDT right after the Name line
            parent = cur.getparent()
            cur_idx = list(parent).index(cur)
            new_p = etree.Element(f"{WQ}p")
            new_p.append(build_content_control(street_tag, f"«{street_tag}»"))
            parent.insert(cur_idx + 1, new_p)
            all_tags.add(street_tag)
            bindings.append({"field": street_tag, "placeholder": f"«{street_tag}»", "source": "address_slot_injection"})
            injected += 1
            log(f"injected {street_tag} placeholder between name and city lines")
            break  # one prefix per position
    if injected:
        log(f"injected {injected} missing-street placeholder(s)")
    return injected


def inject_optional_field_placeholders(doc: etree._Element) -> int:
    """For each known Maine ACHD fill-in anchor (statutory sentence ending in
    colon/comma implying a client-supplied exception), insert a new paragraph
    right after the anchor containing the corresponding SDT placeholder.
    Idempotent: skipped if an SDT with the same tag already exists anywhere."""
    body = doc.find(f"{WQ}body")
    # Collect existing SDT tags so we don't duplicate
    existing: set[str] = set()
    for sdt in doc.iter(f"{WQ}sdt"):
        tag_el = sdt.find(f".//{WQ}tag")
        if tag_el is not None and tag_el.get(f"{WQ}val"):
            existing.add(tag_el.get(f"{WQ}val"))
    paras = list(body.iter(f"{WQ}p"))
    injected = 0
    for p in paras:
        flat = paragraph_text(p)
        for pat, field in OPTIONAL_FIELD_ANCHORS:
            if field in existing:
                continue
            if not pat.search(flat):
                continue
            parent = p.getparent()
            idx = list(parent).index(p)
            new_p = etree.SubElement(parent, f"{WQ}p")
            parent.remove(new_p)
            new_p.append(build_content_control(field, f"«{field}»"))
            parent.insert(idx + 1, new_p)
            bindings.append({"field": field, "placeholder": f"«{field}»", "source": "optional_field_injection"})
            existing.add(field)
            injected += 1
            log(f"injected {field} placeholder after anchor paragraph")
    if injected:
        log(f"injected {injected} optional-field placeholder paragraph(s) total")
    return injected


DATE_ONLY_PAT = re.compile(r"^\s*Date\s*:\s*$", re.I)


def inject_signing_date_placeholder(doc: etree._Element) -> int:
    """Find the first 'Date:'-prefixed paragraph after '(13) SIGNATURES' that
    has no following content and inject «Execution_Date». Many executed HPOAs
    left the date blank (signed later) so no date entity was extracted."""
    body = doc.find(f"{WQ}body")
    paras = list(body.iter(f"{WQ}p"))
    start = None
    sig13_pat = re.compile(r"\(\s*13\s*\)\s+SIGNATURES", re.I)
    for i, p in enumerate(paras):
        if sig13_pat.search(paragraph_text(p)):
            start = i
            break
    if start is None:
        return 0
    for j in range(start + 1, min(len(paras), start + 10)):
        p = paras[j]
        flat = paragraph_text(p)
        if not DATE_ONLY_PAT.match(flat):
            continue
        # Already has Execution_Date SDT? skip
        has_date_sdt = False
        for sdt in p.findall(f".//{WQ}sdt"):
            tag_el = sdt.find(f".//{WQ}tag")
            if tag_el is not None and (tag_el.get(f"{WQ}val") or "").lower() in ("execution_date", "signing_date"):
                has_date_sdt = True
                break
        if has_date_sdt:
            return 0
        # Append " «Execution_Date»" to the paragraph
        sp = etree.SubElement(p, f"{WQ}r")
        spt = etree.SubElement(sp, f"{WQ}t")
        spt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        spt.text = " "
        p.append(build_content_control("Execution_Date", "«Execution_Date»"))
        bindings.append({"field": "Execution_Date", "placeholder": "«Execution_Date»", "source": "signing_date_injection"})
        log("injected Execution_Date placeholder on signing date line")
        return 1
    return 0


def remove_source_file_path_sdt(doc: etree._Element) -> int:
    """Delete any Source_File_Path SDT and the paragraph containing it. These
    come from stripped Word FILENAME fields and represent pipeline metadata
    (original .docx location), not a real template variable — surfacing them
    as «Source_File_Path» in the rendered output looks like a leak."""
    removed = 0
    for sdt in list(doc.iter(f"{WQ}sdt")):
        tag_el = sdt.find(f".//{WQ}tag")
        if tag_el is None or tag_el.get(f"{WQ}val") != "Source_File_Path":
            continue
        parent = sdt.getparent()
        # Remove SDT
        parent.remove(sdt)
        removed += 1
        # If the containing paragraph is now empty, remove it too
        p = parent
        while p is not None and p.tag != f"{WQ}p":
            p = p.getparent()
        if p is not None and not paragraph_text(p).strip() and not p.findall(f".//{WQ}sdt"):
            gp = p.getparent()
            if gp is not None:
                gp.remove(p)
    if removed:
        log(f"removed {removed} Source_File_Path SDT(s)")
    return removed


WITNESS_CITY_TRAILING_PAT = re.compile(
    r"[,\s]+(?:[A-Z][a-z]+\s+)?[A-Z]{2}(?:\s+\d{5})?\s*$"
)


def cleanup_witness_sdt_trailing_residuals(doc: etree._Element) -> int:
    """If a paragraph contains a Witness_*_City SDT and trailing text of the
    form ', ME 03903' or '  Portland ME 03903' after it, strip that residual."""
    removed = 0
    for p in doc.iter(f"{WQ}p"):
        has_witness_city = False
        for sdt in p.findall(f".//{WQ}sdt"):
            tag_el = sdt.find(f".//{WQ}tag")
            if tag_el is not None:
                val = tag_el.get(f"{WQ}val") or ""
                if val.startswith("Witness_") and val.endswith("_City"):
                    has_witness_city = True
                    break
        if not has_witness_city:
            continue
        flat = paragraph_text(p)
        m = WITNESS_CITY_TRAILING_PAT.search(flat)
        if not m:
            continue
        residual = m.group(0)
        # Find and clear this text across w:t elements from the end
        to_strip = len(residual)
        for t in reversed(list(p.iter(f"{WQ}t"))):
            if to_strip <= 0:
                break
            if not t.text:
                continue
            if len(t.text) <= to_strip:
                to_strip -= len(t.text)
                t.text = None
            else:
                t.text = t.text[:-to_strip]
                to_strip = 0
        removed += 1
    if removed:
        log(f"cleaned trailing city/state residuals in {removed} witness paragraph(s)")
    return removed


PRINCIPAL_DUAL_LABEL_PATS = [
    # (regex, col1_field, col2_field) — columns in left→right order
    (re.compile(r"\(\s*address\s*\)\s+\(\s*print\s+your\s+name\s*\)", re.I),
     "Principal_Street", "Principal_Name"),
    (re.compile(r"\(\s*print\s+your\s+name\s*\)\s+\(\s*address\s*\)", re.I),
     "Principal_Name", "Principal_Street"),
    (re.compile(r"^\s*\(\s*city\s*\)\s+\(\s*state\s*\)\s*$", re.I),
     "Principal_City", "Principal_State"),
]


def inject_principal_signature_dual_labels(doc: etree._Element) -> int:
    """Some cluster-77 variants place the principal signature block with
    side-by-side column labels like '(address)  (print your name)' and
    '(city)  (state)' after '(13) SIGNATURES'. Inject a new paragraph BEFORE
    each such label with the two corresponding Principal_* SDTs separated by
    a tab."""
    body = doc.find(f"{WQ}body")
    paras = list(body.iter(f"{WQ}p"))
    start = None
    sig13_pat = re.compile(r"\(\s*13\s*\)\s+SIGNATURES", re.I)
    for i, p in enumerate(paras):
        if sig13_pat.search(paragraph_text(p)):
            start = i
            break
    if start is None:
        return 0
    # Existing tags in doc — don't duplicate
    existing: set[str] = set()
    for sdt in doc.iter(f"{WQ}sdt"):
        tag_el = sdt.find(f".//{WQ}tag")
        if tag_el is not None and tag_el.get(f"{WQ}val"):
            existing.add(tag_el.get(f"{WQ}val"))

    def tab_run():
        r = etree.Element(f"{WQ}r")
        etree.SubElement(r, f"{WQ}tab")
        return r

    injected = 0
    for j in range(start + 1, min(len(paragraphs := paras), start + 15)):
        p_label = paragraphs[j]
        flat = paragraph_text(p_label).strip()
        matched = None
        for pat, f1, f2 in PRINCIPAL_DUAL_LABEL_PATS:
            if pat.search(flat):
                matched = (f1, f2)
                break
        if matched is None:
            continue
        f1, f2 = matched
        # Skip if BOTH fields already have SDTs somewhere in doc
        if f1 in existing and f2 in existing:
            continue
        new_p = etree.Element(f"{WQ}p")
        if f1 not in existing:
            new_p.append(build_content_control(f1, f"«{f1}»"))
            bindings.append({"field": f1, "placeholder": f"«{f1}»", "source": "principal_sig_injection"})
            existing.add(f1)
        new_p.append(tab_run())
        if f2 not in existing:
            new_p.append(build_content_control(f2, f"«{f2}»"))
            bindings.append({"field": f2, "placeholder": f"«{f2}»", "source": "principal_sig_injection"})
            existing.add(f2)
        parent = p_label.getparent()
        idx = list(parent).index(p_label)
        parent.insert(idx, new_p)
        injected += 1
    if injected:
        log(f"injected {injected} principal signature dual-label paragraph(s)")
    return injected


CLUSTER77_WITNESS_LABEL_PATS = {
    "Name":      re.compile(r"\(\s*print\s*name\s*\).*?\(\s*print\s*name\s*\)", re.I),
    "Street":    re.compile(r"\(\s*address\s*\).*?\(\s*address\s*\)", re.I),
    "CityState": re.compile(r"\(\s*city\s*\)\s*\(\s*state\s*\).*?\(\s*city\s*\)\s*\(\s*state\s*\)", re.I),
    "Date":      re.compile(r"\(\s*date\s*\).*?\(\s*date\s*\)", re.I),
}


def inject_cluster77_witness_placeholders(doc: etree._Element) -> int:
    """Cluster-77 / older-template variant: witness block uses dual labels
    side-by-side in a single paragraph (e.g., '(print name)  (print name)')
    rather than table cells. Insert a new paragraph BEFORE each dual-label
    paragraph containing both Witness_N_Field SDTs separated by a tab."""
    body = doc.find(f"{WQ}body")
    paragraphs = list(body.iter(f"{WQ}p"))
    start = None
    for i, p in enumerate(paragraphs):
        if "SIGNATURES OF WITNESSES" in paragraph_text(p).upper():
            start = i
            break
    if start is None:
        return 0

    def prev_has_witness_sdt(p_label: etree._Element) -> bool:
        parent = p_label.getparent()
        if parent is None:
            return False
        idx = list(parent).index(p_label)
        if idx == 0:
            return False
        prev = list(parent)[idx - 1]
        if prev.tag != f"{WQ}p":
            return False
        for sdt in prev.findall(f".//{WQ}sdt"):
            tag_el = sdt.find(f".//{WQ}tag")
            if tag_el is not None and (tag_el.get(f"{WQ}val") or "").startswith("Witness_"):
                return True
        return False

    def tab_run():
        r = etree.Element(f"{WQ}r")
        etree.SubElement(r, f"{WQ}tab")
        return r

    def space_run():
        r = etree.Element(f"{WQ}r")
        t = etree.SubElement(r, f"{WQ}t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = " "
        return r

    injected = 0
    for j in range(start + 1, min(len(paragraphs), start + 25)):
        p_label = paragraphs[j]
        flat = paragraph_text(p_label).strip()
        label_kind = None
        for kind, pat in CLUSTER77_WITNESS_LABEL_PATS.items():
            if pat.search(flat):
                label_kind = kind
                break
        if label_kind is None:
            continue
        if prev_has_witness_sdt(p_label):
            continue
        new_p = etree.Element(f"{WQ}p")
        if label_kind == "CityState":
            new_p.append(build_content_control("Witness_1_City", "«Witness_1_City»"))
            new_p.append(space_run())
            new_p.append(build_content_control("Witness_1_State", "«Witness_1_State»"))
            new_p.append(tab_run())
            new_p.append(build_content_control("Witness_2_City", "«Witness_2_City»"))
            new_p.append(space_run())
            new_p.append(build_content_control("Witness_2_State", "«Witness_2_State»"))
            for f in ("Witness_1_City", "Witness_1_State", "Witness_2_City", "Witness_2_State"):
                bindings.append({"field": f, "placeholder": f"«{f}»", "source": "cluster77_witness_injection"})
            injected += 4
        else:
            f1 = f"Witness_1_{label_kind}"
            f2 = f"Witness_2_{label_kind}"
            new_p.append(build_content_control(f1, f"«{f1}»"))
            new_p.append(tab_run())
            new_p.append(build_content_control(f2, f"«{f2}»"))
            bindings.append({"field": f1, "placeholder": f"«{f1}»", "source": "cluster77_witness_injection"})
            bindings.append({"field": f2, "placeholder": f"«{f2}»", "source": "cluster77_witness_injection"})
            injected += 2
        parent = p_label.getparent()
        idx = list(parent).index(p_label)
        parent.insert(idx, new_p)
    if injected:
        log(f"injected {injected} cluster-77 witness placeholder SDT(s)")
    return injected


WITNESS_LABEL_PATS = {
    "Name":      re.compile(r"^\(\s*print\s*name\s*\)$", re.I),
    "Street":    re.compile(r"^\(\s*address\s*\)$", re.I),
    "CityState": re.compile(r"^\(\s*city\s*\)\s*\(\s*state\s*\)$", re.I),
    "Date":      re.compile(r"^\(\s*date\s*\)$", re.I),
}


def inject_witness_placeholders(doc: etree._Element) -> int:
    """Maine ACHD witness blocks use label paragraphs '(print name)', '(address)',
    '(city) (state)', '(date)' that pair with an adjacent (above) content slot.
    When the source doc was signed in ink the slots are blank. This transform
    finds the 'SIGNATURES OF WITNESSES' section and injects a Witness_N_* SDT
    into any empty slot, alternating N=1/N=2 in paragraph order per label type.
    Skips slots that already carry a Witness_* SDT (idempotent)."""
    body = doc.find(f"{WQ}body")
    paragraphs = list(body.iter(f"{WQ}p"))  # includes paragraphs inside tables
    # Locate the witness signatures section
    start = None
    for i, p in enumerate(paragraphs):
        if "SIGNATURES OF WITNESSES" in paragraph_text(p).upper():
            start = i
            break
    if start is None:
        return 0
    # Scan forward for label paragraphs (capped window)
    counts: dict[str, int] = {k: 0 for k in WITNESS_LABEL_PATS}
    injected = 0
    for j in range(start + 1, min(len(paragraphs), start + 50)):
        flat = paragraph_text(paragraphs[j]).strip()
        label = None
        for kind, pat in WITNESS_LABEL_PATS.items():
            if pat.match(flat):
                label = kind
                break
        if label is None:
            continue
        counts[label] += 1
        witness_num = counts[label]  # 1st match → W1, 2nd → W2
        if witness_num > 2:
            continue  # more than 2 witnesses: out of spec, skip
        if j == 0:
            continue
        slot = paragraphs[j - 1]
        # Skip if slot already has a Witness_* SDT
        already_has = False
        for sdt in slot.findall(f".//{WQ}sdt"):
            tag_el = sdt.find(f".//{WQ}tag")
            if tag_el is not None and (tag_el.get(f"{WQ}val") or "").startswith("Witness_"):
                already_has = True
                break
        if already_has:
            continue
        # Skip if slot has non-whitespace text (conflict — don't overwrite)
        if paragraph_text(slot).strip():
            continue
        # Inject: CityState becomes TWO SDTs separated by a space
        if label == "CityState":
            city_field = f"Witness_{witness_num}_City"
            state_field = f"Witness_{witness_num}_State"
            slot.append(build_content_control(city_field, f"«{city_field}»"))
            sp = etree.SubElement(slot, f"{WQ}r")
            spt = etree.SubElement(sp, f"{WQ}t")
            spt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            spt.text = " "
            slot.append(build_content_control(state_field, f"«{state_field}»"))
            bindings.append({"field": city_field, "placeholder": f"«{city_field}»", "source": "witness_injection"})
            bindings.append({"field": state_field, "placeholder": f"«{state_field}»", "source": "witness_injection"})
            injected += 2
        else:
            field = f"Witness_{witness_num}_{label}"
            slot.append(build_content_control(field, f"«{field}»"))
            bindings.append({"field": field, "placeholder": f"«{field}»", "source": "witness_injection"})
            injected += 1
    if injected:
        log(f"injected {injected} witness placeholder SDT(s)")
    return injected


def strip_strikethrough_formatting(doc: etree._Element) -> int:
    """Remove w:strike / w:dstrike run properties. Client-executed legal docs
    often use strikethrough to indicate a specific client's selections among
    enumerated options (e.g., 'strike the items you do NOT want'); leaving the
    strike in a blank template would leak those prior selections. Keeps the
    text itself intact so all options remain present in the template."""
    removed = 0
    for strike_tag in ("strike", "dstrike"):
        for s in list(doc.iter(f"{WQ}{strike_tag}")):
            val = s.get(f"{WQ}val")
            if val in ("false", "0"):
                continue  # explicitly disabled
            parent = s.getparent()
            if parent is not None:
                parent.remove(s)
                removed += 1
    if removed:
        log(f"stripped {removed} strikethrough formatting mark(s)")
    return removed


def strip_comments_and_tracked_changes(doc: etree._Element) -> None:
    """Remove comment refs + accept tracked changes (promote ins, delete del)."""
    cnt = 0
    for tag in ("commentRangeStart", "commentRangeEnd", "commentReference"):
        for el in doc.iter(f"{WQ}{tag}"):
            el.getparent().remove(el)
            cnt += 1
    # Accept insertions: replace <w:ins>...<runs>...</w:ins> with the runs
    for ins in list(doc.iter(f"{WQ}ins")):
        parent = ins.getparent()
        idx = list(parent).index(ins)
        for child in reversed(list(ins)):
            parent.insert(idx, child)
        parent.remove(ins)
        cnt += 1
    # Reject deletions: remove <w:del> entirely
    for dl in list(doc.iter(f"{WQ}del")):
        dl.getparent().remove(dl)
        cnt += 1
    if cnt:
        log(f"stripped/accepted {cnt} comment/change markers")


def paragraph_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in p.iter(f"{WQ}t"))


def paragraph_is_empty(p: etree._Element) -> bool:
    return paragraph_text(p).strip() == ""


def body_paragraphs(doc: etree._Element) -> list[etree._Element]:
    body = doc.find(f"{WQ}body")
    return [p for p in body.findall(f"{WQ}p")]


def apply_spacing(p: etree._Element, before: int | None = None, after: int | None = None, keep_next: bool = False):
    """Set spacing-before/after (in twips, 1 line = 240 ≈ 12pt) and keepNext."""
    pPr = p.find(f"{WQ}pPr")
    if pPr is None:
        pPr = etree.SubElement(p, f"{WQ}pPr")
        p.insert(0, pPr)
        p.remove(pPr)  # reinsert at index 0
        p.insert(0, pPr)
    sp = pPr.find(f"{WQ}spacing")
    if sp is None:
        sp = etree.SubElement(pPr, f"{WQ}spacing")
    if before is not None:
        sp.set(f"{WQ}before", str(before))
    if after is not None:
        sp.set(f"{WQ}after", str(after))
    if keep_next:
        if pPr.find(f"{WQ}keepNext") is None:
            etree.SubElement(pPr, f"{WQ}keepNext")


def remove_leading_empty_paragraphs(doc: etree._Element) -> int:
    body = doc.find(f"{WQ}body")
    paragraphs = body.findall(f"{WQ}p")
    removed = 0
    for p in paragraphs:
        if paragraph_is_empty(p):
            body.remove(p)
            removed += 1
        else:
            if removed:
                apply_spacing(p, before=240)
                log(f"removed {removed} leading empty paragraphs; added Space Before on first content paragraph")
            break
    return removed


def collapse_internal_empty_paragraphs(doc: etree._Element, after_twips: int = 240) -> int:
    """For each non-empty → empty → non-empty pattern, delete empty and apply Space After to preceding."""
    body = doc.find(f"{WQ}body")
    changed = 0
    while True:
        paras = body.findall(f"{WQ}p")
        found = False
        for i, p in enumerate(paras):
            if not paragraph_is_empty(p):
                continue
            prev = paras[i - 1] if i > 0 else None
            nxt = paras[i + 1] if i + 1 < len(paras) else None
            if prev is not None and not paragraph_is_empty(prev) and nxt is not None and not paragraph_is_empty(nxt):
                apply_spacing(prev, after=after_twips)
                body.remove(p)
                changed += 1
                found = True
                break
        if not found:
            break
    if changed:
        log(f"collapsed {changed} intra-body empty paragraphs into Space After")
    return changed


DYNAMIC_FIELD_TYPES = {
    "FILENAME", "AUTHOR", "USERNAME", "USERINITIALS", "USERADDRESS",
    "SAVEDATE", "CREATEDATE", "EDITTIME", "LASTSAVEDBY", "PRINTDATE",
    "TIME", "DATE", "FILESIZE", "NUMPAGES", "REVNUM", "TEMPLATE",
    "DOCPROPERTY", "DOCVARIABLE", "INFO", "SUBJECT", "TITLE", "COMMENTS",
}


def unlink_dynamic_fields(doc: etree._Element) -> int:
    """Unlink Word info/dynamic fields (FILENAME, AUTHOR, SAVEDATE, etc.) that
    re-compute at render time and leak filesystem or user metadata. Strips the
    fldChar + instrText wrappers while keeping any result-zone content (cached
    text or nested SDT) so previously-inserted placeholders remain visible."""
    body = doc.find(f"{WQ}body")
    unlinked = 0
    for p in list(body.iter(f"{WQ}p")):
        processed: set[int] = set()
        while True:
            fldchars = list(p.iter(f"{WQ}fldChar"))
            begin_idx = None
            for k, fc in enumerate(fldchars):
                if fc.get(f"{WQ}fldCharType") == "begin" and id(fc) not in processed:
                    begin_idx = k
                    break
            if begin_idx is None:
                break
            begin_fc = fldchars[begin_idx]
            processed.add(id(begin_fc))
            depth = 1
            end_idx = None
            sep_idx = None
            for k in range(begin_idx + 1, len(fldchars)):
                t = fldchars[k].get(f"{WQ}fldCharType")
                if t == "begin":
                    depth += 1
                elif t == "separate" and depth == 1 and sep_idx is None:
                    sep_idx = k
                elif t == "end":
                    depth -= 1
                    if depth == 0:
                        end_idx = k
                        break
            if end_idx is None:
                continue
            end_fc = fldchars[end_idx]
            instr_barrier = fldchars[sep_idx] if sep_idx is not None else end_fc
            instr_text = ""
            in_zone = False
            for el in p.iter():
                if el is begin_fc:
                    in_zone = True
                    continue
                if el is instr_barrier:
                    break
                if in_zone and el.tag == f"{WQ}instrText" and el.text:
                    instr_text += el.text
            toks = instr_text.strip().split()
            ftype = toks[0].upper() if toks else ""
            if ftype not in DYNAMIC_FIELD_TYPES:
                continue
            to_remove = [begin_fc, end_fc]
            if sep_idx is not None:
                to_remove.append(fldchars[sep_idx])
            in_zone = False
            for el in list(p.iter()):
                if el is begin_fc:
                    in_zone = True
                    continue
                if el is end_fc:
                    break
                if in_zone and el.tag == f"{WQ}instrText":
                    to_remove.append(el)
            for el in to_remove:
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
            unlinked += 1
            log(f"unlinked {ftype} field")
    if unlinked:
        log(f"unlinked {unlinked} dynamic field(s) total")
    return unlinked


def convert_mergefield_to_sdt(doc: etree._Element) -> int:
    """Find each begin/separate/end fldChar group whose instrText is MERGEFIELD,
    replace with a w:sdt Content Control that preserves the cached result text."""
    body = doc.find(f"{WQ}body")
    converted = 0
    # Iterate paragraphs; within each, scan runs for fldChar begin
    for p in body.findall(f"{WQ}p"):
        runs = list(p)
        i = 0
        while i < len(runs):
            r = runs[i]
            if r.tag != f"{WQ}r":
                i += 1
                continue
            fld_char = r.find(f"{WQ}fldChar")
            if fld_char is None or fld_char.get(f"{WQ}fldCharType") != "begin":
                i += 1
                continue
            # Find matching end
            j = i + 1
            instr_text = ""
            cached_text = ""
            group_runs = [r]
            state = "instr"
            while j < len(runs):
                rj = runs[j]
                if rj.tag != f"{WQ}r":
                    j += 1
                    continue
                group_runs.append(rj)
                fc = rj.find(f"{WQ}fldChar")
                if fc is not None:
                    t = fc.get(f"{WQ}fldCharType")
                    if t == "separate":
                        state = "cache"
                    elif t == "end":
                        j += 1
                        break
                it = rj.find(f"{WQ}instrText")
                if it is not None and it.text:
                    instr_text += it.text
                for tx in rj.findall(f"{WQ}t"):
                    if state == "cache" and tx.text:
                        cached_text += tx.text
                j += 1
            # Parse field name
            m = re.search(r"MERGEFIELD\s+([A-Za-z_][A-Za-z0-9_]*)", instr_text)
            if m:
                field_name = m.group(1)
                # Build SDT
                sdt = build_content_control(field_name, cached_text or f"«{field_name}»")
                parent = r.getparent()
                # Insert sdt at r's position
                idx = list(parent).index(r)
                for gr in group_runs:
                    parent.remove(gr)
                parent.insert(idx, sdt)
                bindings.append({"field": field_name, "placeholder": cached_text or f"«{field_name}»"})
                converted += 1
                # Refresh run list
                runs = list(p)
                i = idx + 1
            else:
                i = j
    if converted:
        log(f"converted {converted} MERGEFIELD(s) to Content Controls")
    return converted


def build_content_control(tag: str, display_text: str) -> etree._Element:
    """Build a plain-text Content Control (SDT) with given tag and placeholder display text."""
    sdt = etree.Element(f"{WQ}sdt", nsmap={"w": W})
    sdtPr = etree.SubElement(sdt, f"{WQ}sdtPr")
    # Title
    alias = etree.SubElement(sdtPr, f"{WQ}alias")
    alias.set(f"{WQ}val", tag.replace("_", " ").title())
    tag_el = etree.SubElement(sdtPr, f"{WQ}tag")
    tag_el.set(f"{WQ}val", tag)
    # Make it show placeholder style
    show_ph = etree.SubElement(sdtPr, f"{WQ}showingPlcHdr")
    etree.SubElement(sdtPr, f"{WQ}text")
    # Content
    sdtContent = etree.SubElement(sdt, f"{WQ}sdtContent")
    r = etree.SubElement(sdtContent, f"{WQ}r")
    t = etree.SubElement(r, f"{WQ}t")
    t.text = display_text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return sdt


DATE_PAT = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s*(20\d{2})\b"
)
YEAR_PAT = re.compile(r"\b(20\d{2})\b")


def _split_run_and_insert_sdt(t_el: etree._Element, match: re.Match, sdt: etree._Element, display: str, field: str, original: str):
    """Split a w:t at a regex match boundary and insert an SDT after its parent run."""
    before = t_el.text[: match.start()]
    after = t_el.text[match.end():]
    t_el.text = before if before else None
    # Preserve whitespace if the 'before' ends with or starts with space
    if before and (before[-1].isspace() or before[0].isspace()):
        t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    r = t_el.getparent()
    while r.tag != f"{WQ}r":
        r = r.getparent()
    r_parent = r.getparent()
    idx = list(r_parent).index(r)
    r_parent.insert(idx + 1, sdt)
    if after:
        r_after = etree.Element(f"{WQ}r")
        t_after = etree.SubElement(r_after, f"{WQ}t")
        t_after.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t_after.text = after
        r_parent.insert(idx + 2, r_after)
    bindings.append({"field": field, "placeholder": display, "original_literal": original})


SUBJECT_PREFIX_PAT = re.compile(r"^\s*Re\s*:", re.I)


def replace_hardcoded_date_and_year(doc: etree._Element) -> tuple[int, int | None]:
    """Subject-line & letterhead-date pass (once each). Returns (count, primary_year)."""
    body = doc.find(f"{WQ}body")
    cnt = 0
    primary_year: int | None = None
    date_done = False
    subject_done = False
    for p in body.findall(f"{WQ}p"):
        text = paragraph_text(p)
        stripped = text.strip()
        # First: date-only paragraph (letterhead date)
        if not date_done and DATE_PAT.fullmatch(stripped):
            for r in p.findall(f"{WQ}r"):
                p.remove(r)
            sdt = build_content_control("Date", "«Date»")
            p.append(sdt)
            bindings.append({"field": "Date", "placeholder": "«Date»", "original_literal": stripped})
            cnt += 1
            date_done = True
            log(f"replaced hard-coded date '{stripped}' with Date Content Control")
            continue
        # Subject line: starts with 'Re:' (letter convention)
        if not subject_done and SUBJECT_PREFIX_PAT.search(stripped) and YEAR_PAT.search(stripped):
            for r in p.findall(f"{WQ}r"):
                for t in r.findall(f"{WQ}t"):
                    if t.text and YEAR_PAT.search(t.text):
                        match = YEAR_PAT.search(t.text)
                        primary_year = int(match.group(0))
                        sdt = build_content_control("Year", "«Year»")
                        _split_run_and_insert_sdt(t, match, sdt, "«Year»", "Year", match.group(0))
                        cnt += 1
                        subject_done = True
                        log(f"replaced hard-coded year '{match.group(0)}' in subject with Year Content Control (primary_year={primary_year})")
                        break
                else:
                    continue
                break
        if date_done and subject_done:
            break
    return cnt, primary_year


def replace_body_year_and_date_references(doc: etree._Element, primary_year: int | None) -> int:
    """Pass over body paragraphs (after subject pass) and parameterize remaining
    year / full-date references deterministically.

    Rules:
    - Full date 'Month Day, 20XX' → named Content Control (Date_2, Date_3, ...)
    - Bare 20XX year == primary_year → «Year»
    - Bare 20XX year == primary_year - 1 → «Prior_Year»
    - Any other year → leave and log warning (unknown context).
    """
    body = doc.find(f"{WQ}body")
    cnt = 0
    warned_years: set[int] = set()
    date_counter = 1  # Date_1 was the letterhead date if present

    # Count existing Date content controls to offset the date_counter properly
    for sdt in doc.iter(f"{WQ}sdt"):
        tag = sdt.find(f"{WQ}sdtPr/{WQ}tag")
        if tag is not None and (tag.get(f"{WQ}val") or "").startswith("Date"):
            date_counter += 1

    for p in body.findall(f"{WQ}p"):
        # Iterate until no more matches in this paragraph (keep re-fetching since we mutate)
        while True:
            found = False
            # Pass 1: full dates (greedy before bare years — so the year inside a date isn't double-matched)
            for r in list(p.findall(f"{WQ}r")):
                for t in list(r.findall(f"{WQ}t")):
                    if not t.text:
                        continue
                    dm = DATE_PAT.search(t.text)
                    if dm:
                        name = f"Date_{date_counter}"
                        display = f"«{name}»"
                        sdt = build_content_control(name, display)
                        orig = dm.group(0)
                        _split_run_and_insert_sdt(t, dm, sdt, display, name, orig)
                        log(f"body: replaced date '{orig}' → {display}")
                        date_counter += 1
                        cnt += 1
                        found = True
                        break
                if found:
                    break
            if found:
                continue
            # Pass 2: bare years
            for r in list(p.findall(f"{WQ}r")):
                for t in list(r.findall(f"{WQ}t")):
                    if not t.text:
                        continue
                    ym = YEAR_PAT.search(t.text)
                    if ym:
                        y = int(ym.group(0))
                        if primary_year is not None and y == primary_year:
                            field, display = "Year", "«Year»"
                        elif primary_year is not None and y == primary_year - 1:
                            field, display = "Prior_Year", "«Prior_Year»"
                        else:
                            if y not in warned_years:
                                log(f"body: unknown year '{y}' left as literal (not current or prior)")
                                warned_years.add(y)
                            continue  # skip this match; check next t
                        sdt = build_content_control(field, display)
                        _split_run_and_insert_sdt(t, ym, sdt, display, field, ym.group(0))
                        log(f"body: replaced year '{y}' → {display}")
                        cnt += 1
                        found = True
                        break
                if found:
                    break
            if not found:
                break
    return cnt


def keep_closing_with_signature(doc: etree._Element) -> None:
    """Find closing line (e.g. 'Very truly yours' / 'Yours truly' / 'Sincerely') and apply keepNext."""
    body = doc.find(f"{WQ}body")
    for p in body.findall(f"{WQ}p"):
        text = paragraph_text(p).strip().lower()
        if text in {"very truly yours:", "very truly yours,", "yours truly,", "sincerely,", "sincerely", "best regards,"}:
            apply_spacing(p, keep_next=True)
            log(f"applied keep-with-next on closing: {paragraph_text(p).strip()!r}")
            return


def merge_adjacent_runs(doc: etree._Element) -> int:
    """Word often splits runs arbitrarily. Merge adjacent runs with identical rPr,
    then coalesce adjacent w:t elements within each run into a single w:t."""
    W_ = WQ
    merged = 0
    for p in doc.iter(f"{W_}p"):
        children = list(p)
        i = 0
        while i < len(children) - 1:
            cur = children[i]
            nxt = children[i + 1]
            if cur.tag != f"{W_}r" or nxt.tag != f"{W_}r":
                i += 1
                continue
            cur_rpr = cur.find(f"{W_}rPr")
            nxt_rpr = nxt.find(f"{W_}rPr")
            cur_rpr_s = etree.tostring(cur_rpr, method="c14n") if cur_rpr is not None else b""
            nxt_rpr_s = etree.tostring(nxt_rpr, method="c14n") if nxt_rpr is not None else b""
            if cur_rpr_s != nxt_rpr_s:
                i += 1
                continue
            for child in list(nxt):
                if child.tag == f"{W_}rPr":
                    continue
                cur.append(child)
            p.remove(nxt)
            merged += 1
            children = list(p)
    # Coalesce adjacent w:t children inside each run
    coalesced = 0
    for r in doc.iter(f"{W_}r"):
        cur_t = None
        to_remove = []
        for ch in list(r):
            if ch.tag == f"{W_}t":
                if cur_t is None:
                    cur_t = ch
                else:
                    cur_t.text = (cur_t.text or "") + (ch.text or "")
                    cur_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    to_remove.append(ch)
                    coalesced += 1
            else:
                # Any non-t element resets (e.g. w:br, w:tab)
                cur_t = None
        for ch in to_remove:
            r.remove(ch)
    if merged or coalesced:
        log(f"merged {merged} adjacent runs, coalesced {coalesced} w:t elements")
    return merged


def trim_whitespace(doc: etree._Element) -> int:
    """Collapse runs of >=2 internal spaces to a single space. NEVER trim edges
    of w:t elements — that erases word separators (because text is split across
    elements, trailing/leading whitespace may be intentional boundary)."""
    cnt = 0
    for t in doc.iter(f"{WQ}t"):
        if t.text and "  " in t.text:
            new = re.sub(r" {2,}", " ", t.text)
            if new != t.text:
                t.text = new
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                cnt += 1
    if cnt:
        log(f"collapsed double-spaces in {cnt} text runs (edge-safe)")
    return cnt


def sanitize_core_xml(core_xml: bytes, firm: str = "Template Author") -> bytes:
    root = etree.fromstring(core_xml)
    changed = False
    for tag, nsu in (("creator", DC), ("lastModifiedBy", CP), ("keywords", CP), ("description", DC)):
        el = root.find(f"{{{nsu}}}{tag}")
        if el is not None:
            if el.text and el.text != firm:
                el.text = firm
                changed = True
    if changed:
        log(f"sanitized core.xml metadata to '{firm}'")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def normalize(src: Path, out_base: Path) -> None:
    out_docx = out_base.with_suffix(".docx")
    out_yaml = out_base.with_suffix(".field_bindings.yaml")
    out_signing = out_base.with_suffix(".signing.yaml")
    out_changelog = out_base.with_suffix(".changelog.md")

    # Copy the zip so we keep all other parts (headers, footers, styles, etc.)
    shutil.copyfile(src, out_docx)

    # Open + edit in place
    with zipfile.ZipFile(src, "r") as zin:
        doc_xml = zin.read("word/document.xml")
        core_xml = zin.read("docProps/core.xml")
        # Also remove comments.xml reference if present
        has_comments = "word/comments.xml" in zin.namelist()
    doc = etree.fromstring(doc_xml)

    log("=== Normalizing ===")
    strip_comments_and_tracked_changes(doc)
    strip_strikethrough_formatting(doc)
    removed = remove_leading_empty_paragraphs(doc)
    collapse_internal_empty_paragraphs(doc)
    unlink_dynamic_fields(doc)            # strip FILENAME/AUTHOR/etc. that re-compute at render time
    convert_mergefield_to_sdt(doc)        # convert fields BEFORE merging (fldChar runs)
    merge_adjacent_runs(doc)              # now safe to unify split runs
    _, primary_year = replace_hardcoded_date_and_year(doc)  # subject pass, detect primary_year
    replace_body_year_and_date_references(doc, primary_year)  # body pass
    remove_source_file_path_sdt(doc)      # drop pipeline-metadata placeholders
    inject_witness_placeholders(doc)      # fill blank witness slots with SDT placeholders (cluster-4: table cells)
    inject_cluster77_witness_placeholders(doc)  # cluster-77 / older layout: dual-label paragraphs
    cleanup_witness_sdt_trailing_residuals(doc)  # strip ', ME 03903' residuals after witness city SDTs
    inject_principal_signature_dual_labels(doc)  # cluster-77 principal sig block dual-column labels
    inject_signing_date_placeholder(doc)  # fill blank principal signing date line
    inject_optional_field_placeholders(doc)  # insert SDTs for known fill-in anchors
    inject_missing_address_street(doc)    # add missing Street between Name and City/State
    keep_closing_with_signature(doc)
    trim_whitespace(doc)
    new_doc_xml = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)

    new_core_xml = sanitize_core_xml(core_xml)

    # Write: copy all non-modified parts, overwrite document.xml + core.xml, skip comments.xml
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name = item.filename
            if name == "word/document.xml":
                zout.writestr(item, new_doc_xml)
            elif name == "docProps/core.xml":
                zout.writestr(item, new_core_xml)
            elif name == "word/comments.xml":
                log("stripped word/comments.xml")
                continue
            else:
                zout.writestr(item, zin.read(name))

    # Write sidecar files
    import yaml
    out_yaml.write_text(yaml.safe_dump({"bindings": bindings}, sort_keys=False))
    out_signing.write_text(
        yaml.safe_dump(
            {
                "profile": "correspondence",
                "date_policy": "blank",
                "notary_required": False,
                "witness_count": 0,
                "signatures": ["attorney"],
            },
            sort_keys=False,
        )
    )
    out_changelog.write_text("\n".join(f"- {l}" for l in changelog))
    print(f"\nOUTPUT: {out_docx}\nBINDINGS: {out_yaml}\nSIGNING: {out_signing}\nCHANGELOG: {out_changelog}")


if __name__ == "__main__":
    normalize(Path(sys.argv[1]), Path(sys.argv[2]))
