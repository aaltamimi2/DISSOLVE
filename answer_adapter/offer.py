"""Per-arm tool offer and registry-roster check."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

from answer_adapter.constants import (
    ARMS,
    DEFAULT_MAX_TOOL_ROUNDS,
    DEFERRED_ARMS,
    REGISTRY_ROSTER,
)
from answer_adapter.halt import AdapterHalt


def roster_from_config(config: Mapping | None) -> object:
    if isinstance(config, Mapping) and "by_name" in config:
        return config["by_name"]
    return REGISTRY_ROSTER


def _name_set(by_name: object) -> set[str]:
    if isinstance(by_name, Mapping):
        return {str(k) for k in by_name.keys()}
    if isinstance(by_name, (list, tuple, set, frozenset)):
        return {str(n) for n in by_name}
    raise AdapterHalt("registry_drift", extra=[], missing=list(REGISTRY_ROSTER))


def tool_config_hash(arm: str, offered: Iterable[str], max_tool_rounds: int) -> str:
    payload = {
        "arm": arm,
        "max_tool_rounds": max_tool_rounds,
        "offered": sorted(offered),
        "result_read": False,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_offer(by_name: object, arm: str) -> dict:
    if arm in DEFERRED_ARMS:
        raise AdapterHalt("arm_deferred")
    if arm not in ARMS:
        raise AdapterHalt("arm_unknown")
    names = _name_set(by_name)
    roster = set(REGISTRY_ROSTER)
    extra = sorted(names - roster)
    missing = sorted(roster - names)
    if extra or missing:
        raise AdapterHalt("registry_drift", extra=extra, missing=missing)
    spec = ARMS[arm]
    offered = list(spec["offered"])
    return {
        "offered": offered,
        "result_read_offered": False,
        "tool_config_hash": tool_config_hash(arm, offered, DEFAULT_MAX_TOOL_ROUNDS),
    }
