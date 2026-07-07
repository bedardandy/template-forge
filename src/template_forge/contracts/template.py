"""Template-specific data contracts: block, variant, slot, and manifest schema.

These describe the *structure* of a template pack — the shape of a block record,
a slot definition, and a manifest — with **no clause text and no firm content**.
They complement (and do not duplicate) the shared fact object defined by the
published ``legal-facts-schema`` package (matter / parties / party / facts); see
``docs/RELATION.md``. A pack's *facts* are validated by legal-facts-schema; a
pack's *blocks and manifests* are described here.

Stdlib dataclasses per the zero-dependency convention: ``to_dict`` / ``from_dict``
round-trip through JSON.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class SlotType(str, Enum):
    """The kind of value a slot carries. Drives structured quasi-identifier
    detection in the mining pipeline and validation in the engine."""

    TEXT = "text"
    NAME = "name"
    DATE = "date"
    AMOUNT = "amount"
    ADDRESS = "address"
    EMAIL = "email"
    PHONE = "phone"
    CASE_NUMBER = "case_number"
    REGISTRY_REF = "registry_ref"
    ENUM = "enum"
    FLAG = "flag"


class PIIClass(str, Enum):
    """How a slot relates to PII in the source corpus.

    ``pii_parametrized`` — the slot replaced a quasi-identifier that was blanked
        out of the block body (the body is safe to publish; the value is filled
        at render time).
    ``pii_residual`` — a slot whose block body may still carry residual
        identifying phrasing that a re-audit must clear before publication.
    ``none`` — structural slot with no PII relationship.
    """

    NONE = "none"
    PII_PARAMETRIZED = "pii_parametrized"
    PII_RESIDUAL = "pii_residual"


@dataclass
class SlotDef:
    """A single fill-in slot in a block or template. Structure only — carries no
    example value that could leak content."""

    name: str
    """Slot token as it appears in the Jinja2 body, e.g. ``party_a_name``."""

    slot_type: SlotType = SlotType.TEXT
    """Value kind (drives detection + validation)."""

    required: bool = False
    """Whether the engine must have a value before the document is complete."""

    pii_class: PIIClass = PIIClass.NONE
    """Relationship to PII in the source corpus (see :class:`PIIClass`)."""

    enum: Optional[list[str]] = None
    """Allowed values, when ``slot_type == ENUM``."""

    description: str = ""
    """Human hint. MUST NOT contain example content or real values."""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["slot_type"] = self.slot_type.value
        d["pii_class"] = self.pii_class.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotDef:
        data = dict(data)
        if "slot_type" in data:
            data["slot_type"] = SlotType(data["slot_type"])
        if "pii_class" in data:
            data["pii_class"] = PIIClass(data["pii_class"])
        return cls(**data)


@dataclass
class VariantDef:
    """A structural alternative for a block. **Text is intentionally optional and
    excluded from the public schema** — a published pack ships variant STRUCTURE
    (id, signal category, trigger) but a firm's private pack supplies the body."""

    variant_id: str
    signal_category: str = "fact_pattern"
    """One of the actionable deviation categories: fact_pattern | party_role |
    jurisdiction | citation_update | scope_expansion | scope_restriction."""

    trigger: str = ""
    """Natural-language condition describing when this variant applies."""

    confidence: str = "medium"
    count: Optional[int] = None
    """Corpus frequency, when known (metadata only)."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VariantDef:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class BlockDef:
    """Metadata for one reusable block. STRUCTURE ONLY — the body lives in a
    separate partial file inside a pack, never inline here."""

    block_id: str
    function: str
    """One of the clause-function taxonomy labels (see ``data/schema/``)."""

    subtype: str = ""
    scope: str = "universal"
    jurisdiction: str = "general"
    statutory_citation: Optional[str] = None
    triggers: str = ""
    confidence: str = "high"
    slots: list[SlotDef] = field(default_factory=list)
    partial: str = ""
    """Relative path to the block body (Jinja2 partial) within the pack."""
    variants: list[VariantDef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["slots"] = [s.to_dict() if isinstance(s, SlotDef) else s for s in self.slots]
        d["variants"] = [v.to_dict() if isinstance(v, VariantDef) else v
                         for v in self.variants]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlockDef:
        data = dict(data)
        allowed = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in data.items() if k in allowed}
        data["slots"] = [SlotDef.from_dict(s) if isinstance(s, dict) else s
                         for s in data.get("slots", [])]
        data["variants"] = [VariantDef.from_dict(v) if isinstance(v, dict) else v
                            for v in data.get("variants", [])]
        return cls(**data)


@dataclass
class BlockRef:
    """A manifest's reference to a block, with its inclusion mode + conditions.
    This is the *structure* of assembly wiring — a published manifest ships the
    empty/synthetic version; a firm's drafting judgment (which variants, which
    conditions) lives in the firm's private pack."""

    block_id: str
    mode: str = "required"  # required | add | remove | substitute
    when: Optional[str] = None
    variant_select: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlockRef:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ManifestDef:
    """A template manifest: which blocks compose a document, in order, with the
    discriminators that guard family selection. STRUCTURE ONLY."""

    template_id: str
    practice_area: str = ""
    document_type: str = ""
    title: str = ""
    description: str = ""
    context_defaults: dict[str, Any] = field(default_factory=dict)
    required_slots: list[Any] = field(default_factory=list)
    paired_forms: list[Any] = field(default_factory=list)
    blocks: list[BlockRef] = field(default_factory=list)
    discriminators: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["blocks"] = [b.to_dict() if isinstance(b, BlockRef) else b for b in self.blocks]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestDef:
        data = dict(data)
        allowed = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in data.items() if k in allowed}
        data["blocks"] = [BlockRef.from_dict(b) if isinstance(b, dict) else b
                          for b in data.get("blocks", [])]
        return cls(**data)
