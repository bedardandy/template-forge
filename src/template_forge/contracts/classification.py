"""Document classification structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class ClassificationResult:
    """Result from document classification via VLM or other classifier.

    Used by documentclassifier and consumed by other services to understand
    what type of document they're dealing with.
    """

    doc_type: str
    """Classified document type (e.g., 'probate_form', 'civil_complaint', 'invoice')"""

    confidence: float
    """Classification confidence score (0.0 to 1.0)"""

    model: Optional[str] = None
    """Model used for classification (e.g., 'qwen2.5-vl-7b', 'gpt-4-vision')"""

    categories_considered: list[str] = field(default_factory=list)
    """List of categories that were considered during classification"""

    extracted_fields: Optional[dict[str, Any]] = None
    """Key-value pairs extracted during classification (e.g., form ID, title)"""

    reasoning: Optional[str] = None
    """Explanation of why this classification was chosen (if model provides it)"""

    alternative_types: Optional[list[tuple[str, float]]] = None
    """Alternative classifications with their confidence scores [(type, confidence), ...]"""

    page_classifications: Optional[list[str]] = None
    """Per-page classifications if document is multi-page"""

    language: Optional[str] = None
    """Detected language code (e.g., 'en', 'es')"""

    timestamp: Optional[str] = None
    """ISO 8601 timestamp when classification was performed"""

    processing_time_ms: Optional[float] = None
    """Time taken to classify in milliseconds"""

    metadata: Optional[dict[str, Any]] = None
    """Additional flexible metadata"""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassificationResult:
        """Create from dictionary (e.g., from JSON)."""
        return cls(**data)

    def is_confident(self, threshold: float = 0.8) -> bool:
        """Check if confidence exceeds threshold."""
        return self.confidence >= threshold
