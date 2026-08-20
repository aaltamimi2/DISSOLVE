"""One generic wrapper, result_read, source_basis, handle issue, system prompt."""
from __future__ import annotations

import inspect, json
from collections.abc import Sequence as AbcSeq
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

from dissolve import registry
from dissolve.contracts import parse_tool_result
from dissolve.session import (
    append_reported, bind_handle_rows, current_tool_session,
    engine_kwargs_for_handle, handle_rows, load_handle, note_in_play,
    primary_row_key, record_tool_call, store_handle,
)
from dissolve.thermodynamics import expand_polymer_identity, get_available_solvents

UNWIRED = frozenset()
PUBCHEM = frozenset({
    "get_solvent_safety_card", "compare_solvent_safety_at_conditions",
    "screen_route_solvent_substitutions",
})
CONSUMERS = frozenset({"compare_solvent_safety_at_conditions"})
_PROCESS_ECONOMICS_HANDLE_TOOLS = frozenset({
    "evaluate_process",
    "rank_landscape",
})
_ALWAYS_HANDLE_TOOLS = _PROCESS_ECONOMICS_HANDLE_TOOLS | frozenset({
    "plan_multistage_separation",
})
_COMPACT_KEEP_LISTS = frozenset({"ranked_path_index"})
_OMIT = frozenset({"temperature_step_c"})
_POLY_ARGS = ("polymers", "feed_polymers", "target_polymer", "target_polymers")
_POLY_KEYS = ("polymer", "polymer_id", "target_polymer", "dissolved_polymer")
_PAGE, _BYTE, _LIM = 20, 8192, 50
_ENGINE_BASIS = {
    "thermodynamics": "cosmo_rs_grid", "separation": "cosmo_rs_grid",
    "optimization": "optimization_workbook", "contaminants": "contaminant_workbook",
    "research": "provider_metadata",
}

def _refuse(refusal: str, **extra: Any) -> dict[str, Any]:
    return {"available": False, "refusal": refusal, **extra}

def result_read(
    handle: str = "", offset: int = 0, limit: int = 20, page: str | None = None,
) -> dict[str, Any]:
    if not isinstance(handle, str) or not handle.strip():
        return _refuse("unknown_handle", handle=handle)
    rec, token = current_tool_session(), handle.strip()
    stored = load_handle(rec, token) if rec is not None else None
    try:
        rows = _handle_page_rows(stored, page) if stored else None
    except ValueError as error:
        if str(error) == "unknown_handle_page":
            return _refuse("unknown_handle_page", handle=handle, page=page)
        rows = None
    except (KeyError, TypeError):
        rows = None
    if stored is None or rows is None:
        return _refuse("unknown_handle", handle=handle)
    try:
        off = max(0, int(offset))
    except (TypeError, ValueError):
        off = 0
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = _PAGE
    lim = max(0, min(lim, _LIM))
    window = rows[off:off + lim]
    return {
        "available": True, "source_basis": stored.get("source_basis"),
        "handle": token, "total": len(rows), "offset": off,
        "returned": len(window), "data": {"rows": window},
    }

def _handle_page_rows(stored: dict[str, Any], page: str | None) -> list[dict[str, Any]]:
    """Named page on handle exact, or the primary list when page is omitted."""
    if page is None or (isinstance(page, str) and not page.strip()):
        return handle_rows(stored)
    token = str(page).strip()
    if token not in {"steps", "top_k_sequences"}:
        raise ValueError("unknown_handle_page")
    exact = stored.get("exact") if isinstance(stored, dict) else None
    rows = exact.get(token) if isinstance(exact, dict) else None
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]

def to_contract(raw: dict[str, Any], source_basis: str | None) -> dict[str, Any]:
    data = raw["data"]
    if not data.get("success"):
        return {"available": False, "refusal": data.get("error_code") or "tool_exception", "data": data}
    out: dict[str, Any] = {"available": True, "data": data}
    if source_basis:
        out["source_basis"] = source_basis
    return out

