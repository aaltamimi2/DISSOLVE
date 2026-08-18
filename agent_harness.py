import json, os, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dissolve.session import bind_tool_session
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
    numeral_scan: list[dict]

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

def complete(messages, tools, *, model, api_base=None, api_key_env=None):
    kind, _, ident = model.partition(":")
    ident, key = ident or model, os.environ.get(api_key_env or "", "")
    if kind == "anthropic":
        import anthropic
        sys, rest = _ant_msgs(messages)
        ant = [{"name": t["name"], "description": t.get("description") or "", "input_schema": t["parameters"]} for t in tools]
        resp = anthropic.Anthropic(api_key=key).messages.create(
            model=ident, system=sys, messages=rest, tools=ant, max_tokens=8192)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        calls = [{"id": b.id, "name": b.name, "args": dict(b.input or {})}
                 for b in resp.content if getattr(b, "type", "") == "tool_use"]
        return {"text": text, "tool_calls": calls}
    if kind == "google_genai":
        from google import genai
        from google.genai import types
        decls = [types.FunctionDeclaration(name=t["name"], description=t.get("description") or "", parameters=t["parameters"]) for t in tools]
        sys = next((m.get("content") or "" for m in messages if m["role"] == "system"), "")
        contents = [m.get("content") or json.dumps(m.get("tool_calls") or "") for m in messages if m["role"] != "system"]
        resp = genai.Client(api_key=key).models.generate_content(
            model=ident, contents=contents,
            config=types.GenerateContentConfig(system_instruction=sys, tools=[types.Tool(function_declarations=decls)]))
        calls = [{"id": getattr(c, "id", "") or "", "name": c.name, "args": dict(c.args or {})}
                 for c in (getattr(resp, "function_calls", None) or [])]
        return {"text": getattr(resp, "text", None) or "", "tool_calls": calls}
    if kind == "openai":
        from openai import OpenAI
        oai = [{"type": "function", "function": {"name": t["name"], "description": t.get("description") or "", "parameters": t["parameters"]}} for t in tools]
        msg = OpenAI(api_key=key or None, base_url=api_base or None).chat.completions.create(
            model=ident, messages=_oai_msgs(messages), tools=oai).choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": c.id, "name": c.function.name, "args": args})
        return {"text": msg.content or "", "tool_calls": calls}
    raise ValueError(f"unknown model prefix: {kind!r}")

def run_turn(
    query: str, *, session: dict, model: str, messages: list | None = None,
    on_event: Callable[[ToolEvent], None] | None = None,
    api_base: str | None = None, api_key_env: str | None = None,
) -> TurnResult:
    schemas = tool_schemas()
    msgs = list(messages) if messages else [
        {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}]
    if not msgs or msgs[0].get("role") != "system":
        msgs.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    trace: list[ToolEvent] = []
    with bind_tool_session(session):
        for _ in range(30):
            try:
                reply = complete(msgs, schemas, model=model, api_base=api_base, api_key_env=api_key_env)
            except Exception as e:
                return TurnResult(answer=f"provider error: {type(e).__name__}: {e}",
                                  status="provider_error", tool_trace=trace, numeral_scan=[])
            calls, text = reply.get("tool_calls") or [], reply.get("text") or ""
            if not calls:
                return TurnResult(answer=text, status="ok", tool_trace=trace, numeral_scan=[])
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
                msgs.append({"role": "tool", "tool_call_id": call.get("id"),
                             "name": call["name"], "content": json.dumps(result)})
        return TurnResult(
            answer="round cap (30) reached; see the tool trace for what was retrieved. No guessed answer.",
            status="round_cap", tool_trace=trace, numeral_scan=[])

def _main() -> None:
    import argparse
    from dissolve.session import new_session
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--model", default="openai:muse-spark-1.2")
    p.add_argument("--api-base", default="https://api.meta.ai/v1")
    p.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ns = p.parse_args()
    def _print(ev: ToolEvent) -> None:
        print(f"tool {ev.name} {ev.args}"); print(ev.result)
    result = run_turn(ns.query, session=new_session(), model=ns.model,
                      on_event=_print, api_base=ns.api_base, api_key_env=ns.api_key_env)
    print(result.answer)
    if result.numeral_scan:
        print("numeral scan: " + ", ".join(f"{row['token']}={row['bucket']}" for row in result.numeral_scan))
    print(f"status={result.status}")

if __name__ == "__main__":
    _main()
