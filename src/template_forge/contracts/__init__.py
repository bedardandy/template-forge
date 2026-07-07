"""template-forge data contracts.

Zero-dependency dataclasses describing document metadata, detected form fields,
classification results, and template structure (blocks / variants / slots /
manifests).

These **complement** the published ``legal-facts-schema`` package, which owns the
shared fact object (matter / parties / party / facts). template-forge does not
redefine that fact object — a pack's *facts* are validated by legal-facts-schema;
its *documents and templates* are described here. See ``docs/RELATION.md``.
"""

__version__ = "0.1.0"

from template_forge.contracts.classification import ClassificationResult
from template_forge.contracts.document import DocumentMetadata
from template_forge.contracts.form_field import DetectedField, FieldType
from template_forge.contracts.template import (
    BlockDef,
    BlockRef,
    ManifestDef,
    PIIClass,
    SlotDef,
    SlotType,
    VariantDef,
)

__all__ = [
    "DocumentMetadata",
    "DetectedField",
    "FieldType",
    "ClassificationResult",
    # template structure
    "BlockDef",
    "BlockRef",
    "ManifestDef",
    "SlotDef",
    "SlotType",
    "VariantDef",
    "PIIClass",
]