def _tea_basis(data: dict[str, Any]) -> str | None:
    rows = [r for r in (data.get("comparison_rows") or []) if isinstance(r, dict)]
    modes = [str(r.get("engine_mode") or "") for r in rows] or [str(data.get("engine_mode") or "")]
    stats = [str(r.get("cache_match_status") or "") for r in rows] or [str(data.get("cache_match_status") or "")]
    analog = (
        any(s == "surrogate" or m == "screening_estimate" for m, s in zip(modes, stats))
        or data.get("cache_match_status") == "surrogate"
        or data.get("engine_mode") == "screening_estimate"
    )
    live = "live" in modes or data.get("engine_mode") == "live"
    if analog:
        return "tea_screening_analog"
    if live:
        return "tea_live"
    if data.get("cache_match_status") == "exact" or "cache" in modes or data.get("engine_mode") == "cache":
        return "tea_cache_exact"
    if (
        data.get("engine_mode") == "campaign"
        or "campaign" in modes
        or str(data.get("source") or "").strip().casefold() == "campaign"
    ):
        return "campaign_process_rows"
    return None

def _pubchem_contributed(obj: Any) -> bool:
    if isinstance(obj, list):
        return any(_pubchem_contributed(x) for x in obj)
    if not isinstance(obj, dict):
        return False
    prov = obj.get("provenance")
    if isinstance(prov, dict) and (
        prov.get("pubchem") or prov.get("pubchem_failed_headings")
        or prov.get("pubchem_heading_errors")
    ):
        failed = list(prov.get("pubchem_failed_headings") or ())
        if len(failed) < 8 and (failed or prov.get("pubchem")):
            return True
    return any(_pubchem_contributed(v) for v in obj.values())

def source_basis_for(name: str, data: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    if name == "lookup_material_database_membership":
        return "identity_registry"
    if (
        name == "rank_landscape"
        and str(data.get("source") or "").strip().casefold() == "planner_routes"
    ):
        return "cosmo_rs_grid"
    eng = registry.BY_NAME[name].engine
    if eng == "safety":
        if kwargs.get("include_pubchem") is True and _pubchem_contributed(data):
            return "pubchem_live"
        return "safety_local"
    if eng == "tea":
        return _tea_basis(data)
    if eng == "analysis":
        ev = data.get("evidence_class")
        return "hsp_fallback" if ev == "hsp_random_forest_fallback" else (str(ev) if ev else "analysis_asset")
    return _ENGINE_BASIS.get(eng)

def _ptype(ann: Any) -> dict[str, Any]:
    origin, args = get_origin(ann), get_args(ann)
    if origin is Annotated:
        return _ptype(args[0])
    if origin is Literal:
        return {"type": "string", "enum": [str(v) for v in args]}
    if origin in (Union, UnionType):
        non = [a for a in args if a is not type(None)]
        if len(non) == 1:
            return _ptype(non[0])
        variants = [p for a in non if (p := _ptype(a))]
        if not variants:
            return {}
        return variants[0] if len(variants) == 1 else {"anyOf": variants}
    if origin in (list, tuple, AbcSeq):
        out: dict[str, Any] = {"type": "array"}
        if args and (item := _ptype(args[0])):
            out["items"] = item
        return out
    if origin is dict:
        return {"type": "object"}
    return {bool: {"type": "boolean"}, int: {"type": "integer"}, float: {"type": "number"}, str: {"type": "string"}}.get(ann, {})

def tool_schemas() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{
        "name": "result_read",
        "description": "Page exact stored rows of a handle. Does not issue a second handle.",
        "parameters": {"type": "object", "properties": {
            "handle": {"type": "string"}, "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 20},
            "page": {"type": "string", "enum": ["steps", "top_k_sequences"]},
        }, "required": ["handle"]},
    }]
    prefix = (
        "UNWIRED. Returns tool_not_wired. Reads session state through an interface "
        "v12 removed; pending an engine pass to accept a handle. Do not call this "
        "to recover a missing route. "
    )
    for spec in registry.REGISTRY:
        props, req = {}, []
        try:
            hints = get_type_hints(spec.fn, include_extras=True)
        except Exception:
            hints = {}
        for n, p in inspect.signature(spec.fn).parameters.items():
            if n in _OMIT or p.kind is p.VAR_KEYWORD:
                continue
            props[n] = _ptype(hints.get(n, p.annotation))
            if spec.name in PUBCHEM and n == "include_pubchem":
                props[n] = {**props[n], "type": "boolean", "default": False}
            elif p.default is not inspect.Parameter.empty and isinstance(
                p.default, (bool, int, float, str)
            ):
                props[n]["default"] = p.default
            if p.default is inspect.Parameter.empty:
                req.append(n)
        if spec.name in CONSUMERS:
            props["handle"] = {"type": "string"}
        desc = prefix + spec.summary if spec.name in UNWIRED else spec.summary
        item: dict[str, Any] = {"name": spec.name, "description": desc, "parameters": {"type": "object", "properties": props}}
        if req:
            item["parameters"]["required"] = req
        out.append(item)
    return out

