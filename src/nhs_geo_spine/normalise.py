import re

_POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$")


def normalise_postcode(value: str | None) -> tuple[str | None, str | None]:
    """Return (display postcode, compact postcode); invalid/blank -> (None, None)."""
    if value is None:
        return None, None
    compact = re.sub(r"\s+", "", value.upper().strip())
    if not compact or not _POSTCODE.match(compact):
        return None, None
    return f"{compact[:-3]} {compact[-3:]}", compact
