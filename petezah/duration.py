import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_NAMES = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(duration_str: str | None) -> tuple[int | None, str | None]:
    if not duration_str:
        return None, None
    raw = duration_str.lower().strip()
    match = re.match(r"^(\d+)(s|m|h|d)?$", raw)
    if not match:
        return None, "Invalid duration. Use formats like 30s, 10m, 2h, 5d."
    amount = int(match.group(1))
    unit = match.group(2) or "m"
    if amount <= 0:
        return None, "Duration must be greater than zero."
    seconds = amount * _UNITS[unit]
    label = f"{amount} {_NAMES[unit]}"
    return seconds, label
