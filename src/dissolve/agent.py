"""The agent: the flat tool registry, the one generic tool wrapper the model calls through, and the turn loop."""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
from collections.abc import Sequence as AbcSeq
from dataclasses import dataclass
from types import UnionType
from typing import (
    Annotated,
    Any,
    Callable,
    Literal,
    NamedTuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from dissolve.contracts import parse_tool_result
from dissolve.session import (
    CompactionBudgetError,
    append_reported,
    bind_handle_rows,
    bind_tool_session,
    compact_messages,
    context_window,
    current_tool_session,
    engine_kwargs_for_handle,
    handle_rows,
    load_handle,
    note_in_play,
    open_turn_record,
    primary_row_key,
    record_tool_call,
    store_handle,
)
from dissolve.thermodynamics import expand_polymer_identity, get_available_solvents

from . import (
    analysis,
    contaminants,
    research,
    safety,
    separation,
    tea,
    thermodynamics,
)

# --- registry: The flat tool registry. Every tool, one list, no routing.
#
# v11 put these behind ten specialists, each with its own prompt, policy, context
# projector, plan validator and completion requirements — so reaching a tool meant
# a routing decision, and a routing decision is a second thing that can be wrong.
# Here they are flat. The model sees all of them and picks.
#
# Nothing in this file decides anything. It imports functions and lists them. If
# you find yourself adding a condition here, that is routing coming back.
#
# PROVENANCE: every tool returns the v11 envelope from `contracts.py` —
# `{"display": str, "data": {...}}` on success, with `success: false` and an
# `error_code` on refusal. The `basis` hook the flat harness will want is not
# uniformly present yet: some tools state their source inside `data`
# (`source_table`, `cache_match_status`, `provenance`, `evidence_class`) and some
# do not. That is deliberate for now — the tools are ported raw and unedited
# beyond decoupling, so what they claim about their own sources is exactly what
# v11 claimed. Normalising that into one declared `basis` enum is a later pass,
# and doing it now would mean inventing provenance for tools that never stated it.



class Tool(NamedTuple):
    name: str
    fn: Callable[..., str]
    engine: str
    summary: str


def _s(fn: Callable[..., Any]) -> str:
    doc = (fn.__doc__ or "").strip().splitlines()
    return doc[0] if doc else ""


def _t(fn: Callable[..., Any], engine: str) -> Tool:
    return Tool(fn.__name__, fn, engine, _s(fn))


REGISTRY: tuple[Tool, ...] = tuple([
    # --- thermodynamics: grid query and separation screens ---
    _t(thermodynamics.solubility_query, "thermodynamics"),
    _t(thermodynamics.screen_polymer_separation, "thermodynamics"),
    _t(thermodynamics.screen_pairwise_solubility_overlap, "thermodynamics"),
    # --- separation: routes, precipitation protocol, membership ---
    _t(separation.resolve_polymer_data_scope, "separation"),
    _t(separation.lookup_material_database_membership, "separation"),
    _t(separation.plan_multistage_separation, "separation"),
    _t(separation.screen_precipitation_order, "separation"),
    _t(separation.screen_cool_then_reheat_getter, "separation"),
    # --- safety ---
    _t(safety.get_solvent_safety_card, "safety"),
    _t(safety.compare_solvent_safety_at_conditions, "safety"),
    _t(safety.screen_green_solvent_candidates, "safety"),
    _t(safety.screen_route_solvent_substitutions, "safety"),
    _t(safety.fetch_solvent_safety_by_cid, "safety"),
    # --- TEA / LCA ---
    _t(tea.evaluate_process, "tea"),
    _t(tea.rank_landscape, "tea"),
    # --- Hansen parameters, thermal properties, numeric analysis ---
    _t(analysis.lookup_hansen_parameters, "analysis"),
    _t(analysis.screen_hansen_compatibility, "analysis"),
    _t(analysis.lookup_glass_transition, "analysis"),
    _t(analysis.list_thermal_evidence, "analysis"),
    _t(analysis.analyze_numeric_samples, "analysis"),
    # --- contaminant removal ---
    _t(contaminants.screen_contaminant_leaching, "contaminants"),
    _t(contaminants.screen_contaminant_strap_removal, "contaminants"),
    _t(contaminants.compare_contaminant_removal_modes, "contaminants"),
    _t(contaminants.screen_contaminant_partitioning, "contaminants"),
    _t(contaminants.lookup_plastchem_contaminants, "contaminants"),
    # --- retrieval-augmented literature ---
    _t(research.search_scholarly_literature, "research"),
    _t(research.search_patent_literature, "research"),
    _t(research.ingest_literature_documents, "research"),
    _t(research.search_literature_corpus, "research"),
    _t(research.inspect_literature_corpus, "research"),
    _t(research.ingest_literature_graph, "research"),
])

BY_NAME: dict[str, Tool] = {t.name: t for t in REGISTRY}


def call(name: str, /, **kwargs: Any) -> str:
    """Invoke a registered tool by name. Unknown name raises — it never guesses."""
    if name not in BY_NAME:
        raise KeyError(f"no such tool: {name!r}")
    return BY_NAME[name].fn(**kwargs)


# --- agent_tools: One generic wrapper, result_read, source_basis, handle issue, system prompt.


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
# Record lists a paged result keeps in the model's view; every other one waits for result_read. A family screen's
# coverage (how many members it screened, how many the release lacks and why) went missing from the answer, and so
# did a safety ranking's order, ties and missing scores once the comparison grew past one page.
_COMPACT_KEEP_LISTS = frozenset({"ranked_path_index", "stage1_shortlists", "family_coverage", "ranking"})
_OMIT = frozenset({"temperature_step_c", "save_to_corpus"})
# Closed keep-out. Not a /literature mode. Retrieval-then-ingest is a later
# spec with an owner decision and a floor re-derive; it does not widen scholarly.
LITERATURE_INGEST_TOOLS = frozenset({
    "ingest_literature_documents",
    "ingest_literature_graph",
})
LITERATURE_CORPUS_TOOLS = frozenset({
    "search_literature_corpus",
    "inspect_literature_corpus",
})
LITERATURE_NETWORK_TOOLS = frozenset({
    "search_scholarly_literature",
    "search_patent_literature",
})
# Named scholarly surface: corpus plus network. Ingest is not a member.
LITERATURE_SCHOLARLY_TOOLS = LITERATURE_CORPUS_TOOLS | LITERATURE_NETWORK_TOOLS
LITERATURE_MODE_SURFACE = {
    "off": frozenset(),
    "corpus": LITERATURE_CORPUS_TOOLS,
    "scholarly": LITERATURE_SCHOLARLY_TOOLS,
}
LITERATURE_AGENT_TOOLS = (
    LITERATURE_INGEST_TOOLS | LITERATURE_CORPUS_TOOLS | LITERATURE_NETWORK_TOOLS
)
_POLY_ARGS = ("polymers", "feed_polymers", "target_polymer", "target_polymers")
_POLY_KEYS = ("polymer", "polymer_id", "target_polymer", "dissolved_polymer")
_PAGE, _BYTE, _LIM = 20, 8192, 50
# The most one page of rows may put in the model's context. A contaminant class screened in every solvent
# nests 26 records in each row, which made a 20-row first page 250 KB and ended the turn at compaction.
_PAGE_BYTES = 24576
_ENGINE_BASIS = {
    "thermodynamics": "cosmo_rs_grid", "separation": "cosmo_rs_grid",
    "optimization": "optimization_workbook", "contaminants": "contaminant_workbook",
    "research": "provider_metadata",
}

def _refuse(refusal: str, **extra: Any) -> dict[str, Any]:
    return {"available": False, "refusal": refusal, **extra}

def _is_rows(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)

def _rows_that_fit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whole rows, as many as fit _PAGE_BYTES, and never fewer than one."""
    used = 0
    for n, row in enumerate(rows):
        used += len(json.dumps(row)) + 2
        if n and used > _PAGE_BYTES:
            return rows[:n]
    return rows

# Log10 partition fields as the model sees them. Past ±6 a substance is effectively all in one phase, so the model
# gets the bound, not the number (owner, 2026-09-24). Engines that compare logD call the tools directly and keep it.
_BOUNDED_LOG_KEYS = frozenset({
    "logd", "tabulated_logd", "computed_delta_logd", "contaminant_logd_min", "logp_solvent_over_polymer",
})

def _bound_logs(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: contaminants.bounded_log(v, None) if k in _BOUNDED_LOG_KEYS and isinstance(v, (int, float))
                else _bound_logs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound_logs(item) for item in value]
    return value

def _first_page(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whole rows when they fit _PAGE_BYTES. Otherwise each row's nested row lists show as counts (result_read
    returns rows whole), and then as many rows as fit."""
    if len(json.dumps(rows)) > _PAGE_BYTES:
        rows = [{k: f"{len(v)} entries; result_read returns this row whole" if _is_rows(v) else v
                 for k, v in row.items()} for row in rows]
    return _rows_that_fit(rows)

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
    window = _rows_that_fit(rows[off:off + lim])
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

def _snapshot_origin(data: dict[str, Any]) -> bool:
    """True when a result's PubChem fields came from DISSOLVE's cached snapshot. The safety tools stamp each field's
    origin, often inside per-solvent rows, so every level is searched; a live stamp anywhere means live."""
    origins, stack = set(), [data]
    while stack:
        obj = stack.pop()
        if isinstance(obj, list):
            stack.extend(obj)
        elif isinstance(obj, dict):
            if obj.get("pubchem_source") in ("snapshot", "live"):
                origins.add(obj["pubchem_source"])
            stamps = obj.get("field_origin")
            if isinstance(stamps, dict):
                origins.update(s["source"] for s in stamps.values()
                               if isinstance(s, dict) and s.get("source") in ("snapshot", "live"))
            stack.extend(obj.values())
    return "snapshot" in origins and "live" not in origins

def source_basis_for(name: str, data: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    if name == "lookup_material_database_membership":
        return "identity_registry"
    if name == "screen_contaminant_partitioning":
        return "opencosmo_24a"
    if name == "lookup_plastchem_contaminants":  # with a solvent it also gives openCOSMO-RS predictions
        return "opencosmo_24a" if kwargs.get("solvent") else "plastchem_identity"
    if (
        name == "rank_landscape"
        and str(data.get("source") or "").strip().casefold() == "planner_routes"
    ):
        return "cosmo_rs_grid"
    eng = BY_NAME[name].engine
    if eng == "safety":
        if name == "fetch_solvent_safety_by_cid":
            return "pubchem_live"
        if kwargs.get("include_pubchem") is True and _pubchem_contributed(data):
            if not _snapshot_origin(data):
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

def literature_agent_mode(session: Any = None) -> str:
    """off | corpus | scholarly. Absent or junk is off. Not a registry filter."""
    if session is None:
        session = current_tool_session()
    if not isinstance(session, dict):
        return "off"
    stored = session.get("literature_mode")
    if isinstance(stored, dict):
        token = str(stored.get("mode") or "").strip().casefold()
    else:
        token = str(stored or "").strip().casefold()
    if token in {"corpus", "scholarly"}:
        return token
    return "off"


def offered_literature_names(session: Any = None) -> frozenset[str]:
    """Literature names offered for this session. Never ingest."""
    return LITERATURE_MODE_SURFACE[literature_agent_mode(session)]


def offered_tool_names(session: Any = None) -> frozenset[str]:
    """Agent surface. Strip all six literature names, then add the mode map.

    Scholarly is LITERATURE_SCHOLARLY_TOOLS, not "registry minus ingest".
    Ingest is not a map key and is not in any map value.
    """
    names = {spec.name for spec in REGISTRY}
    names -= LITERATURE_AGENT_TOOLS
    names |= offered_literature_names(session)
    return frozenset(names)


def _schema_item(spec: Any) -> dict[str, Any]:
    prefix = (
        "UNWIRED. Returns tool_not_wired. Reads session state through an interface "
        "v12 removed; pending an engine pass to accept a handle. Do not call this "
        "to recover a missing route. "
    )
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
    item: dict[str, Any] = {
        "name": spec.name,
        "description": desc,
        "parameters": {"type": "object", "properties": props},
    }
    if req:
        item["parameters"]["required"] = req
    return item


def tool_schema_for(name: str) -> dict[str, Any]:
    """Schema for one registry name. Not an offer; ingest stays inspectable."""
    return _schema_item(BY_NAME[name])


def tool_schemas(session: Any = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{
        "name": "result_read",
        "description": "Page exact stored rows of a handle. Does not issue a second handle.",
        "parameters": {"type": "object", "properties": {
            "handle": {"type": "string"}, "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 20},
            "page": {"type": "string", "enum": ["steps", "top_k_sequences"]},
        }, "required": ["handle"]},
    }]
    offered = offered_tool_names(session)
    for spec in REGISTRY:
        if spec.name not in offered:
            continue
        out.append(_schema_item(spec))
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
    top = _first_page(rows[:_PAGE])
    rest = {k: v for k, v in data.items() if k in _COMPACT_KEEP_LISTS or not _is_rows(v)}
    return {
        "available": True, "source_basis": basis, "handle": name,
        "total": len(rows), "shown": len(top), "top": top, "data": rest,
    }

def _invoke(name, kwargs):
    try:
        return parse_tool_result(call(name, **kwargs)), None
    except KeyError as e:
        if name not in BY_NAME:
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
    if name not in BY_NAME:
        out = _refuse("unknown_tool", name=name)
        return _emit(name, kwargs, out, out)
    if name in LITERATURE_AGENT_TOOLS and name not in offered_tool_names(current_tool_session()):
        out = _refuse(
            "literature_tools_not_offered",
            name=name,
            literature_mode=literature_agent_mode(current_tool_session()),
        )
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
    if name in LITERATURE_NETWORK_TOOLS:
        call_kwargs["save_to_corpus"] = False
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
    parsed = {**parsed, "data": _bound_logs(parsed["data"])}
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

Call a tool only for what no earlier result holds. A screen's handle
feeds the safety comparison; the safety comparison already carries each
solvent's safety-card fields, and the Hansen compatibility screen
carries the parameters it used, so do not fetch those again one by one.
Screens resolve polymer and solvent names themselves; look up database
membership only when that is the question.
When solvents will be used at a temperature, ask the first screen for
options that stay liquid there at 1 atm (require_atmospheric) unless
the user mentions pressure, so you screen once. When the question gives
no temperature, a screen or plan puts each solvent at its own best
temperature (temperature_basis): say so in the first sentence and give
each option's temperature. A question about how
something changes over a range asks for the points in between: query
the range in steps (every 10 °C for temperatures), not only its ends.

Never compute, interpolate, average, or estimate. Every numeral in your
answer came back from a tool call. If a number is not in a tool result,
you do not have it. Rounding a returned value for display, to three
significant figures, is the one change you may make to it.

Never paraphrase a source_basis into a claim that a number is computed
versus measured, or live versus cached, except where this prompt
defines that token. An unexplained token is worse than none: it gets
turned into a confident claim about where a number came from.

Say where the numbers come from once, in plain words, in a closing
line that starts "Source:". Write the meanings below, never the tokens
themselves; name a token this prompt does not define as it is:
- Solubility values are COSMO-RS predictions (source_basis
  cosmo_rs_grid), not measurements.
- PubChem fields fetched live in this call are source_basis
  pubchem_live, with no retrieval date. DISSOLVE's local safety store,
  its safety cards and its cached PubChem snapshot, is source_basis
  safety_local. include_pubchem adds the PubChem fields (GHS hazard
  statements, flash point, exposure limits) from that snapshot, with no
  network call; it defaults to false. Pass true when those fields are
  the question, which includes any request for CHEM21
  Safety/Health/Environment scores or a safety ranking (a CHEM21
  ranking reads them either way).
  fetch_solvent_safety_by_cid queries PubChem live: use it only when the
  user asks for current PubChem data or a solvent is missing from the
  snapshot. A row field named basis on
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
  solubilities, and qualitative_hansen_red_screen is a qualitative
  Hansen compatibility check. An HSP random-forest row is hsp_fallback.
  Name a Hansen record by its material ("LDPE, two parameter sets"),
  never by its record label.
- Contaminant screens are contaminant_workbook screening proxies.
- PlastChem partitioning and miscibility (source_basis opencosmo_24a)
  are DISSOLVE's openCOSMO-RS 24a predictions for neutral species,
  logP at 25 °C. They were checked against the COSMOtherm workbook only
  for PVC with eight phthalates; other polymers have no COSMO-RS check.
  They are not measurements. Use screen_contaminant_partitioning for PlastChem
  contaminants, named one by one or as a family (phthalates, bisphenols,
  antioxidants and the others its description lists). For a family, say how
  many members it screened and how many the release lacks, and why. The
  workbook screens cover 26 PFAS and 8 phthalates.
- plastchem_identity is the PlastChem release's record of who a
  contaminant is (name, CAS number, InChIKey, SMILES, families), as
  PubChem gave it: identities, not predictions. Use
  lookup_plastchem_contaminants for such questions; it needs no polymer
  or solvent.
- A contaminant in one solvent needs no polymer: give
  lookup_plastchem_contaminants the solvent, and it returns the
  contaminant's miscibility with that solvent and its logP between the
  solvent and every polymer. Show those rather than asking which
  polymer; logP always compares the solvent with a polymer, so label
  each value with its polymer. When a solvent is not in the panel, the
  refusal names close panel solvents (o-xylene for xylene): use the
  closest and say which one you used.
- Optimization figures are optimization_workbook.
- identity_registry is DISSOLVE's material identity registry;
  safety_local is its local safety store (safety cards and a cached
  PubChem snapshot); provider_metadata is a
  literature provider's record; analysis_asset is a stored analysis
  dataset. Claim nothing more about them.
- Screening thresholds (1, 5, 10 wt%) are engine defaults, not process
  claims. If you mention them, say that.
- When a tool result states bound inclusivity (bounds_are_inclusive),
  report qualifying counts with those bounds. Do not restate the
  user's strict inequality if the engine applied an inclusive one.
  A count for >= 5 / <= 1 is not a count for > 5 / < 1. If you have
  not read a boundary-equal row off the handle, do not claim the two
  are the same.

A refusal is final. Report it, say what is available, and do not retry
the same call with a nudged argument. Two exceptions: a solvent the
user named that comes back solvent_not_in_scope is outside the default
screening set, not unknown; repeat that call once with
solvent_scope='all'. A ranking refused as shortlist_handle: run the
screen again with the top_k it names, then rank that handle. Off-grid
temperatures come back with neighbouring nodes — report the
neighbours, do not invent a value at the requested temperature.
Unknown solvents come back with an
identity verdict (nonsense vs known chemical without grid values) and
at most five near-miss names — do not substitute the top hit.
PE and polyethylene mean LDPE unless the user names HDPE. Other
polymer family names expand to every stored-grid member or refuse
ambiguous_polymer with the members (nylon is NYLON6 and NYLON66);
report every member. Do not treat a Hansen catalog row labelled PE as
a single polymer.

Large results come back as a handle, a total, and the first page
(top). total is not something you infer from how many rows you can
see. To read more rows, call result_read(handle, offset, limit). A
page too large for your context shows each nested list in a row as
its count; result_read returns rows whole, as many as fit, so read on
from offset + returned. To hand a shortlist to a downstream tool, pass
the handle. Do not retype the rows. If you do not have a handle and
the user did not name the subjects, you do not have candidates.

Literature search and ingest tools are ordinary tools on your list.
Call them directly. There is no research sub-agent.

Do not continue a refusal by calling the same tool with a spelling
you invented. Do not interpolate. Do not average two grid nodes. Do
not answer a temperature that is not a grid node. A difference
between two tool results (a recovery window, a gap) is arithmetic
you must not perform. If the user asks for one, give the two numbers
and say you did not subtract them; otherwise say nothing about it.

Writing the answer. The reader is an engineer deciding what to do, not
a reviewer of the tools.
- Open with the answer in one or two sentences: the verdict, the
  recommendation or the value, with its unit and conditions.
- Support it with a short list, or a table when there are two or more
  rows: plain labels, units in the headers (for example "Solubility
  (wt%)", "Boiling point (°C)"), values rounded for display. A table is
  a header row, a |---| separator row, then one row per line. Include
  only the rows and columns the
  question needs; if others were screened, say how many and why they
  were left out.
- Show a molecule's structure by giving its SMILES in a table column
  headed SMILES, each in backticks and copied exactly as the tool gives
  it; the web app draws every structure beside it. Give SMILES when the
  user asks for them or for structures.
- Recommend only options that work at the stated conditions. A solvent
  that boils below the operating temperature, or a value the tool marks
  as clipped at a limit, is not a recommendation; say what is wrong
  with it instead. Do not mention a limit a value did not reach.
- A requirement in the question is a filter. When the user says a
  polymer must stay undissolved (insoluble, retained, intact), screen
  with max_retained_pct=1 so the tool applies it, leave out options
  where it dissolves above the engine's insolubility level (1 wt%, the
  precipitation threshold), say how many you left out, and name
  the level. If none remain, say so first and show the closest.
- Never show field names, unit tokens, handle names, tool names,
  true/false flags or status codes. Say what they mean in words: a
  record marked unreviewed_raw is "not yet reviewed".
- A number shown in a table is not repeated in the prose. Aim for the
  shortest answer that supports the decision: usually one table and
  under about 250 words, unless the user asks for detail.
- End with the Source line. Add caveats only if they would change the
  reader's decision, at most three, and never repeat the Source line.
- Stop when the results answer the question. A summary a tool computed
  over all its rows (a recommended list, a count) covers those rows;
  read more rows only for values you will show.
- Do not describe your process or repeat these instructions.

Comparing alternatives (solvents, routes, conditions).
- Name the criterion you ranked by, and its direction, in the first
  sentence or the table header. When the user names a criterion, such
  as safety, rank by it and show the other criteria as context.
- Let the tools rank; report their order. To rank solvents by safety or
  greenness, pass rank_by to compare_solvent_safety_at_conditions
  (chem21, unless the user names the Safety score alone; g_score for
  greenness). To rank routes, plan with breadth 3 and pass the plan's
  handle to rank_landscape (source planner_routes, operation sort,
  objective max_stage_chem21 or min_stage_g_score). Keep the order they
  return, say which options tie, and name any option ranked last for a
  missing score; never break a tie by another criterion without saying
  which. A route's CHEM21 standing is its least safe stage's band and
  worst score (chem21_least_safe_band, chem21_least_safe_worst_score);
  the objective value is only a sort key, never a score to show.
- A shortlist is not the screen. When a screen shows fewer candidates
  than qualified (qualifying_total_by_target), say "top N of M". To rank
  every qualifying solvent by another criterion, run the screen with
  top_k set to the qualifying total (at most 40) and pass that handle
  to the ranking tool; a default shortlist's handle is refused for
  ranking. When a polymer's data cover fewer solvents
  than were asked about, say how many, and that the rest were not
  assessed.
- A route with several solvents is as safe as its least safe solvent,
  and separates as well as its weakest step, unless the user says
  otherwise. Say which rule you used, and apply it to every solvent in
  the route.
- Missing data never counts in an option's favour. On a criterion where
  an option's data are missing, rank it after the options with complete
  data and name what is missing.
- Describe a route as its steps in order: the polymer each step
  dissolves, the solvent and temperature, and what is left at the end.
- When you recommend a solvent for a polymer, cross-check it with the
  Hansen tools where records exist, and say whether the Hansen verdict
  agrees with the COSMO-RS prediction. Say so plainly when a Hansen
  record is missing; never estimate one. A step the Hansen check
  contradicts (the solvent far outside the sphere) is weaker evidence:
  say so beside it, and when another option works on both counts, lead
  with that one. Hansen parameters are room-temperature values, so this
  holds near room temperature; for a step above about 60 °C, call the
  Hansen verdict indicative only, never a contradiction.
- Two values clipped at the same ceiling are not ranked against each
  other. Do not lead with an option whose place depends on a clipped
  value; show it after options with resolved values, marked as capped.
  If every option depends on a capped value, say so in the first
  sentence.

Metrics. Explain each score an answer shows once, in one short legend
line under the table (or after its first mention when there is no
table): what it measures, its scale and which direction is better.
Call each score by the same name in the table header and the legend.
GHS hazard statements are written in words (H225: highly flammable
liquid and vapour), never as bare codes.
- G score: the GSK solvent sustainability score, about 1 to 10, higher
  is greener. Tabulated for solvents in the GSK guide; otherwise
  ML-predicted with an uncertainty, which you should mention.
- CHEM21 Safety, Health and Environment scores: 1 to 10 each, higher is
  more hazardous, combined into a band (recommended, problematic,
  hazardous). Missing scores are missing, never safe.
- GHS signal word: Danger is more severe than Warning.
- Hansen parameters: dispersion, polar and hydrogen-bonding components
  (MPa^0.5). RED is the Hansen distance divided by the polymer's
  interaction radius: below 1 the solvent lies inside the polymer's
  solubility sphere (likely to dissolve it), above 1 outside.
- Selectivity (percentage points): the dissolved polymer's solubility
  minus the retained polymer's, as the tool reports it.
- logP or logD: log10 of the ratio of a substance's concentration in the
  solvent to its concentration in the polymer; positive favours the
  solvent, and K is the ratio itself. Give the size with the direction,
  as a range when there are many rows. When |logP| is below 0.5 (K 0.3
  to 3), say the substance splits nearly evenly between the phases,
  whatever the verdict says. A value shown as "> 6" or "< -6" means
  effectively all in one phase: say that, and never write a larger
  number.
"""


# --- agent_harness: The turn loop: one user turn runs tool calls until the model answers; `python -m dissolve.agent "question"` runs one.


_PIPE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_PIPE_SEPARATOR = re.compile(r"^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$")

_DASH_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")

def _separator_like(line: str) -> bool:
    """A repeated separator, even with stray text in it: every cell holds ---, or most cells are nothing but dashes."""
    cells = line.strip().strip("|").split("|")
    return all("---" in cell for cell in cells) or sum(bool(_DASH_CELL.match(cell)) for cell in cells) * 2 > len(cells)

def _complete_tables(text: str) -> str:
    """Give a pipe table its missing header separator, drop a repeated one, and end it with a blank line. Without the
    |---| row, the CLI's Markdown and the web renderer both show the rows as one paragraph; a second separator (the
    model once wrote one with stray text in a cell) renders as a junk data row; and a line of text right under a
    table is drawn as one more row. Code blocks are left alone."""
    lines, out, fenced, separated = text.split("\n"), [], False, False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        row = not fenced and _PIPE_ROW.match(line)
        if not row:
            if separated and line.strip():  # text right under a table would render as one more table row
                out.append("")
            separated = False
        elif separated and _separator_like(line):
            continue
        elif _PIPE_SEPARATOR.match(line):
            separated = True
        out.append(line)
        header = row and not (i and _PIPE_ROW.match(lines[i - 1]))
        if header and i + 1 < len(lines) and _PIPE_ROW.match(lines[i + 1]) and not _PIPE_SEPARATOR.match(lines[i + 1]):
            out.append("|" + "---|" * (line.strip().strip("|").count("|") + 1))
            separated = True
    return "\n".join(out)

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

# A provider that is briefly overloaded (503) or rate-limiting (429) is retried with the SDK's backoff
# before the turn gives up; the SDK default of two retries ended users' turns during short overloads.
_PROVIDER_RETRIES = 5

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
        resp = anthropic.Anthropic(api_key=key, max_retries=_PROVIDER_RETRIES).messages.create(
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
        resp = OpenAI(api_key=key or None, base_url=api_base or None, max_retries=_PROVIDER_RETRIES).chat.completions.create(
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
                text = _complete_tables(text)
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

    from dissolve.cli import DEFAULT_MODEL, CliApp, _tool_event_summary, main
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
