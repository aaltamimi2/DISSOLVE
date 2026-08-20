"""Interactive DISSOLVE terminal. The loop is run_turn; this file only draws."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    readline = None

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from agent_harness import ToolEvent, TurnResult, run_turn
from agent_tools import SYSTEM_PROMPT
from dissolve import RELEASE, tea
from dissolve.contracts import normalize_json
from dissolve.session import SessionRecord, handle_total, new_session

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
_MODE_LINE = {
    "review": "Ask before making consequential process assumptions the user did not specify.",
    "auto": "Make reasonable process assumptions when needed and label them explicitly.",
}


@dataclass(frozen=True)
class ModelSpec:
    label: str
    model: str
    env_var: str
    usage: str
    base_url: str | None = None


MODELS = {
    "gemini-flash": ModelSpec(
        "Gemini 3.5 Flash", "google_genai:gemini-3.5-flash", "GOOGLE_API_KEY",
        "Default · stronger tool reasoning",
    ),
    "gemini-flash-lite": ModelSpec(
        "Gemini 3.1 Flash Lite", "google_genai:gemini-3.1-flash-lite", "GOOGLE_API_KEY",
        "Budget mode",
    ),
    "gemini-pro": ModelSpec(
        "Gemini 3.1 Pro", "google_genai:gemini-3.1-pro-preview", "GOOGLE_API_KEY",
        "Highest detail · slowest",
    ),
    "claude-sonnet": ModelSpec(
        "Claude Sonnet 4.6", "anthropic:claude-sonnet-4-6", "ANTHROPIC_API_KEY",
        "General reasoning",
    ),
    "muse-spark": ModelSpec(
        "Meta Muse Spark 1.2", "openai:muse-spark-1.2", "META_MUSE_API_KEY",
        "Reasoning model · Meta API", "https://api.meta.ai/v1",
    ),
}
MODEL_ALIASES = {"gemini": "gemini-flash", "claude": "claude-sonnet", "muse": "muse-spark"}
DEFAULT_MODEL = "muse-spark"

# Declared post-consolidation public roster. Doctor compares the live
# registry to this list, not to BY_NAME. Changing the surface is two
# edits: registry.py and this set.
EXPECTED_REGISTRY_NAMES: frozenset[str] = frozenset((
    "solubility_query",
    "screen_polymer_separation",
    "screen_pairwise_solubility_overlap",
    "resolve_polymer_data_scope",
    "lookup_material_database_membership",
    "plan_multistage_separation",
    "screen_precipitation_order",
    "screen_cool_then_reheat_getter",
    "get_solvent_safety_card",
    "compare_solvent_safety_at_conditions",
    "screen_green_solvent_candidates",
    "screen_route_solvent_substitutions",
    "lookup_admitted_process_records",
    "evaluate_process",
    "evaluate_tea_lca_scenarios",
    "evaluate_stored_route_tea_lca",
    "analyze_tea_sensitivity",
    "rank_landscape",
    "optimize_stored_route",
    "pareto_optimize_stored_route",
    "lookup_hansen_parameters",
    "screen_hansen_compatibility",
    "lookup_glass_transition",
    "estimate_thermal_properties",
    "list_thermal_evidence",
    "analyze_numeric_samples",
    "screen_contaminant_leaching",
    "screen_contaminant_strap_removal",
    "compare_contaminant_removal_modes",
    "search_scholarly_literature",
    "search_patent_literature",
    "ingest_literature_documents",
    "search_literature_corpus",
    "inspect_literature_corpus",
    "ingest_literature_graph",
))


def resolve_model(alias: str) -> tuple[str, ModelSpec]:
    key = MODEL_ALIASES.get(alias, alias)
    if key not in MODELS:
        raise ValueError(f"Unknown model alias: {alias}")
    return key, MODELS[key]


def _is_v12_session(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    session, messages = payload.get("session"), payload.get("messages")
    if not isinstance(session, dict) or not isinstance(messages, list):
        return False
    if "state" in payload or "context" in payload:
        return False
    version = payload.get("schema_version")
    release = str(payload.get("release") or "")
    if version == 2:
        return True
    return version == 1 and release.startswith("dissolve-v12")


def _system_prompt(mode: str) -> str:
    return f"{SYSTEM_PROMPT}\n\nCLI interaction mode: {mode}. {_MODE_LINE[mode]}"


def _scrub_session(loaded: dict[str, Any] | None) -> SessionRecord:
    """Resume the world. Never restore an in-flight `_turn`."""
    rec = SessionRecord(loaded) if isinstance(loaded, dict) else new_session()
    rec.pop("_turn", None)
    return rec


def _following_tools(messages: list, asst_at: int) -> list:
    out = []
    for msg in messages[asst_at + 1:]:
        if msg.get("role") != "tool":
            break
        out.append(msg)
    return out


def _calls_match(calls: list, tools: list) -> bool:
    if len(calls) != len(tools):
        return False
    for call, tool in zip(calls, tools):
        cid, tid = call.get("id") or "", tool.get("tool_call_id") or ""
        if cid or tid:
            if cid != tid:
                return False
        elif (call.get("name") or "") != (tool.get("name") or ""):
            return False
    return True


def _drop_incomplete_tool_round(messages: list) -> list:
    """Drop an unmatched user/assistant/tool group. Keep complete empty-id rounds."""
    msgs = list(messages)
    while True:
        incomplete = next(
            (i for i, msg in enumerate(msgs)
             if msg.get("role") == "assistant" and msg.get("tool_calls")
             and not _calls_match(msg.get("tool_calls") or [], _following_tools(msgs, i))),
            None,
        )
        if incomplete is None:
            return msgs
        start = next(
            (j for j in range(incomplete - 1, -1, -1) if msgs[j].get("role") == "user"),
            incomplete,
        )
        end = incomplete + 1 + len(_following_tools(msgs, incomplete))
        msgs = msgs[:start] + msgs[end:]


def doctor_report(
    home: str | Path | None = None, *, model_alias: str | None = None,
) -> dict[str, Any]:
    from dissolve import analysis, contaminants, optimization, safety, tea, thermodynamics
    from dissolve.registry import BY_NAME, REGISTRY

    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str, **facts: Any) -> None:
        checks.append({"name": name, "status": status, "detail": detail, **facts})

    selected_alias, selected_spec = resolve_model(model_alias or DEFAULT_MODEL)
    supported_keys = ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "META_MUSE_API_KEY")
    available_keys = [name for name in supported_keys if os.getenv(name)]
    selected_provider_ready = bool(os.getenv(selected_spec.env_var))
    provider_detail = (
        f"{selected_spec.env_var} configured for selected model "
        f"{selected_alias} ({selected_spec.label})"
        if selected_provider_ready else
        f"{selected_spec.env_var} is required for selected model "
        f"{selected_alias} ({selected_spec.label}); "
        + (f"configured alternatives: {', '.join(available_keys)}" if available_keys else
           f"no provider keys are configured (supported: {', '.join(supported_keys)})")
    )
    add(
        "Model provider", "pass" if selected_provider_ready else "fail",
        provider_detail, configured_keys=available_keys,
        selected_model=selected_alias, selected_model_id=selected_spec.model,
        required_key=selected_spec.env_var,
    )

    assets = [
        ("thermodynamics", thermodynamics._ASSET, thermodynamics._ASSET_SHA256),
        ("safety", safety._ASSET, safety._ASSET_SHA256),
        ("contaminants", contaminants._ASSET, contaminants._ASSET_SHA256),
        ("analysis", analysis._ASSET, analysis._ASSET_SHA256),
        ("tea", tea._ASSET, tea._ASSET_SHA256),
        ("optimization", optimization._ASSET, optimization._ASSET_SHA256),
    ]
    bad_assets = []
    for name, path, expected in assets:
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            bad_assets.append(name)
    add(
        "Scientific assets", "pass" if not bad_assets else "fail",
        f"{len(assets)} checksums verified" if not bad_assets
        else "mismatch/missing: " + ", ".join(bad_assets),
        checked=len(assets), failures=bad_assets,
    )

    live = {tool.name for tool in REGISTRY}
    n_reg, n_by = len(REGISTRY), len(BY_NAME)
    missing = sorted(EXPECTED_REGISTRY_NAMES - live)
    extra = sorted(live - EXPECTED_REGISTRY_NAMES)
    roster_ok = (
        live == EXPECTED_REGISTRY_NAMES
        and n_reg == n_by == len(EXPECTED_REGISTRY_NAMES)
    )
    detail = f"{n_reg} registered names"
    if not roster_ok:
        parts = [detail]
        if missing:
            parts.append("missing " + ", ".join(missing[:8]))
        if extra:
            parts.append("extra " + ", ".join(extra[:8]))
        if n_reg != n_by:
            parts.append(f"REGISTRY {n_reg} != BY_NAME {n_by}")
        detail = "; ".join(parts)
    add(
        "Tool registry", "pass" if roster_ok else "fail",
        detail, registered=n_reg,
    )

    try:
        import duckdb
        add("duckdb", "pass", getattr(duckdb, "__version__", "imported"))
    except ImportError:
        add("duckdb", "fail", "not installed")

    base = Path(home or os.getenv("DISSOLVE_HOME") or Path.home() / ".dissolve").expanduser().resolve()
    probe = base / "sessions" / f".doctor-{os.getpid()}"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("dissolve-doctor", encoding="utf-8")
        healthy = probe.read_text(encoding="utf-8") == "dissolve-doctor"
        probe.unlink(missing_ok=True)
        add("Session store", "pass" if healthy else "fail", str(probe.parent), path=str(probe.parent))
    except OSError as error:
        add("Session store", "fail", f"{probe.parent}: {error}", path=str(probe.parent))

    live_tea = tea.live_environment_report()
    execution_path = live_tea.get("execution_path") or tea.LIVE_TEA_EXECUTION_PATH
    if live_tea.get("available"):
        handshake = live_tea.get("child_handshake_error_type") or "missing"
        live_detail = (
            f"live TEA ready via DISSOLVE_TEA_PYTHON subprocess of tea_worker.py "
            f"(child handshake {tea.LIVE_CHILD_HANDSHAKE_TARGET} {handshake}); "
            f"admitted {', '.join(live_tea['admitted_live_targets'])}; "
            f"parameters in {live_tea['parameter_surface']}. {execution_path}"
        )
    else:
        why = live_tea.get("why_unavailable") or live_tea.get("detail") or live_tea.get("reason")
        live_detail = (
            f"unavailable because {why} "
            f"(parameters {live_tea['parameter_surface']}). {execution_path}"
        )
    add(
        "Live TEA", live_tea["check_status"], live_detail.strip(),
        available=live_tea["available"],
        reason=live_tea.get("reason"),
        why_unavailable=live_tea.get("why_unavailable"),
        unavailable_reasons=live_tea.get("unavailable_reasons"),
        parent_python=live_tea.get("parent_python"),
        DISSOLVE_TEA_PYTHON=live_tea.get("DISSOLVE_TEA_PYTHON"),
        DISSOLVE_PLASTICS_PATH=live_tea.get("DISSOLVE_PLASTICS_PATH"),
        admitted_live_targets=live_tea.get("admitted_live_targets"),
        refused_grid_targets=live_tea.get("refused_grid_targets"),
        parameter_surface=live_tea.get("parameter_surface"),
        plastics_layout=live_tea.get("plastics_layout"),
        execution_path=execution_path,
    )

    return {
        "schema": "dissolve.doctor.v1",
        "ready": not any(item["status"] == "fail" for item in checks),
        "remote_calls": 0,
        "checks": checks,
    }


def print_doctor(report: dict[str, Any], console: Console) -> None:
    table = Table(title="DISSOLVE environment doctor", box=None)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")
    styles = {"pass": "green", "warn": "yellow", "fail": "red", "not_required": "dim"}
    for item in report["checks"]:
        status = str(item["status"])
        table.add_row(str(item["name"]), f"[{styles.get(status, 'white')}]{status.upper()}[/]", str(item["detail"]))
    console.print(table)
    console.print("[green]Ready[/]" if report["ready"] else "[red]Not ready[/]")


class _Store:
    """Thin session files inside cli.py. Not a port of v11 persistence.py."""

    def __init__(self, session_id: str | None = None, root: str | Path | None = None):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        if not _SESSION_RE.fullmatch(self.session_id):
            raise ValueError("Session ID must be 3-64 letters, digits, underscores, or hyphens.")
        base = Path(root or os.getenv("DISSOLVE_HOME") or Path.home() / ".dissolve")
        self.directory = base.expanduser().resolve() / "sessions" / self.session_id
        self.state_path = self.directory / "session.json"
        self.transcript_path = self.directory / "transcript.jsonl"

    def load(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not _is_v12_session(payload):
            raise ValueError("incompatible DISSOLVE session file; not overwritten")
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        body = normalize_json({
            **payload,
            "schema_version": 2,
            "session_id": self.session_id,
            "release": RELEASE,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.state_path)

    def append(self, role: str, content: str, **metadata: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        event = normalize_json({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": role, "content": content, **metadata,
        })
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


_ARG_ITEM_MAX = 48
_CONTAINER_SAMPLE = 2
_ARGS_MAX = 160
_FP_HEX = 12


def _canonical_json(value: Any) -> str:
    try:
        payload = normalize_json(value)
    except TypeError:
        payload = str(value)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _args_fingerprint(args: Any) -> str:
    blob = _canonical_json(args if args is not None else {})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:_FP_HEX]


def _render_scalar(value: Any, limit: int = _ARG_ITEM_MAX) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        text = str(value)
        return text if len(text) <= limit else text[: limit - 1] + "…"
    text = value if isinstance(value, str) else str(value)
    escaped = (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    escaped = "".join(
        ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in escaped
    )
    if len(escaped) > limit:
        escaped = escaped[: limit - 1] + "…"
    return escaped


def _render_value(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, (list, tuple)):
        if depth >= 2:
            return f"<{len(value)}>"
        bits = [_render_value(item, depth=depth + 1) for item in value[:_CONTAINER_SAMPLE]]
        extra = len(value) - _CONTAINER_SAMPLE
        if extra > 0:
            bits.append(f"…+{extra}")
        return "[" + ", ".join(bits) + "]"
    if isinstance(value, dict):
        if depth >= 2:
            return f"<{len(value)}>"
        items = list(value.items())[:_CONTAINER_SAMPLE]
        bits = [
            f"{_render_scalar(key, 24)}:{_render_value(item, depth=depth + 1)}"
            for key, item in items
        ]
        extra = len(value) - _CONTAINER_SAMPLE
        if extra > 0:
            bits.append(f"…+{extra}")
        return "{" + ", ".join(bits) + "}"
    return _render_scalar(value)


def _tool_arg_fragment(key: str, value: Any) -> str:
    return f"{_render_scalar(key)}={_render_value(value)}"


def _tool_event_summary(event: ToolEvent) -> str:
    name = _render_scalar(event.name)
    args = ", ".join(_tool_arg_fragment(k, v) for k, v in (event.args or {}).items())
    if len(args) > _ARGS_MAX:
        args = args[: _ARGS_MAX - 1] + "…"
    fingerprint = _args_fingerprint(event.args or {})
    inner = f"{args} #{fingerprint}" if args else f"#{fingerprint}"
    blob = json.dumps(event.result, ensure_ascii=False, default=str)
    return f"{name}({inner}) -> {len(blob.encode('utf-8'))} B"


_PROCESS_SHEET_HEADER = """\
[bold]Process confirmation[/]