def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) + len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(cur[j] + 1, prev[j + 1] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]

def _near_miss(data: dict[str, Any]) -> list[str]:
    queries = [str(x).casefold() for x in (data.get("unsupported_solvents") or ()) if x]
    if not queries and data.get("solvent_name"):
        queries = [str(data["solvent_name"]).casefold()]
    roster, picked = sorted(get_available_solvents()), []
    for q in queries:
        if len(q) >= 4:
            for n in roster:
                cf = n.casefold()
                if (cf.startswith(q) or q.startswith(cf)) and n not in picked:
                    picked.append(n)
                    if len(picked) >= 5:
                        return picked
        for n in roster:
            if _lev(q, n.casefold()) <= 2 and n not in picked:
                picked.append(n)
                if len(picked) >= 5:
                    return picked
    return picked[:5]

def _ambiguous(kwargs: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
    key, ids = primary_row_key(data), set()
    for row in (data.get(key) if key else []) or []:
        if not isinstance(row, dict):
            continue
        for k in _POLY_KEYS:
            v = row.get(k)
            if isinstance(v, str) and v:
                ids.update(expand_polymer_identity(v) or (v,))
    if not ids:
        return None
    for arg in _POLY_ARGS:
        val = kwargs.get(arg)
        if val is None and arg not in kwargs:
            continue
        for n in (val if isinstance(val, (list, tuple)) else (val,)):
            if not isinstance(n, str):
                continue
            exp = expand_polymer_identity(n)
            if len(exp) > 1 and set(exp) - ids:
                return _refuse(
                    "ambiguous_polymer", requested=n, roster=list(exp),
                    detail="this name names more than one stored-grid polymer",
                )
    return None

def _issue_handle(record, tool, basis, data, payload, display=None):
    key = primary_row_key(data)
    n_rows = len(data[key]) if key else 0
    page_over = bool(key and n_rows > _PAGE)
    json_over = len(json.dumps(data)) > _BYTE
    always = tool in _ALWAYS_HANDLE_TOOLS and key is not None
    if tool in _PROCESS_ECONOMICS_HANDLE_TOOLS:
        over = page_over
    else:
        over = page_over or json_over
    if not over and not always:
        return payload
    if record is None:
        if always and not over:
            return payload
        return _refuse("unaddressable_result", detail="no bound session", tool=tool)
    try:
        name = store_handle(record, tool=tool, source_basis=basis, data=data, display=display)
    except ValueError:
        if always and not over:
            return payload
        return _refuse(
            "unaddressable_result",
            detail="result crossed a handle threshold with no primary row list",
            tool=tool,
        )
    if always and not over:
        return {**payload, "handle": name, "total": n_rows}
    rows = handle_rows(load_handle(record, name))
    top = rows[:_PAGE]
    rest = {
        k: v for k, v in data.items()
        if k in _COMPACT_KEEP_LISTS
        or not (isinstance(v, list) and v and all(isinstance(i, dict) for i in v))
    }
    return {
        "available": True, "source_basis": basis, "handle": name,
        "total": len(rows), "shown": len(top), "top": top, "data": rest,
    }

def _invoke(name, kwargs):
    try:
        return parse_tool_result(registry.call(name, **kwargs)), None
    except KeyError as e:
        if name not in registry.BY_NAME:
            return None, _refuse("unknown_tool", name=name)
        return None, _refuse("tool_exception", error=f"KeyError: {e}")
    except (TypeError, ValueError) as e:
        return None, _refuse("tool_exception", error=f"{type(e).__name__}: {e}")
    except Exception as e:
        return None, _refuse("tool_exception", error=f"{type(e).__name__}: {e}")

def _emit(name, kwargs, out, exact, handle=None, display=None, source_basis=None):
    rec = current_tool_session()
    if rec is not None:
        record_tool_call(
            rec, tool=name, args=kwargs, exact=exact, handle=handle,
            display=display, source_basis=source_basis,
        )
        note_in_play(rec, kwargs)
        append_reported(rec, out)
    return out

def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    if name == "result_read":
        h = kwargs.get("handle")
        out = result_read(
            handle=h if isinstance(h, str) else "",
            offset=kwargs.get("offset", 0), limit=kwargs.get("limit", 20),
            page=kwargs.get("page"),
        )
        token = h.strip() if isinstance(h, str) and h.strip() else None
        rec = current_tool_session()
        stored = load_handle(rec, token) if rec is not None and token else None
        if stored is not None and out.get("available"):
            return _emit(
                name, kwargs, out, out, token, None, stored.get("source_basis"),
            )
        return _emit(name, kwargs, out, out)
    if name in UNWIRED:
        out = _refuse(
            "tool_not_wired",
            detail="reads session state through an interface v12 removed; pending an engine pass to accept a handle",
        )
        return _emit(name, kwargs, out, out)
    if name not in registry.BY_NAME:
        out = _refuse("unknown_tool", name=name)
        return _emit(name, kwargs, out, out)
    record, call_kwargs, bind = current_tool_session(), dict(kwargs), None
    if name in CONSUMERS:
        if "handle" in kwargs:
            token = kwargs.get("handle")
            if not isinstance(token, str) or not token.strip():
                out = _refuse("no_upstream_candidates")
                return _emit(name, kwargs, out, out)
            bind, call_kwargs = engine_kwargs_for_handle({**kwargs, "handle": token.strip()})
            if record is None or load_handle(record, bind) is None:
                out = _refuse("no_upstream_candidates")
                return _emit(name, kwargs, out, out)
        elif kwargs.get("candidates") is None:
            out = _refuse("no_upstream_candidates")
            return _emit(name, kwargs, out, out)
    if name in PUBCHEM and call_kwargs.get("include_pubchem") is None:
        call_kwargs["include_pubchem"] = False
    if bind:
        try:
            with bind_handle_rows(record, bind):
                parsed, err = _invoke(name, call_kwargs)
        except (KeyError, ValueError):
            out = _refuse("no_upstream_candidates")
            return _emit(name, kwargs, out, out)
    else:
        parsed, err = _invoke(name, call_kwargs)
    if err:
        return _emit(name, kwargs, err, err)
    data, display = parsed["data"], parsed.get("display")
    if not data.get("success"):
        out = to_contract(parsed, None)
        if out.get("refusal") == "unknown_solvents" and not (data.get("near_miss_solvents") or data.get("suggested_solvents")):
            out["near_miss_solvents"] = _near_miss(data)
        return _emit(name, kwargs, out, parsed, display=display)
    basis = source_basis_for(name, data, call_kwargs)
    if not basis:
        out = _refuse("no_honest_basis", tool=name)
        return _emit(name, kwargs, out, parsed, display=display)
    guard = _ambiguous(call_kwargs, data)
    if guard:
        return _emit(name, kwargs, guard, parsed, display=display, source_basis=basis)
    payload = _issue_handle(record, name, basis, data, to_contract(parsed, basis), display)
    handle = payload.get("handle") if payload.get("available") else None
    if handle:
        return _emit(name, kwargs, payload, None, handle, None, basis)
    return _emit(name, kwargs, payload, parsed, None, display, basis)

SYSTEM_PROMPT = """\
You are DISSOLVE v12, a thermodynamic analysis agent. You have tools. You
do not have a calculator.

The architecture is a loop: you call a tool, you read the result, you
call another tool or you answer. There is no plan to fill in, no
specialist to route to, no phase to complete.

Never compute, interpolate, average, or estimate. Every numeral in your
answer came back from a tool call. If a number is not in a tool result,
you do not have it.

Report the source_basis token as given. Never paraphrase it into a
claim that a number is computed versus measured, or live versus
cached, except where this prompt already defines that token. An
unexplained token is worse than none: it gets turned into a
confident claim about where a number came from.

Report the source_basis with the number.
- Solubility values are COSMO-RS predictions (source_basis
  cosmo_rs_grid), not measurements.
- PubChem safety fields are fetched live (source_basis pubchem_live)
  with no retrieval date. Local-only safety cards are source_basis
  safety_local. include_pubchem defaults to false; pass true only
  when the live fields are the question. A row field named basis on
  a safety card is an averaging time (for example 8-hour TWA), not
  the source of the card.
- A TEA cache hit is an exact prior simulation (source_basis
  tea_cache_exact).
- A TEA screening analog is a cache-derived estimate (source_basis
  tea_screening_analog), not a simulation of the named configuration.
  Say so.
- A live BioSTEAM run (source_basis tea_live) is a simulation of the
  named configuration. Say so. It may not appear in this environment.
- Campaign process_rows (source_basis campaign_process_rows) are
  already-run rows consumed from a registered campaign at that
  campaign's held basis. They are not tea_cache_exact and not a live
  BioSTEAM run of this call.
- Hansen curated rows are qualitative_hansen_parameters, not
  solubilities. An HSP random-forest row is hsp_fallback.
- Contaminant screens are contaminant_workbook screening proxies.
- Optimization figures are optimization_workbook.
- identity_registry, safety_local, provider_metadata, analysis_asset:
  report the token. Do not invent a gloss.
- Screening thresholds (1, 5, 10 wt%) are engine defaults, not process
  claims. If you mention them, say that.
- When a tool result states bound inclusivity (bounds_are_inclusive),
  report qualifying counts with those bounds. Do not restate the
  user's strict inequality if the engine applied an inclusive one.
  A count for >= 5 / <= 1 is not a count for > 5 / < 1. If you have
  not read a boundary-equal row off the handle, do not claim the two
  are the same.

A refusal is final. Report it, say what is available, and do not retry
the same call with a nudged argument. Off-grid temperatures come back
with neighbouring nodes — report the neighbours, do not invent a value
at the requested temperature. Unknown solvents come back with an
identity verdict (nonsense vs known chemical without grid values) and
at most five near-miss names — do not substitute the top hit.
Ambiguous polymer family names expand to every stored-grid member
or refuse ambiguous_polymer with the members (polyethylene is LDPE
and HDPE; nylon is NYLON6 and NYLON66). Report every member. Do not
pick HDPE. Do not treat a Hansen catalog row labelled PE as a
single polymer.

Large results come back as a handle, a total, and the first page
(top). total is not something you infer from how many rows you can
see. To read more rows, call result_read(handle, offset, limit). To
hand a shortlist to a downstream tool, pass the handle. Do not retype
the rows. If you do not have a handle and the user did not name the
subjects, you do not have candidates.

Literature search and ingest tools are ordinary tools on your list.
Call them directly. There is no research sub-agent.

Do not continue a refusal by calling the same tool with a spelling
you invented. Do not interpolate. Do not average two grid nodes. Do
not answer a temperature that is not a grid node. A difference
between two tool results (a recovery window, a gap) is arithmetic
you must not perform; if the user needs the difference, say the
two numbers and that you did not subtract them.
"""
