"""§3.2.0(d) canonical JSON and collision detection."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from answer_eval.errors import CanonicalizationCollision


def nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def fold_ws(value: str) -> str:
    return " ".join(nfkc(value).casefold().split())


def label_key(value: str) -> str:
    return "".join(nfkc(value).casefold().split())


def _check_key_collisions(obj: dict[str, Any]) -> None:
    seen: dict[str, str] = {}
    for key in obj:
        if not isinstance(key, str):
            continue
        canon = nfkc(key)
        prior = seen.get(canon)
        if prior is not None and prior != key:
            raise CanonicalizationCollision("distinct keys normalize to one form")
        seen[canon] = key


def _check_string_value_collisions(obj: dict[str, Any]) -> None:
    for key, value in obj.items():
        if not isinstance(value, list):
            continue
        strings = [v for v in value if isinstance(v, str)]
        seen: dict[str, str] = {}
        for item in strings:
            canon = nfkc(item)
            prior = seen.get(canon)
            if prior is not None and prior != item:
                raise CanonicalizationCollision(
                    "distinct string values of one field normalize to one form"
                )
            seen[canon] = item


def _walk_collisions(obj: Any) -> None:
    if isinstance(obj, dict):
        _check_key_collisions(obj)
        _check_string_value_collisions(obj)
        for value in obj.values():
            _walk_collisions(value)
    elif isinstance(obj, list):
        for item in obj:
            _walk_collisions(item)


def _prep(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if value is None:
                continue
            ck = nfkc(key) if isinstance(key, str) else key
            out[ck] = _prep(value)
        return out
    if isinstance(obj, list):
        return [_prep(v) for v in obj]
    if isinstance(obj, str):
        return nfkc(obj)
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, int):
        return obj
    return obj


def canonical_dumps(obj: Any, *, check: bool = True) -> str:
    if check:
        _walk_collisions(obj)
    prepared = _prep(obj)
    return json.dumps(prepared, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonicalize(obj: Any) -> Any:
    """Return a JSON-round-tripped canonical object; raises on collision."""
    text = canonical_dumps(obj, check=True)
    return json.loads(text)


def pointer_escape(segment: str) -> str:
    return nfkc(str(segment)).replace("~", "~0").replace("/", "~1")


def json_pointer(parts: list[Any]) -> str:
    if not parts:
        return ""
    return "/" + "/".join(pointer_escape(p) for p in parts)
