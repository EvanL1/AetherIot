"""Minimal local fallback for RFC 8785-style canonical JSON bytes."""

from __future__ import annotations

import math
import json
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalize(child) for child in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not permitted")
        if value == 0.0:
            return 0
        if value.is_integer():
            return int(value)
    return value


def dumps(value: Any) -> bytes:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
