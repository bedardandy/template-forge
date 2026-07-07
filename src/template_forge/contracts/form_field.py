"""Form field detection structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Optional


class FieldType(str, Enum):
    """Types of form fields that can be detected."""

    TEXT = "text"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SIGNATURE = "signature"
    DATE = "date"
    CURRENCY = "currency"
    DROPDOWN = "dropdown"
    TEXTAREA = "textarea"


@dataclass
class DetectedField:
    """A field detected in a form document.

    Used by field detection systems (heuristic, VLM, hybrid) to represent
    form fields that should be filled or written to AcroForms.
    """

    field_name: str
    """Internal field name (e.g., 'decedent_last_name', 'plaintiff_address_street')"""

    field_type: FieldType
    """Type of field (text, checkbox, signature, etc.)"""

    page: int
    """Page number (0-indexed)"""

    rect: tuple[float, float, float, float]
    """Bounding box as (x0, y0, x1, y1) in PDF points"""

    confidence: float = 0.5
    """Detection confidence score (0.0 to 1.0)"""

    canonical_key: Optional[str] = None
    """Canonical schema key (e.g., 'plaintiff.name.last' from case_data.SCHEMA)"""

    nearby_label: str = ""
    """Label text found near the field"""

    detection_source: str = "heuristic"
    """How this field was detected ('heuristic', 'vlm', 'merged', 'manual')"""

    group_id: Optional[str] = None
    """Group identifier for radio button groups"""

    default_value: Optional[str] = None
    """Default or suggested value"""

    options: Optional[list[str]] = None
    """Available options for dropdown/radio fields"""

    required: bool = False
    """Whether this field is required"""

    validation_pattern: Optional[str] = None
    """Regex pattern for validation"""

    metadata: Optional[dict[str, Any]] = None
    """Additional flexible metadata"""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert FieldType enum to string
        data["field_type"] = self.field_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DetectedField:
        """Create from dictionary (e.g., from JSON)."""
        # Convert field_type string back to enum
        if isinstance(data.get("field_type"), str):
            data["field_type"] = FieldType(data["field_type"])
        return cls(**data)

    @property
    def width(self) -> float:
        """Width of the bounding box."""
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        """Height of the bounding box."""
        return self.rect[3] - self.rect[1]

    @property
    def center(self) -> tuple[float, float]:
        """Center point of the bounding box."""
        return (
            (self.rect[0] + self.rect[2]) / 2,
            (self.rect[1] + self.rect[3]) / 2,
        )
