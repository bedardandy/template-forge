"""Template-pack path resolution for the assembly engine.

The engine operates over a *template pack*: a directory tree of the shape ::

    <pack>/
      manifests/                 one <template_id>.json per template
      blocks/blocks.jsonl        block metadata + variant records
      blocks/partials/*.md.j2    block bodies as Jinja2 partials
      facts/fact_keys.json       the fact/slot registry

The framework ships *no* firm content. A pack is either:

* the bundled synthetic ``example_pack/`` (used for demos + tests), or
* your own private pack, selected via the ``TEMPLATE_FORGE_PACK`` environment
  variable or the ``--pack`` CLI flag.

This is the multi-tenant seam: the public engine is firm-agnostic; a firm points
it at a private pack that never enters this repository. See ``docs/OPERATING.md``.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENGINE_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _ENGINE_DIR.parent
# The bundled, fully synthetic demo pack (fictional data only).
DEFAULT_PACK = _PACKAGE_ROOT / "example_pack"


def pack_root() -> Path:
    """Resolve the active template pack.

    Precedence: ``TEMPLATE_FORGE_PACK`` env var, else the bundled ``example_pack``.
    A firm sets the env var to its private pack directory; nothing about that
    pack is baked into this package.
    """
    env = os.environ.get("TEMPLATE_FORGE_PACK")
    return Path(env).expanduser().resolve() if env else DEFAULT_PACK


def set_pack(path: str | os.PathLike[str]) -> None:
    """Point the engine at a specific pack for the current process (used by the
    ``--pack`` CLI flag)."""
    os.environ["TEMPLATE_FORGE_PACK"] = str(Path(path).expanduser().resolve())


def manifests_dir() -> Path:
    return pack_root() / "manifests"


def blocks_jsonl() -> Path:
    return pack_root() / "blocks" / "blocks.jsonl"


def partials_dir() -> Path:
    return pack_root() / "blocks" / "partials"


def fact_keys_path() -> Path:
    return pack_root() / "facts" / "fact_keys.json"
