"""Config echo, identity checks, and harness stamps."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from answer_adapter.constants import (
    ADAPTER_VERSION,
    ARMS,
    CLOSED_BOOK_SENTINEL,
    CONFIG_ECHO_FROM_CONFIG,
    CONFIG_ECHO_KEYS,
    DEFAULT_MAX_TOOL_ROUNDS,
    KEY_MATERIAL_FIELDS,
    PINNED_CENSUS_VERSION,
    PINNED_MODEL_ID,
    PINNED_STORE_DIGEST,
    PRESENTATION_ID,
    STAMP_KEYS,
)
from answer_adapter.halt import AdapterHalt
from answer_adapter import offer as offer_mod
from answer_adapter.prompt import load_packaged_prompt
from answer_adapter.constants import PROMPT_SHA256


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def manifest_file_digest(manifest: Mapping) -> str:
    return hashlib.sha256(_canonical_json(dict(manifest)).encode("utf-8")).hexdigest()


def _key_material_field(config: Mapping) -> str | None:
    for key in config.keys():
        if str(key) in KEY_MATERIAL_FIELDS:
            return str(key)
    return None


def config_echo(arm: str, config: Mapping) -> dict:
    field = _key_material_field(config)
    if field is not None:
        raise AdapterHalt("key_material_in_config", field=field, value_echoed=False)
    missing = [k for k in CONFIG_ECHO_FROM_CONFIG if k not in config]
    if missing:
        raise AdapterHalt("config_incomplete", missing=missing)
    offer = offer_mod.build_offer(offer_mod.roster_from_config(config), arm)
    spec = ARMS[arm]
    load_packaged_prompt(spec["prompt"])
    if "substrate_manifest" not in config:
        raise AdapterHalt("manifest_digest_mismatch")
    computed_manifest = manifest_file_digest(config["substrate_manifest"])
    configured_manifest = config.get("substrate_manifest_sha256")
    if configured_manifest != computed_manifest:
        raise AdapterHalt("manifest_digest_mismatch")
    manifest = config["substrate_manifest"]
    if config.get("substrate_index_sha256") != manifest.get("index_sha256"):
        raise AdapterHalt("manifest_index_mismatch")
    echo = {
        "adapter_version": config.get("adapter_version", ADAPTER_VERSION),
        "api_base": config["api_base"],
        "api_key_env": config["api_key_env"],
        "arm": arm,
        "census_version": config["census_version"],
        "decoding": config["decoding"],
        "envelope_profile": spec["envelope_profile"],
        "max_tool_rounds": config["max_tool_rounds"],
        "model_alias": config["model_alias"],
        "model_id": config["model_id"],
        "offered_tools": list(offer["offered"]),
        "presentation": config.get("presentation", PRESENTATION_ID),
        "prompt_name": spec["prompt"],
        "prompt_sha256": PROMPT_SHA256[spec["prompt"]],
        "research_home": config["research_home"],
        "store_digest": config["store_digest"],
        "substrate_id": config["substrate_id"],
        "substrate_index_sha256": config["substrate_index_sha256"],
        "substrate_manifest_sha256": configured_manifest,
        "tool_config_hash": offer_mod.tool_config_hash(
            arm, offer["offered"], config.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
        ),
    }
    return {k: echo[k] for k in CONFIG_ECHO_KEYS}


def _missing_stamp(field: str) -> None:
    raise AdapterHalt("stamp_value_missing", field=field)


def _mismatch_stamp(field: str) -> None:
    raise AdapterHalt("stamp_value_mismatch", field=field)


def resolved_scope_from_question(question: Mapping) -> dict:
    scope = question.get("resolved_scope")
    if not isinstance(scope, Mapping):
        _missing_stamp("resolved_scope")
    out = {
        "materials": list(scope.get("materials", [])),
        "quantities": list(scope.get("quantities", [])),
        "conditions": list(scope["conditions"]) if "conditions" in scope else [],
    }
    if "papers_filter" in scope:
        out["papers_filter"] = scope["papers_filter"]
    return out


def stamp_values(arm: str, question: Mapping, config: Mapping, clock: object, run_ordinal: object, echo: Mapping) -> dict:
    spec = ARMS[arm]
    question_id = question.get("question_id")
    if question_id in (None, ""):
        _missing_stamp("request_id")
    if run_ordinal in (None, ""):
        _missing_stamp("request_id")
    text = question.get("text")
    if text is None:
        _missing_stamp("request_text_hash")
    if clock in (None, ""):
        _missing_stamp("generated_at")
    generated_at = clock() if callable(clock) else clock
    if generated_at in (None, ""):
        _missing_stamp("generated_at")
    if config.get("model_id") != PINNED_MODEL_ID:
        _mismatch_stamp("model_id")
    if arm == "closed_book":
        snapshot = {
            "census_version": CLOSED_BOOK_SENTINEL,
            "index_manifest_digest": CLOSED_BOOK_SENTINEL,
            "model_id": PINNED_MODEL_ID,
            "prompt_hash": echo["prompt_sha256"],
            "store_digest": CLOSED_BOOK_SENTINEL,
            "tool_config_hash": echo["tool_config_hash"],
        }
    else:
        if config.get("store_digest") != PINNED_STORE_DIGEST:
            _mismatch_stamp("store_digest")
        if config.get("census_version") != PINNED_CENSUS_VERSION:
            _mismatch_stamp("census_version")
        snapshot = {
            "census_version": PINNED_CENSUS_VERSION,
            "index_manifest_digest": echo["substrate_manifest_sha256"],
            "model_id": PINNED_MODEL_ID,
            "prompt_hash": echo["prompt_sha256"],
            "store_digest": PINNED_STORE_DIGEST,
            "tool_config_hash": echo["tool_config_hash"],
        }
    return {
        "envelope_version": "AdapterEnvelope.v1",
        "envelope_profile": spec["envelope_profile"],
        "request_id": f"{question_id}:{arm}:r{run_ordinal}",
        "request_text_hash": hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
        "resolved_scope": resolved_scope_from_question(question),
        "generated_at": generated_at,
        "corpus_snapshot": snapshot,
    }


def stamped_key_names() -> list[str]:
    return list(STAMP_KEYS)
