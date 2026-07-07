"""Jurisdiction hook: pull citations + a counting profile from a jurisdiction module.

Template-forge's citation tooling and jurisdiction profile were historically
Maine-flavored, backed by the bundled reference table at ``data/citations/authority.jsonl``.
This module adds an *additive* seam so the same tooling can instead source citations
(and a counting/service profile + slot vocabulary) from a published
**jurisdiction module** — ``jurisdiction-maine`` (``US-ME``), ``jurisdiction-federal``
(``US``), or any other registered with the ``legal_jurisdictions`` contract — when one
is installed for the requested code.

Design constraints (kept deliberately small):

* **Additive, never breaking.** ``legal_jurisdictions`` is an OPTIONAL dependency. If it
  is not installed, or no module is registered for the requested code, or the requested
  module is an unpopulated scaffold, the resolver falls back to the bundled
  ``data/citations/authority.jsonl`` exactly as before. No jurisdiction argument at all
  → bundled table, unchanged behavior.
* **Never silent-wrong law.** A module that IS installed for the code but reports
  ``is_populated == False`` (a ``TO_RESEARCH`` scaffold, the New-Hampshire shape) is
  refused — :func:`resolve_authority_records` raises unless ``strict=False`` is passed to
  opt into the bundled fallback. The engine never presents an empty scaffold as if it
  were verified authority.
* The bundled table stays canonical for the demo/example pack; ``jurisdiction-maine`` /
  ``jurisdiction-federal`` are the canonical per-jurisdiction sources going forward
  (see ``data/citations/README.md``).

Packaging note: the bundled reference table lives at the repo-root ``data/citations/``
tree (resolved via the same pack-root convention the citation tools use), which is not
shipped inside the wheel. :func:`bundled_authority_path` therefore searches both the
repo-root path and the active pack's ``citations/`` dir, and raises
:class:`BundledAuthorityMissingError` with an actionable message rather than a bare
``FileNotFoundError`` if neither is present — so an installed-wheel consumer without a
jurisdiction module gets a clear diagnostic, not a mystery crash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "bundled_authority_path",
    "load_bundled_authority",
    "module_available",
    "resolve_authority_records",
    "jurisdiction_profile",
    "jurisdiction_slots",
    "NotPopulatedJurisdictionError",
    "BundledAuthorityMissingError",
]

# The bundled reference table lives at repo-root ``data/citations/`` in a source
# checkout. The existing citation tools also resolve a pack-local ``<pack>/citations/``
# copy. We search several candidate locations so the fallback works whether imported
# from the src tree, an editable install, or pointed at a private pack — and raise a
# clear error (never a bare FileNotFoundError) if none is found.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # …/template_forge
_REPO_ROOT = _PACKAGE_ROOT.parent.parent  # …/<repo>  (src/template_forge → repo)


class NotPopulatedJurisdictionError(RuntimeError):
    """A jurisdiction module is installed for the code but is an unpopulated scaffold.

    Mirrors the ``legal_jurisdictions`` not-verified-law doctrine: an unpopulated
    module must refuse use rather than be presented as verified authority."""


class BundledAuthorityMissingError(RuntimeError):
    """The bundled reference table could not be located in any known location.

    Raised (loudly, not a bare ``FileNotFoundError``) so a fallback that cannot find
    its data fails with an actionable message — set ``TEMPLATE_FORGE_PACK`` to a pack
    that ships ``citations/authority.jsonl``, or install a jurisdiction module."""


def _candidate_authority_paths() -> list[Path]:
    candidates: list[Path] = [_REPO_ROOT / "data" / "citations" / "authority.jsonl"]
    # A private pack may ship its own citations/ dir (how the citation tools resolve it).
    try:
        from template_forge.engine import paths as _paths

        candidates.append(_paths.pack_root() / "citations" / "authority.jsonl")
    except Exception:  # noqa: BLE001 - pack resolution is best-effort here
        pass
    return candidates


def bundled_authority_path() -> Path:
    """Path to the bundled Maine/federal reference table.

    Returns the first existing candidate (repo-root ``data/citations/`` or the active
    pack's ``citations/``). Raises :class:`BundledAuthorityMissingError` if none exist,
    so callers get a clear diagnostic instead of a bare file error."""
    candidates = _candidate_authority_paths()
    for path in candidates:
        if path.is_file():
            return path
    raise BundledAuthorityMissingError(
        "bundled citation reference table not found. Looked in: "
        + ", ".join(str(p) for p in candidates)
        + ". Point TEMPLATE_FORGE_PACK at a pack that ships citations/authority.jsonl, "
        "or install a jurisdiction module (legal_jurisdictions) for the requested code."
    )


def load_bundled_authority() -> list[dict]:
    """Load the bundled ``authority.jsonl`` reference table (the historical source)."""
    path = bundled_authority_path()
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_module(jurisdiction: Any):
    """Return a JurisdictionModule for ``jurisdiction`` (a code str or a module object),
    or None if ``legal_jurisdictions`` is not installed / no module is registered."""
    # Already a JurisdictionModule-shaped object? (duck-typed to avoid a hard import.)
    if hasattr(jurisdiction, "is_populated") and hasattr(jurisdiction, "citations"):
        return jurisdiction
    if not isinstance(jurisdiction, str):
        return None
    try:
        from legal_jurisdictions import load as _lj_load
        from legal_jurisdictions.registry import UnknownJurisdictionError
    except ImportError:
        return None
    try:
        return _lj_load(jurisdiction)
    except UnknownJurisdictionError:
        return None


def module_available(jurisdiction: Any) -> bool:
    """True iff a POPULATED jurisdiction module is resolvable for ``jurisdiction``."""
    module = _load_module(jurisdiction)
    return bool(module is not None and module.is_populated)


def resolve_authority_records(
    jurisdiction: Any = None,
    *,
    strict: bool = True,
) -> list[dict]:
    """Resolve statutory-citation records for ``jurisdiction``.

    * ``jurisdiction is None`` → the bundled table (historical default, unchanged).
    * a code/module with a POPULATED module installed → that module's citation records
      (normalized to the bundled record shape: ``citation``/``current_form``/``status``/
      ``source_url``/``verified_on``/``note``).
    * a code/module with NO module installed → the bundled table (graceful fallback).
    * a code/module whose module IS installed but UNPOPULATED → raises
      :class:`NotPopulatedJurisdictionError` when ``strict`` (default); with
      ``strict=False`` falls back to the bundled table.
    """
    if jurisdiction is None:
        return load_bundled_authority()
    module = _load_module(jurisdiction)
    if module is None:
        return load_bundled_authority()  # not installed → bundled fallback
    if not module.is_populated:
        if strict:
            raise NotPopulatedJurisdictionError(
                f"jurisdiction module {getattr(module, 'code', jurisdiction)!r} is an "
                "unpopulated scaffold (TO_RESEARCH); refusing to present it as verified "
                "authority. Populate it, pass a different code, or set strict=False to "
                "fall back to the bundled reference table."
            )
        return load_bundled_authority()
    return [_normalize_record(rec) for rec in module.citations]


def _normalize_record(rec: dict) -> dict:
    """Map a module citation record onto the bundled table's field names.

    Module records use ``cite``; the bundled table uses ``citation``. Keep every field
    and add the bundled aliases so downstream tooling reads either source uniformly."""
    out = dict(rec)
    if "citation" not in out and "cite" in out:
        out["citation"] = out["cite"]
    out.setdefault("status", "current")
    out.setdefault("current_form", out.get("citation"))
    return out


def jurisdiction_profile(jurisdiction: Any) -> dict | None:
    """Return the module's counting/service profile as a plain dict, or None if no
    populated module is installed for ``jurisdiction``.

    Shape (from ``legal_jurisdictions`` ``CountingProfile`` + ``ServiceRules``)::

        {"code", "name", "count_model", "short_period_threshold",
         "service_additions": {method: days}, "verified_as_of"}

    This is the per-jurisdiction replacement for the engine's hardcoded
    ``jurisdiction == "ME"`` default: template assembly can key block choices off the
    resolved profile's ``code`` instead of a baked-in string."""
    module = _load_module(jurisdiction)
    if module is None or not module.is_populated:
        return None
    profile = module.profile
    service = module.service_rules
    return {
        "code": module.code,
        "name": module.name,
        "kind": module.kind,
        "count_model": profile.count_model,
        "short_period_threshold": profile.short_period_threshold,
        "service_additions": {
            method: entry.get("days")
            for method, entry in service.additions.items()
        },
        "verified_as_of": module.verified_as_of,
    }


def jurisdiction_slots(jurisdiction: Any) -> dict:
    """Return the module's slot/vocabulary overrides as a plain dict (empty if none/
    not installed). Optional provider — an empty dict is a valid populated result."""
    module = _load_module(jurisdiction)
    if module is None or not module.is_populated:
        return {}
    return dict(getattr(module.slots, "data", {}) or {})
