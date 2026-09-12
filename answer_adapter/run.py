"""Question loop: offer, refuse-before-dispatch, present, normalize, stamp."""

from __future__ import annotations

import json
from typing import Callable, Mapping

from answer_adapter import config as config_mod
from answer_adapter import draft as draft_mod
from answer_adapter import ledger as ledger_mod
from answer_adapter import offer as offer_mod
from answer_adapter import prompt as prompt_mod
from answer_adapter.constants import ARMS, DEFAULT_MAX_TOOL_ROUNDS, REGISTRY_ROSTER, STAMP_KEYS


def _max_rounds(config: Mapping) -> int:
    return int(config.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS))


def _refusal_message(name: str) -> dict:
    return {"available": False, "refusal": "tool_not_offered", "name": name}


def _parse_draft(text: object) -> tuple[object, str | None]:
    try:
        parsed = json.loads(text if isinstance(text, str) else "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "envelope_unparseable"
    return parsed, None


def _assemble_envelope(stamps: Mapping, normalized: Mapping, status: str) -> dict:
    return {
        "envelope_version": stamps["envelope_version"],
        "envelope_profile": stamps["envelope_profile"],
        "request_id": stamps["request_id"],
        "request_text_hash": stamps["request_text_hash"],
        "resolved_scope": stamps["resolved_scope"],
        "corpus_snapshot": stamps["corpus_snapshot"],
        "status": status,
        "claims": normalized.get("claims") or [],
        "unanswered_subparts": normalized.get("unanswered_subparts") or [],
        "support_refs": normalized.get("support_refs") or [],
        "limitations": normalized.get("limitations") or [],
        "generated_at": stamps["generated_at"],
    }


def run_question(
    question: Mapping,
    arm: str,
    model: Callable,
    executor: Callable,
    config: Mapping,
    clock: object,
    run_ordinal: object,
) -> dict:
    offer = offer_mod.build_offer(config.get("by_name") or REGISTRY_ROSTER, arm)
    offered = list(offer["offered"])
    echo = config_mod.config_echo(arm, config)
    stamps = config_mod.stamp_values(arm, question, config, clock, run_ordinal, echo)
    spec = ARMS[arm]
    prompt_mod.load_packaged_prompt(spec["prompt"])
    tools = list(offered)
    ledger = ledger_mod.new_ledger()
    ledger_before = ledger_mod.new_ledger()
    call_records_passed = []
    presented_tool_messages = []
    executor_calls = []
    refused_tool_calls = []
    model_calls = 0
    tool_rounds = 0
    max_rounds = _max_rounds(config)
    session_bound = arm == "rag_alone"
    messages = [
        {"role": "system", "content": prompt_mod.packaged_prompt_text(spec["prompt"])},
        {"role": "user", "content": question.get("text")},
    ]
    adapter_error = None
    draft = None
    check = {
        "check_scope": "draft_profile",
        "draft_structure_valid": False,
        "draft_errors": [],
        "claim_errors": {},
        "claim_flags": {},
    }
    while True:
        response = model(messages, tools)
        model_calls += 1
        if not isinstance(response, Mapping):
            adapter_error = "envelope_unparseable"
            break
        tool_calls = list(response.get("tool_calls") or [])
        if tool_calls:
            if tool_rounds >= max_rounds:
                adapter_error = "budget_exhausted"
                break
            tool_rounds += 1
            for item in tool_calls:
                name = item.get("name") if isinstance(item, Mapping) else None
                args = item.get("args") if isinstance(item, Mapping) else {}
                if name not in offered:
                    refused_tool_calls.append({"name": name, "code": "tool_not_offered"})
                    presented_tool_messages.append(_refusal_message(name))
                    messages.append({"role": "tool", "name": name, "content": _refusal_message(name)})
                    continue
                result = executor(name, args)
                executor_calls.append(name)
                call = {"tool": name, "call_ordinal": ledger["executed_calls"] + 1}
                call_records_passed.append({"tool": name, "call_ordinal": call["call_ordinal"]})
                presented = ledger_mod.present_result(result, ledger, call)
                ledger = presented["ledger"]
                presented_tool_messages.append(presented["message"])
                messages.append({"role": "tool", "name": name, "content": presented["message"]})
            continue
        draft, parse_error = _parse_draft(response.get("text"))
        if parse_error:
            adapter_error = parse_error
            break
        check = draft_mod.check_draft(draft, spec["envelope_profile"])
        if not check["draft_structure_valid"]:
            adapter_error = "envelope_unparseable"
            break
        break

    result = {
        "model_calls": model_calls,
        "tool_rounds": tool_rounds,
        "executor_calls": executor_calls,
        "refused_tool_calls": refused_tool_calls,
        "tools_passed_to_model": tools,
        "presented_tool_messages": presented_tool_messages,
        "session_bound": session_bound,
        "executed_calls": ledger["executed_calls"],
        "ledger_before": ledger_before,
        "call_records_passed": call_records_passed,
        "ledger_after": ledger,
        "stamped_keys": list(STAMP_KEYS),
        "adapter_error": adapter_error,
        "claim_errors": check["claim_errors"],
        "claim_flags": check["claim_flags"],
        "draft_errors": check["draft_errors"],
        "draft_structure_valid": check["draft_structure_valid"],
        "discarded_draft_keys": [],
    }
    if session_bound:
        result["literature_mode"] = "corpus"
    if adapter_error:
        result["final_status"] = "operational_failure"
        result["draft_structure_valid"] = False if adapter_error in {"budget_exhausted", "envelope_unparseable"} else check["draft_structure_valid"]
        return result
    version = echo["substrate_manifest_sha256"] if arm != "closed_book" else stamps["corpus_snapshot"]["index_manifest_digest"]
    normalized = ledger_mod.normalize_draft(draft, ledger, arm, version)
    check_errors = draft_mod.unanswered_errors(draft) if isinstance(draft, Mapping) else []
    envelope = _assemble_envelope(stamps, normalized, draft.get("status") if isinstance(draft, Mapping) else "operational_failure")
    result.update(
        {
            "final_status": envelope["status"],
            "envelope": envelope,
            "discarded_draft_keys": normalized["discarded_draft_keys"],
            "draft_errors": check_errors,
            "malformed_unanswered_entries": len(check_errors),
        }
    )
    return result
