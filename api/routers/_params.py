"""Request-parameter parsing shared by the routers.

Validation happens here, before any SQL runs. Bad input raises HTTP 422 (the same
status FastAPI uses for its own validation errors) so clients see one consistent
error shape.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

COUNTRY_RE = re.compile(r"^[A-Z0-9_]{2,12}$")
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
MAX_COUNTRIES = 50


def parse_countries(raw: str | None) -> list[str]:
    """'PT,DE' -> ['PT', 'DE'] (deduplicated, order kept). None/empty -> []."""
    if not raw:
        return []
    codes = list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if len(codes) > MAX_COUNTRIES:
        raise HTTPException(422, f"At most {MAX_COUNTRIES} countries per request.")
    bad = [c for c in codes if not COUNTRY_RE.fullmatch(c)]
    if bad:
        raise HTTPException(
            422, f"Invalid country code(s): {', '.join(bad)}. Expected ^[A-Z0-9_]{{2,12}}$."
        )
    return codes
