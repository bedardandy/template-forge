#!/usr/bin/env python3
"""Extract PII / client-specific entities from an executed legal document via Opus 4.7.

Usage: extract_entities.py <path.docx> > entities.json
"""
import json
import re
import urllib.request
import zipfile
from pathlib import Path

from lxml import etree

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
import os

API_URL = os.environ.get("TEMPLATE_FORGE_LLM_URL", "http://localhost:8080/v1/messages")

# The extraction prompt is a TEMPLATE. Callers supply the document type and an
# optional per-document-type "coverage checklist" (the roster of field names the
# model must account for). Nothing here is firm- or jurisdiction-specific: the
# example profile below is one illustrative document type, not a default corpus.
PROMPT_TEMPLATE = """You are reviewing an executed legal document so we can extract a blank template.

Document type: {document_type}.

The document is provided below as a NUMBERED list of paragraphs (one paragraph per line, format 'N: text'). For EVERY client-specific entity (PII and variable information), return an object with:

- paragraph_index: integer line number where the entity appears
- exact_text: the verbatim substring as it appears in that paragraph
- role: category label
- field_name: PascalCase Content Control name
- notes: optional disambiguation hint

STRICT RULES:
1. One object per OCCURRENCE. If a state name appears in three addresses, produce three entity objects (e.g., Principal_State, Witness_1_State, Witness_2_State), each with its own paragraph_index.
2. DO NOT list occurrences inside statutory boilerplate (e.g., a state name inside a statutory "STATE OF ..." heading is NOT to be listed — that's statutory text).
3. The exact_text must be unique enough that a simple find-and-replace within that paragraph would be unambiguous. If the paragraph has multiple occurrences of the same substring, include more surrounding characters.
4. Preserve case, punctuation, whitespace exactly.
5. Only list entities you can verify from the text. Do not invent.
6. ALSO list 3–5 characteristic phrases of the underlying statutory/official form that must survive verbatim (statutory_boilerplate_markers).
{coverage_block}8. RELATIONSHIP PHRASES — any free-text description of a party's relationship to another (e.g., "my son", "my wife's daughter", "my friend Jane", "my neighbor") IS client-specific PII. Extract the full phrase as a *_Relationship field. Do NOT treat generic relationship words ("spouse", "child") inside statutory explanation text as PII — only extract when they name or qualify a specific person.
9. BOLD / CAPITALIZED NAMES — if a name appears in bold, caps, or is otherwise visually emphasized, it is still PII. Extract it.
10. SOURCE PATHS — any filesystem path, UNC share path, or filename (e.g., "\\\\server\\share\\file.docx", "C:\\Users\\foo\\bar.docx") IS PII metadata. Extract as Source_File_Path.

Return ONLY JSON (no markdown fences), shape:

{{
  "document_type": "{document_type}",
  "entities": [
    {{"paragraph_index": 142, "exact_text": "Jordan A. Doe", "role": "principal_name", "field_name": "Principal_Name", "notes": "signature line"}},
    {{"paragraph_index": 145, "exact_text": "Anystate", "role": "principal_state", "field_name": "Principal_State", "notes": "after principal city, before zip"}}
  ],
  "statutory_boilerplate_markers": [
    "..."
  ],
  "notes": "optional"
}}

Numbered document text follows:

"""

# One illustrative coverage checklist (advance-health-care-directive shape). Pass
# your own via ``build_prompt(coverage=...)`` for other document types.
EXAMPLE_COVERAGE_CHECKLIST = [
    "Principal_Name, Principal_Street, Principal_City, Principal_State, Principal_Zip, Principal_Phone",
    "Agent_Name, Agent_Street, Agent_City, Agent_State, Agent_Zip, Agent_Phone, Agent_Relationship",
    "Alternate_Agent_Name, Alternate_Agent_Street, Alternate_Agent_City, Alternate_Agent_State, Alternate_Agent_Relationship",
    "Witness_1_Name, Witness_1_Street, Witness_1_City, Witness_1_State, Witness_1_Date",
    "Witness_2_Name, Witness_2_Street, Witness_2_City, Witness_2_State, Witness_2_Date",
    "Execution_Date",
]


def build_prompt(document_type: str = "a legal document",
                 coverage: list[str] | None = None) -> str:
    """Compose the extraction prompt for a given document type and optional
    mandatory-coverage checklist (roster of field names the model must account
    for). No document type is baked in as a default corpus."""
    if coverage:
        lines = "\n".join(f"   - {c}" for c in coverage)
        coverage_block = (
            "7. MANDATORY COVERAGE CHECKLIST — before returning, verify you have "
            "listed entities (when they appear in the text) for EACH of:\n"
            f"{lines}\n"
        )
    else:
        coverage_block = ""
    return PROMPT_TEMPLATE.format(document_type=document_type,
                                  coverage_block=coverage_block)


def extract_numbered_text(docx_path: Path) -> str:
    """Return document text as a numbered paragraph list: 'N: text'."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
    doc = etree.fromstring(doc_xml)
    lines = []
    for i, p in enumerate(doc.iter(f"{{{NS_W}}}p")):
        text = "".join(t.text or "" for t in p.iter(f"{{{NS_W}}}t"))
        lines.append(f"{i}: {text}")
    return "\n".join(lines)


def call_llm(prompt: str) -> dict:
    body = json.dumps({
        "model": os.environ.get("TEMPLATE_FORGE_LLM_MODEL", "claude-opus-4-7"),
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "max_tokens": 6000,
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": "x",
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    parts = resp.get("content", [])
    text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
    text = re.sub(r"^```(?:json)?\n?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Extract PII/client-specific entities from an executed .docx "
                    "via an LLM (configure endpoint with TEMPLATE_FORGE_LLM_URL).")
    ap.add_argument("docx", help="path to the executed .docx")
    ap.add_argument("--document-type", default="a legal document",
                    help="document type description for the prompt")
    ap.add_argument("--example-coverage", action="store_true",
                    help="attach the shipped example coverage checklist")
    a = ap.parse_args()
    doc_text = extract_numbered_text(Path(a.docx))
    prompt = build_prompt(
        document_type=a.document_type,
        coverage=EXAMPLE_COVERAGE_CHECKLIST if a.example_coverage else None,
    )
    result = call_llm(prompt + doc_text)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