This pane is every TEA/LCA public process variable the agent can vary.
Accept or edit; the dict you submit is what runs. Chemistry (τ, ρ, Cp,
Tm, Tb, oligomer, precipitation solubility) lives in
src/dissolve/tea_polymer_parameters.py and is expert_surface_only.
Recovery is not a knob. facilities/turbogenerator are derived from
energy_case. Commented-out setters are field_not_adjustable.

[dim]Enter to accept a value. field=value to edit. blank line or run to
submit. abort / q discards this buffer and does not run.[/]
"""
_BOOL_PROCESS_FIELDS = frozenset({
    "sell_leftover_plastic", "burn_leftover_plastic",
})


def _coerce_process_sheet_value(field: str, raw: str, current: Any) -> Any:
    text = str(raw).strip()
    if field in _BOOL_PROCESS_FIELDS:
        token = text.casefold()
        if token in {"true", "yes", "1"}:
            return True
        if token in {"false", "no", "0"}:
            return False
        raise ValueError(f"{field} must be true or false")
    if field == "energy_case":
        case = text.upper()
        if case not in tea._ENERGY_CASES:
            raise ValueError("energy_case must be C1, C2, or C3")
        return case
    if field == "duration":
        token = text.strip()
        if token.startswith("["):
            parsed = json.loads(token)
        else:
            parsed = [
                part.strip()
                for part in token.replace(";", ",").split(",")
                if part.strip()
            ]
        if not isinstance(parsed, (list, tuple)) or len(parsed) != 2:
            raise ValueError(
                "duration must be two years, for example 2025, 2055"
            )
        return (int(parsed[0]), int(parsed[1]))
    if field == "construction_schedule":
        token = text.strip()
        if token.startswith("["):
            parsed = json.loads(token)
        else:
            parsed = [
                part.strip()
                for part in token.replace(";", ",").split(",")
                if part.strip()
            ]
        if not isinstance(parsed, (list, tuple)) or len(parsed) < 1:
            raise ValueError(
                "construction_schedule must be investment fractions, "
                "for example 0.08, 0.60, 0.32"
            )
        return tuple(float(item) for item in parsed)
    if field == "lang_factor":
        if not text:
            return None
        return float(text)
    if field in {
        "precipitation_temperature_format", "precipitation_configuration",
        "target_polymer", "solvent", "depreciation",
        "steam_power_depreciation",
    }:
        return text
    number = float(text)
    if field == "irr" and number >= 1:
        raise ValueError("irr is a fraction (0.10 is 10 percent)")
    if field == "income_tax" and number >= 1:
        raise ValueError("income_tax is a fraction (0.21 is 21 percent)")
    if field == "finance_interest" and number >= 1:
        raise ValueError(
            "finance_interest is a fraction (0.08 is 8 percent)"
        )
    if field == "finance_fraction" and number > 1:
        raise ValueError(
            "finance_fraction is a fraction (0.4 is 40 percent)"
        )
    if field == "startup_FOCfrac" and number > 1:
        raise ValueError(
            "startup_FOCfrac is a fraction (1 is 100 percent)"
        )
    if field == "startup_VOCfrac" and number > 1:
        raise ValueError(
            "startup_VOCfrac is a fraction (0.75 is 75 percent)"
        )
    if field == "startup_salesfrac" and number > 1:
        raise ValueError(
            "startup_salesfrac is a fraction (0.5 is 50 percent)"
        )
    if field == "WC_over_FCI" and number > 1:
        raise ValueError(
            "WC_over_FCI is a fraction (0.05 is 5 percent)"
        )
    if field == "warehouse" and number > 1:
        raise ValueError(
            "warehouse is a fraction (0.04 is 4 percent)"
        )
    if field == "site_development" and number > 1:
        raise ValueError(
            "site_development is a fraction (0.09 is 9 percent)"
        )
    if field == "additional_piping" and number > 1:
        raise ValueError(
            "additional_piping is a fraction (0.045 is 4.5 percent)"
        )
    if field == "proratable_costs" and number > 1:
        raise ValueError(
            "proratable_costs is a fraction (0.10 is 10 percent)"
        )
    if field == "field_expenses" and number > 1:
        raise ValueError(
            "field_expenses is a fraction (0.10 is 10 percent)"
        )
    if field == "construction" and number > 1:
        raise ValueError(
            "construction is a fraction (0.20 is 20 percent)"
        )
    if field == "contingency" and number > 1:
        raise ValueError(
            "contingency is a fraction (0.4 is 40 percent)"
        )
    if field == "other_indirect_costs" and number > 1:
        raise ValueError(
            "other_indirect_costs is a fraction (0.10 is 10 percent)"
        )
    if field == "property_insurance" and number > 1:
        raise ValueError(
            "property_insurance is a fraction (0.007 is 0.7 percent)"
        )
    if field == "maintenance" and number > 1:
        raise ValueError(
            "maintenance is a fraction (0.03 is 3 percent)"
        )
    return number


def _format_sheet_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        return ", ".join(_format_sheet_value(item) for item in value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e12:
            return str(int(value)) if abs(value) >= 1 else f"{value:g}"
        return f"{value:g}"
    if value is None:
        return ""
    return str(value)


class CliApp:
    def __init__(
        self,
        *,
        session_id: str | None = None,
        model_alias: str | None = None,
        mode: str | None = None,
        store_root: str | Path | None = None,
        persist: bool = True,
        console: Console | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        quiet: bool = False,
        require_key: bool = True,
    ):
        self.console = console or Console()
        self.store = _Store(session_id, store_root)
        self.persist = persist
        self.event_sink = event_sink
        self.quiet = quiet
        stored = self.store.load() if persist else None
        meta = dict((stored or {}).get("metadata") or {})
        self.model_alias, self.model_spec = resolve_model(
            model_alias or meta.get("model") or DEFAULT_MODEL
        )
        self.mode = mode or str(meta.get("mode") or "review")
        if self.mode not in _MODE_LINE:
            raise ValueError("Mode must be review or auto")
        if require_key and not (os.getenv(self.model_spec.env_var) or "").strip():
            raise RuntimeError(f"{self.model_spec.env_var} is required for {self.model_spec.label}")
        loaded_session = (stored or {}).get("session") if stored else None
        self.session: dict[str, Any] = _scrub_session(loaded_session)
        loaded_msgs = list((stored or {}).get("messages") or [])
        self.messages: list[dict[str, Any]] = _drop_incomplete_tool_round(loaded_msgs) or [
            {"role": "system", "content": _system_prompt(self.mode)},
        ]
        self._apply_mode_prompt()
        self.last_result: TurnResult | None = None
        self.last_status = meta.get("last_status")
        self.last_tool_rounds = meta.get("last_tool_rounds")
        self.last_tool_calls = meta.get("last_tool_calls")
        self.last_usage = meta.get("last_usage")
        self._process_buffer: dict[str, Any] | None = None
        self._confirmation_sheet_submitted = False
        self._sheet_field_origin: dict[str, str] | None = None
        self._cli_direct_active = False
        self._save()

    def _apply_mode_prompt(self) -> None:
        text = _system_prompt(self.mode)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = text
        else:
            self.messages.insert(0, {"role": "system", "content": text})

    def _save(self) -> None:
        record = dict(self.session)
        record.pop("_turn", None)
        meta = {
            "model": self.model_alias,
            "model_id": self.model_spec.model,
            "mode": self.mode,
            "release": RELEASE,
        }
        if self.last_result is not None:
            meta["last_status"] = self.last_result.status
            meta["last_tool_calls"] = len(self.last_result.tool_trace)
            meta["last_tool_rounds"] = self.last_tool_rounds
            meta["last_usage"] = self.last_usage
        elif self.last_status is not None:
            meta["last_status"] = self.last_status
            if self.last_tool_calls is not None:
                meta["last_tool_calls"] = self.last_tool_calls
            meta["last_tool_rounds"] = self.last_tool_rounds
            meta["last_usage"] = self.last_usage
        if self.persist:
            self.store.save({"messages": self.messages, "session": record, "metadata": meta})

    def _append(self, role: str, content: str, **metadata: Any) -> None:
        if self.persist:
            self.store.append(role, content, **metadata)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(event)

    def banner(self) -> None:
        details = (
            f"[bold cyan]D I S S O L V E[/]\n"
            f"[dim]Advanced polymer separation engineering[/]\n\n"
            f"Model    [bold]{self.model_spec.label}[/]  ·  {self.model_spec.usage}\n"
            f"Session  [bold]{self.store.session_id}[/]  ·  mode {self.mode}\n"
            f"[dim]Type /context to inspect state, /model to switch, or quit to exit.[/]"
        )
        self.console.print(Panel(details, title="Advanced Recycling Agent", subtitle=RELEASE))

    def _show_models(self) -> None:
        table = Table(title="Models", box=None)
        table.add_column("")
        table.add_column("Alias")
        table.add_column("Model")
        table.add_column("Use")
        table.add_column("Key")
        for alias, spec in MODELS.items():
            table.add_row(
                "●" if alias == self.model_alias else "",
                alias, spec.label, spec.usage,
                "ready" if os.getenv(spec.env_var) else f"missing {spec.env_var}",
            )
        self.console.print(table)

    def _show_context(self) -> None:
        handles = self.session.get("handles") or {}
        live = []
        for name, stored in handles.items():
            if not isinstance(stored, dict):
                continue
            try:
                total = handle_total(stored)
            except (TypeError, ValueError, KeyError):
                total = None
            live.append({"handle": name, "tool": stored.get("tool"), "total": total,
                         "source_basis": stored.get("source_basis")})
        payload = {
            "polymers_in_play": list(self.session.get("polymers_in_play") or []),
            "temperatures_in_play": list(self.session.get("temperatures_in_play") or []),
            "handles": live,
        }
        self.console.print_json(data=payload)

    def _show_cost(self) -> None:
        status = self.last_result.status if self.last_result is not None else self.last_status
        calls = (
            len(self.last_result.tool_trace) if self.last_result is not None
            else self.last_tool_calls
        )
        if status is None and calls is None:
            self.console.print("[dim]No model usage in this process yet.[/]")
            return
        self.console.print(
            f"Last turn: [bold]{self.last_tool_rounds} tool round(s)[/] · "
            f"{calls if calls is not None else 0} tool call(s) · "
            f"usage {json.dumps(self.last_usage, sort_keys=True)} · status {status}."
        )

    def _print_tool_event(self, event: ToolEvent) -> None:
        self._append("tool", json.dumps(event.result), name=event.name, args=event.args)
        self._emit({"event": "tool", "name": event.name, "args": event.args, "result": event.result})
        if not self.quiet:
            line = Text(no_wrap=True)
            line.append("tool", style="cyan")
            line.append(f"  {_tool_event_summary(event)}")
            self.console.print(line, soft_wrap=True)

    def handle_command(self, line: str) -> bool:
        parts = line.strip().split()
        command = parts[0].casefold()
        argument = parts[1].casefold() if len(parts) > 1 else ""
        if command in {"quit", "exit", "q", "/quit", "/exit", "/q"}:
            return True
        if command == "/model":
            if not argument or argument == "list":
                self._show_models()
            elif argument == "current":
                self.console.print(f"{self.model_alias}: {self.model_spec.label}")
            else:
                alias, spec = resolve_model(argument)
                if not (os.getenv(spec.env_var) or "").strip():
                    raise RuntimeError(f"{spec.env_var} is required for {spec.label}")
                self.model_alias, self.model_spec = alias, spec
                self._save()
                self.console.print(f"Model switched to [bold]{spec.label}[/].")
        elif command == "/mode":
            if not argument or argument in {"list", "current"}:
                self.console.print(f"Mode: [bold]{self.mode}[/] (review | auto)")
            elif argument in _MODE_LINE:
                self.mode = argument
                self._apply_mode_prompt()
                self._save()
                self.console.print(f"Mode switched to [bold]{self.mode}[/].")
            else:
                raise ValueError("Mode must be review or auto")
        elif command == "/clear":
            self.session = new_session()
            self.messages = [{"role": "system", "content": _system_prompt(self.mode)}]
            self.last_result = None
            self.last_status = self.last_tool_rounds = self.last_tool_calls = self.last_usage = None
            self._process_buffer = None
            self._confirmation_sheet_submitted = False
            self._sheet_field_origin = None
            self._save()
            self.console.print("Messages and handles cleared.")
        elif command == "/context":
            if argument == "path":
                self.console.print(f"session: {self.store.state_path}")
                self.console.print(f"transcript: {self.store.transcript_path}")
            else:
                self._show_context()
        elif command == "/cost":
            self._show_cost()
        elif command == "/process":
            submitted = self._edit_process_sheet(
                self._process_buffer or tea.seed_public_process_config(),
                require_submit=False,
            )
            if submitted is None:
                self.console.print("[dim]Process sheet discarded.[/]")
            else:
                self._process_buffer = submitted
                self.console.print("[dim]Process sheet kept in this session buffer.[/]")
        elif command == "/harness":
            self.console.print("flat loop, no specialists")
        else:
            self.console.print(f"[yellow]Unknown command:[/] {command}")
        return False

    def _print_process_sheet(self, buffer: dict[str, Any]) -> None:
        self.console.print(_PROCESS_SHEET_HEADER)
        energy = str(buffer.get("energy_case") or "C1")
        table = Table(box=None, show_header=True, pad_edge=False)
        table.add_column("field")
        table.add_column("value")
        for field in tea.public_process_field_names(energy_case=energy):
            shown = _format_sheet_value(buffer.get(field))
            if field == "irr" and buffer.get("irr") is not None:
                shown = f"{buffer['irr']} ({float(buffer['irr']) * 100:g}%)"
            table.add_row(field, shown)
        self.console.print(table)
        missing = tea.missing_public_process_fields(buffer)
        if missing:
            self.console.print(f"[yellow]missing:[/] {', '.join(missing)}")
        preview = tea.sheet_standing_preview(buffer)
        badge = preview.get("badge")
        if badge == "validated":
            self.console.print("[green]standing:[/] VALIDATED-process")
        elif badge == "provisional":
            extra = ", ".join(preview.get("provisional_parameters") or [])
            suffix = f" ({extra})" if extra else ""
            self.console.print(f"[yellow]standing:[/] provisional{suffix}")
        elif badge == "unavailable":
            reason = preview.get("reason") or "unverifiable"
            self.console.print(
                f"[dim]standing:[/] unavailable/unverifiable ({reason})"
            )
        else:
            self.console.print(
                "[dim]standing:[/] incomplete (need polymer and solvent)"
            )

    def _edit_process_sheet(
        self,
        seed: dict[str, Any],
        *,
        require_submit: bool = True,
        prompt_fn: Callable[..., str] | None = None,
    ) -> dict[str, Any] | None:
        """Question pane. Returns the same buffer object, or None on abort."""
        buffer = seed
        ask = prompt_fn or Prompt.ask
        self._print_process_sheet(buffer)
        energy = str(buffer.get("energy_case") or "C1")
        fields = tea.public_process_field_names(energy_case=energy)
        for field in fields:
            current = buffer.get(field)
            default = _format_sheet_value(current)
            try:
                raw = str(ask(f"{field}", default=default) or "").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if not raw or raw == default:
                continue
            if raw.casefold() in {"abort", "q"}:
                return None
            if raw.casefold() == "run":
                break
            try:
                buffer[field] = _coerce_process_sheet_value(field, raw, current)
            except ValueError as error:
                self.console.print(f"[red]{error}[/]")
            if field == "energy_case":
                energy = str(buffer["energy_case"])
                if energy == "C2":
                    buffer.pop("natural_gas_price_usd_per_m3", None)
                    buffer.pop("steam_power_depreciation", None)
                else:
                    if "natural_gas_price_usd_per_m3" not in buffer:
                        buffer["natural_gas_price_usd_per_m3"] = (
                            tea._NATURAL_GAS_PRICE_USD_PER_M3
                        )
                    if "steam_power_depreciation" not in buffer:
                        buffer["steam_power_depreciation"] = (
                            tea._STEAM_POWER_DEPRECIATION_DEFAULT
                        )
        while True:
            try:
                line = str(ask(
                    "edit field=value, run, or abort", default="",
                ) or "").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if not line or line.casefold() == "run":
                missing = tea.missing_public_process_fields(buffer)
                if missing:
                    self.console.print(
                        f"[red]incomplete_process_config:[/] {', '.join(missing)}"
                    )
                    if require_submit:
                        continue
                return buffer
            if line.casefold() in {"abort", "q"}:
                return None
            if "=" not in line:
                self.console.print("[yellow]Use field=value, run, or abort.[/]")
                continue
            field, _, raw = line.partition("=")
            field = field.strip()
            public = set(tea.public_process_field_names(
                energy_case=str(buffer.get("energy_case") or "C1"),
            ))
            if field not in public:
                self.console.print(f"[yellow]unknown_process_field:[/] {field}")
                continue
            try:
                buffer[field] = _coerce_process_sheet_value(
                    field, raw, buffer.get(field),
                )
            except ValueError as error:
                self.console.print(f"[red]{error}[/]")

    def _confirm_seed(self, scenario: dict[str, Any] | None) -> dict[str, Any]:
        """Prefer a /process buffer. Never overlay model args onto it."""
        if self._process_buffer is not None:
            return self._process_buffer
        return tea.seed_public_process_config(scenario)

    def _process_confirm_applies(
        self, name: str, kwargs: dict[str, Any],
    ) -> bool:
        if name not in tea.PROCESS_CONFIRM_TOOLS:
            return False
        if name != "evaluate_process":
            return True
        return str(kwargs.get("mode") or "").strip().casefold() in {
            "evaluate", "sensitivity", "route",
        }

    def _bind_route_sheet_scalars(
        self, kwargs: dict[str, Any], buffer: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Route identity is handle + row_id. Do not inject process_config."""
        out = dict(kwargs)
        out.pop("process_config", None)
        out.pop("process_configs", None)
        out.pop("screening_shortlist", None)
        out.pop("held_process_basis", None)
        if isinstance(buffer, dict):
            for field in (
                "processing_capacity_mt_per_yr",
                "energy_case",
                "precipitation_temperature_c",
            ):
                if field not in out and buffer.get(field) is not None:
                    out[field] = buffer[field]
        return out

    def _handoff_confirm_seed(
        self, shortlist: Any, held: Any,
    ) -> dict[str, Any] | None:
        """Seed the sheet from a screening handoff. Not a cache-pair fill."""
        seed: dict[str, Any] = {}
        if isinstance(held, dict):
            seed.update(held)
        items = shortlist.get("items") if isinstance(shortlist, dict) else None
        first = None
        if isinstance(items, list):
            first = next((item for item in items if isinstance(item, dict)), None)
        if isinstance(first, dict):
            item = dict(first)
            if (
                "temperature_c" in item
                and not item.get("dissolution_temperature_c")
                and not item.get("dissolution_temp_c")
            ):
                item["dissolution_temperature_c"] = item.pop("temperature_c")
            for key in (
                "target_polymer", "target_plastic", "solvent",
                "dissolution_temperature_c", "dissolution_temp_c",
            ):
                if key in item:
                    seed[key] = item[key]
        return seed or None

    def _first_shortlist_item(self, shortlist: Any) -> dict[str, Any] | None:
        items = shortlist.get("items") if isinstance(shortlist, dict) else None
        if not isinstance(items, list):
            return None
        return next((item for item in items if isinstance(item, dict)), None)

    def _record_sheet_field_origin(
        self,
        submitted: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        seed_from_handoff: bool,
        shortlist: Any = None,
        held: Any = None,
        caller: Any = None,
    ) -> None:
        screening_item = (
            self._first_shortlist_item(shortlist) if seed_from_handoff else None
        )
        caller_keys = list(caller) if isinstance(caller, dict) else []
        held_keys = (
            list(held) if seed_from_handoff and isinstance(held, dict) else []
        )
        self._sheet_field_origin = tea.confirmation_sheet_field_origin(
            submitted,
            snapshot=snapshot,
            screening_item=screening_item,
            caller_keys=caller_keys,
            held_keys=held_keys,
        )

    def _stamp_confirmation_field_origin(self, result: Any) -> Any:
        origin = self._sheet_field_origin
        if not origin or not isinstance(result, dict):
            return result
        data = result.get("data")
        payload = data if isinstance(data, dict) else result
        rows: list[dict[str, Any]] = []
        for key in ("comparison_rows", "sensitivity_rows"):
            block = payload.get(key)
            if isinstance(block, list):
                rows.extend(
                    item for item in block if isinstance(item, dict)
                )
        if not rows:
            return result
        for row in rows:
            if "field_origin" not in row:
                row["field_origin"] = dict(origin)
        if "field_origin" not in payload:
            payload["field_origin"] = dict(origin)
        return result

    def _bind_confirmed_process(
        self, name: str, kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Later process tools run the submitted buffer, not the model dict."""
        buffer = self._process_buffer
        if buffer is None:
            return kwargs
        out = dict(kwargs)
        if name == "evaluate_tea_lca_scenarios":
            out["scenarios"] = [buffer]
            out.pop("screening_shortlist", None)
            out.pop("held_process_basis", None)
        elif name == "evaluate_process":
            mode = str(kwargs.get("mode") or "").strip().casefold()
            if mode == "route":
                return self._bind_route_sheet_scalars(kwargs, buffer)
            out["process_config"] = buffer
            out.pop("process_configs", None)
            out.pop("screening_shortlist", None)
            out.pop("held_process_basis", None)
        elif name == "analyze_tea_sensitivity":
            out["scenario"] = buffer
            out.pop("screening_shortlist", None)
            out.pop("held_process_basis", None)
        return out

    def _confirm_tool_kwargs(
        self,
        name: str,
        kwargs: dict[str, Any],
        *,
        prompt_fn: Callable[..., str] | None = None,
    ) -> dict[str, Any] | None:
        if name == "evaluate_tea_lca_scenarios":
            scenarios = list(kwargs.get("scenarios") or [])
            seed_src = scenarios[0] if scenarios else None
            if not isinstance(seed_src, dict):
                seed_src = self._handoff_confirm_seed(
                    kwargs.get("screening_shortlist"),
                    kwargs.get("held_process_basis"),
                )
            seed = self._confirm_seed(seed_src if isinstance(seed_src, dict) else None)
            snapshot = copy.deepcopy(seed)
            submitted = self._edit_process_sheet(seed, prompt_fn=prompt_fn)
            if submitted is None:
                return None
            self._process_buffer = submitted
            self._record_sheet_field_origin(
                submitted,
                snapshot,
                seed_from_handoff=not scenarios,
                shortlist=kwargs.get("screening_shortlist"),
                held=kwargs.get("held_process_basis"),
                caller=seed_src if scenarios else None,
            )
            out = dict(kwargs)
            out["scenarios"] = [submitted]
            out.pop("screening_shortlist", None)
            out.pop("held_process_basis", None)
            return out
        if name == "evaluate_process":
            mode = str(kwargs.get("mode") or "").strip().casefold()
            if mode == "route":
                seed = self._confirm_seed(None)
                snapshot = copy.deepcopy(seed)
                submitted = self._edit_process_sheet(seed, prompt_fn=prompt_fn)
                if submitted is None:
                    return None
                self._process_buffer = submitted
                self._record_sheet_field_origin(
                    submitted, snapshot, seed_from_handoff=False,
                )
                return self._bind_route_sheet_scalars(kwargs, submitted)
            configs = list(kwargs.get("process_configs") or [])
            seed_src = kwargs.get("process_config")
            if not isinstance(seed_src, dict):
                seed_src = configs[0] if configs else None
            if not isinstance(seed_src, dict):
                seed_src = self._handoff_confirm_seed(
                    kwargs.get("screening_shortlist"),
                    kwargs.get("held_process_basis"),
                )
            seed = self._confirm_seed(seed_src if isinstance(seed_src, dict) else None)
            snapshot = copy.deepcopy(seed)
            submitted = self._edit_process_sheet(seed, prompt_fn=prompt_fn)
            if submitted is None:
                return None
            self._process_buffer = submitted
            caller = seed_src if isinstance(kwargs.get("process_config"), dict) or configs else None
            self._record_sheet_field_origin(
                submitted,
                snapshot,
                seed_from_handoff=caller is None,
                shortlist=kwargs.get("screening_shortlist"),
                held=kwargs.get("held_process_basis"),
                caller=caller,
            )
            out = dict(kwargs)
            out["process_config"] = submitted
            out.pop("process_configs", None)
            out.pop("screening_shortlist", None)
            out.pop("held_process_basis", None)
            return out
        if name == "analyze_tea_sensitivity":
            seed_src = kwargs.get("scenario")
            if not isinstance(seed_src, dict):
                seed_src = self._handoff_confirm_seed(
                    kwargs.get("screening_shortlist"),
                    kwargs.get("held_process_basis"),
                )
            seed = self._confirm_seed(seed_src if isinstance(seed_src, dict) else None)
            snapshot = copy.deepcopy(seed)
            submitted = self._edit_process_sheet(seed, prompt_fn=prompt_fn)
            if submitted is None:
                return None
            self._process_buffer = submitted
            self._record_sheet_field_origin(
                submitted,
                snapshot,
                seed_from_handoff=not isinstance(kwargs.get("scenario"), dict),
                shortlist=kwargs.get("screening_shortlist"),
                held=kwargs.get("held_process_basis"),
                caller=seed_src if isinstance(kwargs.get("scenario"), dict) else None,
            )
            out = dict(kwargs)
            out["scenario"] = submitted
            out.pop("screening_shortlist", None)
            out.pop("held_process_basis", None)
            return out
        return kwargs

    def _cli_direct_dispatch(
        self, original: Callable[..., Any], name: str, kwargs: dict[str, Any],
    ) -> Any:
        armed = (
            self._cli_direct_active
            and self._process_confirm_applies(name, kwargs)
            and sys.stdin.isatty()
            and not self.quiet
        )
        if not armed:
            return original(name, **kwargs)
        if self._confirmation_sheet_submitted:
            kwargs = self._bind_confirmed_process(name, kwargs)
            return self._stamp_confirmation_field_origin(original(name, **kwargs))
        confirmed = self._confirm_tool_kwargs(name, kwargs)
        if confirmed is None:
            return {
                "success": False,
                "error": (
                    "Process confirmation aborted; the model args "
                    "did not run."
                ),
                "error_code": "process_confirmation_aborted",
            }
        kwargs = confirmed
        self._confirmation_sheet_submitted = True
        return self._stamp_confirmation_field_origin(original(name, **kwargs))

    def ask(self, query: str) -> TurnResult:
        self._append("user", query, mode=self.mode, model=self.model_alias)
        started = time.monotonic()
        with nullcontext() if self.quiet else self.console.status("[cyan]Reasoning with scientific tools…[/]"):
            result = run_turn(
                query,
                session=self.session,
                model=self.model_spec.model,
                messages=self.messages,
                on_event=self._print_tool_event,
                api_base=self.model_spec.base_url,
                api_key_env=self.model_spec.env_var,
            )
        self.last_result = result
        self.last_status = result.status
        self.last_tool_calls = len(result.tool_trace)
        self.last_tool_rounds = result.tool_rounds
        self.last_usage = result.usage
        self._append("assistant", result.answer, status=result.status)
        self._save()
        self._emit({
            "event": "turn.completed", "session_id": self.store.session_id,
            "status": result.status, "answer": result.answer,
            "model": self.model_alias, "model_id": self.model_spec.model,
            "tool_calls": len(result.tool_trace),
        })
        if self.quiet:
            return result
        self.console.print()
        if result.status in {"round_cap", "provider_error", "compaction_error"}:
            self.console.print(Panel(result.answer, title="Session budget stopped", style="yellow"))
        else:
            self.console.print(Markdown(result.answer))
        self.console.print(
            f"\n[dim]({time.monotonic() - started:.1f}s · "
            f"{len(result.tool_trace)} tool calls · {result.status})[/]\n"
        )
        return result

    def run(self) -> None:
        import agent_harness
        original_dispatch = agent_harness.dispatch

        def wrapped_dispatch(name: str, **kwargs: Any) -> Any:
            return self._cli_direct_dispatch(original_dispatch, name, kwargs)

        self.banner()
        self._cli_direct_active = True
        agent_harness.dispatch = wrapped_dispatch
        try:
            while True:
                try:
                    line = Prompt.ask("\n[bold cyan]>[/]").strip()
                    if not line:
                        continue
                    if line.startswith("/") or line.casefold() in {"quit", "exit", "q"}:
                        if self.handle_command(line):
                            break
                    else:
                        self.ask(line)
                except (EOFError, KeyboardInterrupt):
                    self.console.print("\n[dim]Session closed.[/]")
                    break
                except (RuntimeError, ValueError) as error:
                    self.console.print(f"[red]Error:[/] {error}")
        finally:
            agent_harness.dispatch = original_dispatch
            self._cli_direct_active = False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DISSOLVE advanced recycling agent")
    parser.add_argument("command", nargs="?", choices=("doctor",))
    parser.add_argument("--session")
    parser.add_argument("--model")
    parser.add_argument("--mode", choices=("review", "auto"))
    parser.add_argument("--home")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--once", metavar="QUERY")
    parser.add_argument("--stream-json", action="store_true")
    args = parser.parse_args(argv)
    if args.stream_json and not args.once:
        parser.error("--stream-json requires --once")
    console = Console()
    if args.command == "doctor":
        try:
            alias = args.model
            if args.session and alias is None:
                payload = _Store(args.session, args.home).load()
                alias = (payload or {}).get("metadata", {}).get("model")
            report = doctor_report(args.home, model_alias=alias)
        except ValueError as error:
            console.print(f"[red]DISSOLVE doctor could not resolve the model:[/] {error}")
            return 2
        print_doctor(report, console)
        return 0 if report["ready"] else 1
    try:
        event_sink = (
            (lambda event: print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True))
            if args.stream_json else None
        )
        app = CliApp(
            session_id=args.session,
            model_alias=args.model,
            mode=args.mode,
            store_root=args.home,
            persist=not args.no_persist,
            console=console,
            event_sink=event_sink,
            quiet=bool(args.stream_json),
        )
    except (RuntimeError, ValueError) as error:
        if args.stream_json:
            print(json.dumps({"event": "startup.failed", "error": str(error)}, separators=(",", ":")))
        else:
            console.print(f"[red]DISSOLVE could not start:[/] {error}")
        return 2
    if args.once:
        if not args.stream_json:
            app.banner()
        result = app.ask(args.once)
        return 0 if result.status == "ok" else 1
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
