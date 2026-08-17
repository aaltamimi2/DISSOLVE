"""Strict, bounded JSON contracts shared by DISSOLVE tools and the harness."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from numbers import Integral, Real
from typing import Any


def normalize_json(value: Any) -> Any:
    """Recursively coerce values to strict JSON primitives; non-finite is null."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, Mapping):
        return {str(key): normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_json(item) for item in value]
    raise TypeError(f"Unsupported tool-result value: {type(value).__name__}")


def tool_result(
    tool: str,
    *,
    success: bool,
    display: str = "",
    **data: Any,
) -> str:
    """Serialize the stable v11 tool envelope without NaN or Infinity."""
    payload = {"tool_name": tool, "success": success, **data}
    return json.dumps(
        normalize_json({"display": display, "data": payload}),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def tool_success(tool: str, *, display: str = "", **data: Any) -> str:
    return tool_result(tool, success=True, display=display, **data)


def tool_error(
    tool: str,
    message: str,
    *,
    error_code: str = "invalid_input",
    **data: Any,
) -> str:
    return tool_result(
        tool,
        success=False,
        display=message,
        error=message,
        error_code=error_code,
        **data,
    )


def parse_tool_result(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if set(parsed) != {"display", "data"} or not isinstance(parsed["data"], dict):
        raise ValueError("Invalid DISSOLVE tool envelope")
    return parsed
