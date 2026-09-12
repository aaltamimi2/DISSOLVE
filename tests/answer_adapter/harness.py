"""Load pinned fixtures by digest and drive exported adapter functions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Mapping

from answer_adapter import (
    build_offer,
    check_draft,
    config_echo,
    load_prompt,
    resolve_substrate,
    run_question,
)
from answer_adapter.halt import AdapterHalt

FIXTURE_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/fixtures/FIXTURES.adapter.v3.json")
FIXTURE_SHA256 = "9a5636e11b9ff5f85cee3a2d5ddbd8bc5b56eb4049d6ec24b364741810e2c347"


class InputDigestMismatch(Exception):
    def __init__(self, observed: str) -> None:
        super().__init__("input_digest_mismatch")
        self.halt = "input_digest_mismatch"
        self.observed = observed


def load_fixture_file(path: Path = FIXTURE_PATH, expected_sha256: str = FIXTURE_SHA256) -> dict:
    raw = Path(path).read_bytes()
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected_sha256:
        raise InputDigestMismatch(observed)
    return json.loads(raw.decode("utf-8"))


class ScriptedModel:
    def __init__(self, script: list) -> None:
        self.script = list(script or [])
        self.index = 0
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append({"tools": list(tools), "message_count": len(messages)})
        if self.index >= len(self.script):
            return {"text": "", "tool_calls": []}
        step = self.script[self.index]
        self.index += 1
        return copy.deepcopy(step)


class ScriptedExecutor:
    def __init__(self, script: list) -> None:
        self.script = list(script or [])
        self.index = 0
        self.calls = []

    def __call__(self, name, args):
        self.calls.append({"name": name, "args": copy.deepcopy(args)})
        if self.index >= len(self.script):
            return {}
        step = self.script[self.index]
        self.index += 1
        return copy.deepcopy(step)


def _halt(exc: AdapterHalt) -> dict:
    return exc.as_dict()


def run_fixture(fixture: Mapping | dict, adapter=None) -> dict:
    adapter = adapter or {
        "build_offer": build_offer,
        "load_prompt": load_prompt,
        "check_draft": check_draft,
        "config_echo": config_echo,
        "resolve_substrate": resolve_substrate,
        "run_question": run_question,
    }
    family = fixture["family"]
    inp = fixture["input"]
    try:
        if family == "F-OFFER":
            return adapter["build_offer"](inp["by_name"], inp["arm"])
        if family == "F-PROMPT":
            return adapter["load_prompt"](inp["prompt_name"], inp["text"])
        if family == "F-ENV":
            return adapter["check_draft"](inp["draft"], inp["profile"])
        if family == "F-CFG" or family == "F-IDENT":
            echo = adapter["config_echo"](inp["arm"], inp["config"])
            return {
                "valid": True,
                "echo_keys": list(echo.keys()),
                "api_key_env": echo["api_key_env"],
                "offered_tools": echo["offered_tools"],
                "tool_config_hash": echo["tool_config_hash"],
                "prompt_sha256": echo["prompt_sha256"],
                "substrate_index_sha256": echo["substrate_index_sha256"],
                "substrate_manifest_sha256": echo["substrate_manifest_sha256"],
            }
        if family == "F-SUB":
            return adapter["resolve_substrate"](inp["substrate_id"], inp["research_home"])
        model = ScriptedModel(inp.get("model_script") or [])
        executor = ScriptedExecutor(inp.get("executor_script") or [])
        return adapter["run_question"](
            inp["question"],
            inp["arm"],
            model,
            executor,
            inp["config"],
            inp.get("clock"),
            inp.get("run_ordinal"),
        )
    except AdapterHalt as exc:
        return _halt(exc)


def graded_expected(expected: dict) -> dict:
    return {k: v for k, v in expected.items() if k != "note" and not str(k).startswith("note_")}


def values_equal(a, b) -> bool:
    return a == b


def compare_fixture(fixture: dict, actual: dict) -> dict:
    expected = graded_expected(fixture["expected"])
    missed = []
    matched = []
    for key, value in expected.items():
        if key not in actual or not values_equal(actual[key], value):
            missed.append(key)
        else:
            matched.append(key)
    return {
        "fixture_id": fixture["fixture_id"],
        "family": fixture["family"],
        "matched": len(missed) == 0,
        "matched_keys": len(matched),
        "missed_keys": missed,
        "graded_keys": len(expected),
    }


def _tokens(path: str) -> list[str]:
    tokens = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if buf:
                tokens.append(buf)
                buf = ""
            i += 1
            continue
        if ch == "[":
            if buf:
                tokens.append(buf)
                buf = ""
            j = path.index("]", i)
            tokens.append(path[i : j + 1])
            i = j + 1
            continue
        buf += ch
        i += 1
    if buf:
        tokens.append(buf)
    return tokens


def get_path(obj, path: str):
    if path == "":
        return obj

    def rec(cur, toks):
        if not toks:
            return cur
        tok, rest = toks[0], toks[1:]
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1]
            if inner == "*":
                if not isinstance(cur, list):
                    return None
                return [rec(item, rest) for item in cur]
            if ".." in inner:
                lo, hi = inner.split("..", 1)
                return [rec(item, rest) for item in cur[int(lo) : int(hi) + 1]]
            return rec(cur[int(inner)], rest)
        if not isinstance(cur, dict) or tok not in cur:
            return None
        return rec(cur[tok], rest)

    return rec(obj, _tokens(path))


def activating_value(obj, key: str):
    if key in obj and "[" not in key:
        return obj[key]
    return get_path(obj, key)
