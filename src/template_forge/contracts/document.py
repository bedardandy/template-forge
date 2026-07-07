"""Document metadata structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class DocumentMetadata:
    """Metadata for a legal document.

    Used across the ecosystem to track documents as they move through
    the pipeline (scraping -> classification -> field detection -> filling).
    """

    id: str
    """Unique document identifier (e.g., form ID like 'DE-101' or UUID)"""

    source: str
    """Where this document came from (e.g., 'maine_courts', 'uploaded', 'scraped')"""

    doc_type: str
    """Document classification (e.g., 'probate_form', 'civil_complaint', 'motion')"""

    page_count: int
    """Number of pages in the document"""

    filename: Optional[str] = None
    """Original filename if available"""

    category: Optional[str] = None
    """Category or subcategory (e.g., 'probate', 'family', 'civil')"""

    title: Optional[str] = None
    """Human-readable document title"""

    version: Optional[str] = None
    """Form version if applicable"""

    url: Optional[str] = None
    """Source URL if scraped from web"""

    file_path: Optional[str] = None
    """Local file system path"""

    file_size: Optional[int] = None
    """File size in bytes"""

    created_at: Optional[str] = None
    """ISO 8601 timestamp when document was created/scraped"""

    updated_at: Optional[str] = None
    """ISO 8601 timestamp when document was last updated"""

    metadata: Optional[dict[str, Any]] = None
    """Additional flexible metadata as key-value pairs"""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentMetadata:
        """Create from dictionary (e.g., from JSON)."""
        return cls(**data)
