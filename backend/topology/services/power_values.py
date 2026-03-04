from __future__ import annotations

from typing import Optional


SENTINEL_ZERO_EPSILON = 1e-6
SENTINEL_NEG40_EPSILON = 1e-3
VALID_POWER_MAX_DBM = 0.0
VALID_POWER_MIN_DBM = -40.0


def to_float_or_none(value) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalize_power_value(value) -> Optional[float]:
    """
    Normalize optical power readings and discard invalid values.

    Accepted range is strictly:
    -40 dBm < value < 0 dBm
    """
    numeric = to_float_or_none(value)
    if numeric is None:
        return None
    if abs(numeric) <= SENTINEL_ZERO_EPSILON:
        return None
    if abs(numeric + 40.0) <= SENTINEL_NEG40_EPSILON:
        return None
    if numeric <= VALID_POWER_MIN_DBM:
        return None
    if numeric >= VALID_POWER_MAX_DBM:
        return None
    return numeric
