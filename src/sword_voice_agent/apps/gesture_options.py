from __future__ import annotations

import math
import os


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed
