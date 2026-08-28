import json, os, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dissolve.session import CompactionBudgetError, bind_tool_session, compact_messages, context_window, open_turn_record
from agent_tools import SYSTEM_PROMPT, dispatch, tool_schemas

@dataclass(frozen=True)
class ToolEvent:
    name: str
    args: dict
    result: dict

@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: str
    tool_trace: list[ToolEvent]
    turn_record: str
    tool_rounds: int = 0
    usage: dict | None = None

def _oai_msgs(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            tcs = [{"id": c["id"], "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}
                   for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": tcs})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": m.get("content") or ""})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out

def _ant_msgs(messages):
    sys, rest = "", []
    for m in messages:
        if m["role"] == "system":
            sys = m.get("content") or ""
        elif m["role"] == "assistant":
            blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("args") or {}}
                for c in m.get("tool_calls") or []
            ]
            rest.append({"role": "assistant", "content": blocks or ""})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": m.get("content") or ""}
            if rest and rest[-1]["role"] == "user" and isinstance(rest[-1]["content"], list):
                rest[-1]["content"].append(block)
            else:
                rest.append({"role": "user", "content": [block]})
        else:
            rest.append({"role": "user", "content": m.get("content") or ""})
    return sys, rest

class MissingProviderKey(Exception): pass

def _usage(kind, resp):
    raw = getattr(resp, "usage_metadata" if kind == "google_genai" else "usage", None)
    if raw is None:
        return None
    names = {
        "anthropic": ("input_tokens", "output_tokens", None),
        "google_genai": ("prompt_token_count", "candidates_token_count", "total_token_count"),
    }.get(kind, ("prompt_tokens", "completion_tokens", "total_tokens"))
    input_name, output_name, total_name = names
    inp = getattr(raw, input_name, None)
    outp = getattr(raw, output_name, None)
    tot = getattr(raw, total_name, None) if total_name else None
    usage = {}
    if inp is not None:
        usage["input_tokens"] = int(inp)
    if outp is not None:
        usage["output_tokens"] = int(outp)
    if tot is not None:
        usage["total_tokens"] = int(tot)
    if (
        kind == "anthropic"
        and "total_tokens" not in usage
        and "input_tokens" in usage
        and "output_tokens" in usage
    ):
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage or None

def _fold_usage(acc):
    if not acc or any(item is None for item in acc):
        return None
    keys = {key for item in acc for key in item}
    return {key: sum(item[key] for item in acc if key in item) for key in keys}

def complete(messages, tools, *, model, api_base=None, api_key_env=None):
    kind, _, ident = model.partition(":")
    ident = ident or model
    if kind not in ("anthropic", "google_genai", "openai"):
        raise ValueError(f"unknown model prefix: {kind!r}")
    key = (os.environ.get(api_key_env) or "").strip() if api_key_env else None
    if api_key_env and not key:
        raise MissingProviderKey(f"missing environment variable {api_key_env}")
    if kind == "anthropic":
        import anthropic
        sys, rest = _ant_msgs(messages)
        ant = [{"name": t["name"], "description": t.get("description") or "", "input_schema": t["parameters"]} for t in tools]
        resp = anthropic.Anthropic(api_key=key).messages.create(
            model=ident, system=sys, messages=rest, tools=ant, max_tokens=8192)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        calls = [{"id": b.id, "name": b.name, "args": dict(b.input or {})}
                 for b in resp.content if getattr(b, "type", "") == "tool_use"]
        return {"text": text, "tool_calls": calls, "usage": _usage(kind, resp)}
    if kind == "google_genai":
        from google import genai
        from google.genai import types
        decls = [types.FunctionDeclaration(name=t["name"], description=t.get("description") or "", parameters=t["parameters"]) for t in tools]
        sys = next((m.get("content") or "" for m in messages if m["role"] == "system"), "")
        resp = genai.Client(api_key=key).models.generate_content(
            model=ident, contents=_gen_contents(messages),
            config=types.GenerateContentConfig(system_instruction=sys, tools=[types.Tool(function_declarations=decls)]))
        calls = [{"id": getattr(c, "id", "") or "", "name": c.name, "args": dict(c.args or {})}
                 for c in (getattr(resp, "function_calls", None) or [])]
        return {"text": getattr(resp, "text", None) or "", "tool_calls": calls, "usage": _usage(kind, resp)}
    if kind == "openai":
        from openai import OpenAI
        oai = [{"type": "function", "function": {"name": t["name"], "description": t.get("description") or "", "parameters": t["parameters"]}} for t in tools]
        resp = OpenAI(api_key=key or None, base_url=api_base or None).chat.completions.create(
            model=ident, messages=_oai_msgs(messages), tools=oai)
        msg = resp.choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": c.id, "name": c.function.name, "args": args})
        return {"text": msg.content or "", "tool_calls": calls, "usage": _usage(kind, resp)}

def _gen_contents(messages):
    from google.genai import types
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "assistant":
            parts = []
            if m.get("content"):
                parts.append(types.Part.from_text(text=m["content"]))
            for c in m.get("tool_calls") or []:
                parts.append(types.Part.from_function_call(name=c["name"], args=c.get("args") or {}))
            if parts:
                contents.append(types.Content(role="model", parts=parts))
        elif m["role"] == "tool":
            try:
                resp = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                resp = {"result": m.get("content") or ""}
            if not isinstance(resp, dict):
                resp = {"result": resp}
            contents.append(types.Content(role="user", parts=[
                types.Part.from_function_response(name=m.get("name") or "", response=resp)]))
        else:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m.get("content") or "")]))
    return contents

def run_turn(
    query: str, *, session: dict, model: str, messages: list | None = None,
    on_event: Callable[[ToolEvent], None] | None = None,
    api_base: str | None = None, api_key_env: str | None = None,
) -> TurnResult:
    schemas = tool_schemas(session)
    if messages is None:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}]
    else:
        msgs = messages
        if not msgs or msgs[0].get("role") != "system":
            msgs.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        msgs.append({"role": "user", "content": query})
    trace: list[ToolEvent] = []
    rounds = 0
    acc = []
    with bind_tool_session(session) as bound:
        tid = open_turn_record(bound)
        for _ in range(30):
            try:
                reply = complete(msgs, schemas, model=model, api_base=api_base, api_key_env=api_key_env)
            except MissingProviderKey as e:
                return TurnResult(
                    answer=str(e), status="provider_error",
                    tool_trace=trace, turn_record=tid, tool_rounds=rounds,
                    usage=_fold_usage(acc),
                )
            except Exception as e:
                return TurnResult(
                    answer=f"provider error: {type(e).__name__}: {e}",
                    status="provider_error", tool_trace=trace, turn_record=tid,
                    tool_rounds=rounds, usage=_fold_usage(acc),
                )
            acc.append(reply.get("usage"))
            calls, text = reply.get("tool_calls") or [], reply.get("text") or ""
            if not calls:
                msgs.append({"role": "assistant", "content": text})
                return TurnResult(
                    answer=text, status="ok", tool_trace=trace,
                    turn_record=tid, tool_rounds=rounds, usage=_fold_usage(acc),
                )
            rounds += 1
            msgs.append({"role": "assistant", "content": text, "tool_calls": calls})
            for call in calls:
                args = call.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                result = dispatch(call["name"], **args)
                event = ToolEvent(name=call["name"], args=args, result=result)
                trace.append(event)
                if on_event:
                    on_event(event)
                msgs.append({
                    "role": "tool", "tool_call_id": call.get("id"),
                    "name": call["name"], "content": json.dumps(result),
                })
            try:
                compact_messages(msgs, bound, window=context_window(model))
            except CompactionBudgetError as e:
                return TurnResult(
                    answer=str(e), status="compaction_error",
                    tool_trace=trace, turn_record=tid, tool_rounds=rounds,
                    usage=_fold_usage(acc),
                )
        return TurnResult(
            answer="round cap (30) reached; see the tool trace for what was retrieved. No guessed answer.",
            status="round_cap", tool_trace=trace, turn_record=tid,
            tool_rounds=rounds, usage=_fold_usage(acc),
        )

def _main() -> None:
    import argparse
    from dissolve.cli import DEFAULT_MODEL, CliApp, main, _tool_event_summary
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        raise SystemExit(main())
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--model", default=DEFAULT_MODEL)
    ns = p.parse_args()
    app = CliApp(model_alias=ns.model, persist=True, quiet=True, require_key=False)
    result = app.ask(ns.query)
    for ev in result.tool_trace:
        print(f"tool  {_tool_event_summary(ev)}")
    print(result.answer)
    print(f"status={result.status}")
    raise SystemExit(0 if result.status == "ok" else 1)

if __name__ == "__main__":
    _main()
