#!/usr/bin/env python3
"""Synthetic matter / fixture generator.

Produces fictional slot data for driving the assembly engine end-to-end with
**zero real bytes**. Follows the fiction-reserved conventions used by public
synthetic legal-data generators:

* names          — fictional Doe / Roe / Poe / Public families only
* phones         — ``555-01xx`` (the fiction-reserved exchange)
* emails         — ``@example.com``
* SSNs           — ``900-xx-xxxx`` (900-series is never issued)
* recording refs — ``Book 100, Page 50``-style never-real coordinates
* addresses      — fictional streets in "Anytown"

Deterministic given a seed, so fixtures are reproducible. No network, no PII, no
firm content.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# Fiction-only name pools. Surnames are the classic placeholder set (Doe/Roe/
# Poe/Public/Coe/Loe) — deliberately NOT plausibly-real surnames.
_FIRST = ["Jamie", "Robin", "Alex", "Pat", "Casey", "Jordan", "Morgan", "Riley",
          "Quinn", "Avery", "Sam", "Drew", "Blake", "Reese", "Dana", "Lee"]
_MIDDLE = list("ABCDEFGHJKLMNPQRTVW")
_LAST = ["Doe", "Roe", "Poe", "Public", "Coe", "Loe", "Noe", "Moe"]

_ENTITY = ["Acme Holdings, LLC", "Widget Works, Inc.", "Example Trust",
           "Placeholder Cooperative", "Sample Ventures LLC",
           "Fictional Estates, LLC", "Anytown Condominium Association"]

_STREETS = ["Main Street", "Example Avenue", "Placeholder Road", "Sample Lane",
            "Fiction Way", "Anytown Boulevard"]

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _person(rng: random.Random) -> str:
    return f"{rng.choice(_FIRST)} {rng.choice(_MIDDLE)}. {rng.choice(_LAST)}"


def _entity(rng: random.Random) -> str:
    return rng.choice(_ENTITY)


def _phone(rng: random.Random) -> str:
    # 555-01xx fiction-reserved range
    return f"(207) 555-01{rng.randint(0, 99):02d}"


def _email(rng: random.Random, name: str) -> str:
    handle = name.split()[0].lower()
    return f"{handle}@example.com"


def _ssn(rng: random.Random) -> str:
    # 900-series is never a valid issued SSN
    return f"900-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"


def _address(rng: random.Random) -> dict:
    return {
        "street": f"{rng.randint(1, 999)} {rng.choice(_STREETS)}",
        "city": "Anytown",
        "state": "Maine",
        "zip": f"0{rng.randint(3900, 4999)}",
    }


def _date(rng: random.Random) -> str:
    return f"{rng.choice(_MONTHS)} {rng.randint(1, 28)}, {rng.randint(2020, 2026)}"


def _registry_ref(rng: random.Random) -> str:
    return f"Book {rng.randint(100, 999)}, Page {rng.randint(1, 500)}"


def generate_matter(seed: int = 0) -> dict:
    """Generate one fully synthetic matter as a flat slot dict, suitable as a
    fixture for the example manifests."""
    rng = random.Random(seed)
    a = _person(rng)
    b = _person(rng)
    return {
        "party_a_name": a,
        "party_b_name": b,
        "party_a_label": "First Party",
        "party_b_label": "Second Party",
        "party_a_email": _email(rng, a),
        "party_a_phone": _phone(rng),
        "party_a_ssn": _ssn(rng),
        "county": rng.choice(["Cumberland", "York", "Penobscot", "Kennebec"]),
        "state": "Maine",
        "governing_state": "Maine",
        "execution_date": _date(rng),
        "amount": f"${rng.randint(1, 500) * 1000:,}",
        "registry_ref": _registry_ref(rng),
        "entity_name": _entity(rng),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic matter fixtures")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-dir", help="write one <seed>.json per matter here")
    a = ap.parse_args()
    if a.out_dir:
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for i in range(a.count):
            m = generate_matter(seed=a.seed + i)
            (out / f"matter_{a.seed + i:04d}.json").write_text(
                json.dumps(m, indent=2) + "\n")
        print(f"wrote {a.count} synthetic matters to {out}")
    else:
        for i in range(a.count):
            print(json.dumps(generate_matter(seed=a.seed + i), indent=2))


if __name__ == "__main__":
    main()
