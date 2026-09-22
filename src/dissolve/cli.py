"""Interactive DISSOLVE terminal. The loop is run_turn; this file only draws."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Sequence


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

from dissolve.agent_harness import ToolEvent, TurnResult, run_turn
from dissolve.agent_tools import SYSTEM_PROMPT
from dissolve import RELEASE, tea
from dissolve.contracts import normalize_json
from dissolve.session import SessionRecord, handle_total, new_session

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
_MODE_LINE = {
    "review": "Ask before making consequential process assumptions the user did not specify.",
    "auto": "Make reasonable process assumptions when needed and label them explicitly.",
}
_PLANNER_STAGE_CAP = 50
_PICKER_BREADTH_PRESETS = (1, 3, 5, 10)
_PICKER_BREADTH_BOUND = 20
_PickerFn = Callable[..., Any]
_InputFn = Callable[[str], str]


def _format_solvent_scope_default(stored: dict[str, Any] | None, *, origin: str) -> str:
    scope = (stored or {}).get("scope") or "all"
    return f"solvent_scope={scope}  ({origin})"


def _parse_solvents_slash(tokens: Sequence[str]) -> dict[str, Any] | None:
    """None is the no-argument path. Raises ValueError on a bad token."""
    if not tokens:
        return None
    if len(tokens) != 1:
        raise ValueError("usage: /solvents [common | all]")
    token = str(tokens[0]).strip().casefold()
    if token not in {"common", "all"}:
        raise ValueError("usage: /solvents [common | all]")
    return {"scope": token}


_SAFETY_USAGE = "usage: /safety"

# Frozen served disclosure of HAZARD_METHODS.v1.json (sha256 8540e6fe…).
# Format-only. Do not open the safety asset or call a score helper to fill this.
PUBLISHED_HAZARD_METHODS_COPY = MappingProxyType({
    "published_sha": "5ec2521",
    "measured_on_builder_sha": "1d97a737c231357f87041e42f43ec18de99db869",
    "enumerating_function": "get_available_solvents",
    "scope_token": "all",
    "scope_origin": "built_in",
    "n": 990,
    "served_gsk": 130,
    "served_green": 840,
    "served_neither": 20,
    "both_table_hits": 128,
    "leftover": (
        ("1,2-dimethoxyethane", "110-71-4"),
        ("cis-decalin", "493-01-6"),
    ),
    "mae_2dp": 0.3,
    "signed_mean_2dp": 0.01,
    "default_minimum_g_score": 6.0,
})


def format_published_hazard_methods_copy(
    copy: MappingProxyType | dict[str, Any] | None = None,
) -> str:
    row = PUBLISHED_HAZARD_METHODS_COPY if copy is None else copy
    leftover = ", ".join(f"{key} ({cas})" for key, cas in row["leftover"])
    measured = str(row["measured_on_builder_sha"])[:7]
    mae = f"{float(row['mae_2dp']):.2f}"
    signed = f"{float(row['signed_mean_2dp']):+.2f}"
    floor = row["default_minimum_g_score"]
    return (
        f"safety  published Methods {row['published_sha']}  measured {measured}\n"
        f"enumerating_function={row['enumerating_function']}  "
        f"scope={row['scope_token']}  origin={row['scope_origin']}  n={row['n']}\n"
        f"served GSK={row['served_gsk']}  GreenSolventDB={row['served_green']}  "
        f"neither={row['served_neither']}\n"
        f"both_table={row['both_table_hits']}  leftover={leftover}\n"
        f"average MAE={mae}  signed_mean={signed}  green_lookup=LIMIT 1 no ORDER BY\n"
        f"green_screen=screen_green_solvent_candidates  network=offline  "
        f"floor={floor} unsourced\n"
        f"route=screen_route_solvent_substitutions  include_pubchem=True (default)\n"
        f"card=get_solvent_safety_card  include_pubchem=True (default)\n"
    )


_PUBLISHED_HAZARD_METHODS_DETAIL = (
    "n=990 GSK=130 GreenSolventDB=840 neither=20 both=128 "
    "MAE=0.30 signed=+0.01 floor=6.0 unsourced"
)
_SAFETY_ASSET_SHA256 = (
    "88ce0d09ac28de17045702a8a283de6610b5fe1ab33aa5f90bf6e98edfd75a74"
)
_CENSUS_JSON_SHA256 = (
    "8540e6fe2f3ee780989ea15beb1b9ce0fe2aac2c81a073e271fd073997ecb150"
)
_EXPECTED_LEFTOVER = (
    ("1,2-dimethoxyethane", "110-71-4"),
    ("cis-decalin", "493-01-6"),
)


def published_hazard_methods_doctor_check(
    *,
    asset_sha256: str | None,
    census_json_path: Path,
) -> dict[str, Any]:
    """Format-only doctor row from PUBLISHED_HAZARD_METHODS_COPY. Not a query."""
    row = PUBLISHED_HAZARD_METHODS_COPY
    leftover_ok = tuple(row["leftover"]) == _EXPECTED_LEFTOVER
    mapping_ok = (
        row["n"] == 990
        and row["served_gsk"] == 130
        and row["served_green"] == 840
        and row["served_neither"] == 20
        and row["both_table_hits"] == 128
        and row["mae_2dp"] == 0.3
        and row["signed_mean_2dp"] == 0.01
        and row["default_minimum_g_score"] == 6.0
        and leftover_ok
        and str(row["measured_on_builder_sha"]).startswith("1d97a73")
    )
    pin_ok = asset_sha256 == _SAFETY_ASSET_SHA256
    if census_json_path.is_file():
        census_json = hashlib.sha256(census_json_path.read_bytes()).hexdigest()
        json_ok = census_json == _CENSUS_JSON_SHA256
    else:
        census_json = "not_shipped"
        json_ok = True
    ok = mapping_ok and pin_ok and json_ok
    mae = f"{float(row['mae_2dp']):.2f}"
    signed = f"{float(row['signed_mean_2dp']):+.2f}"
    shaped = (
        f"n={row['n']} GSK={row['served_gsk']} "
        f"GreenSolventDB={row['served_green']} neither={row['served_neither']} "
        f"both={row['both_table_hits']} MAE={mae} signed={signed} "
        f"floor={row['default_minimum_g_score']} unsourced"
    )
    return {
        "name": "Published hazard Methods",
        "status": "pass" if ok else "fail",
        "detail": _PUBLISHED_HAZARD_METHODS_DETAIL if ok else f"census drift: {shaped}",
        "n": row["n"],
        "served_gsk": row["served_gsk"],
        "served_green": row["served_green"],
        "served_neither": row["served_neither"],
        "both_table_hits": row["both_table_hits"],
        "mae_2dp": row["mae_2dp"],
        "signed_mean_2dp": row["signed_mean_2dp"],
        "default_minimum_g_score": row["default_minimum_g_score"],
        "measured_on_builder_sha": row["measured_on_builder_sha"],
        "census_json": census_json,
    }


_CONTAMINANT_USAGE = "usage: /contaminant [off | leaching | strap | swing | compare]"
_LOGP_USAGE = (
    "usage: /contaminant logp (--smiles <SMILES> | --file <path> | --job <handle> "
    "| --solvent-dft <name>) "
    "[--solvents <name,name>] [--reference <name>] [--absolute] "
    "[--literature-logp <float>]"
)
_CONTAMINANT_COMPARE_USAGE = (
    "usage: /contaminant compare  "
    "(needs a prior contaminant screen with target_polymer and contaminants)"
)


def _format_contaminant_default(stored: dict[str, Any] | None, *, origin: str) -> str:
    mode = (stored or {}).get("mode") or "off"
    return f"contaminant_mode={mode}  ({origin})"


def _parse_contaminant_logp(tokens: Sequence[str]) -> dict[str, Any]:
    """One-shot /contaminant logp. Not a persistent contaminant_mode."""
    args = [str(token) for token in tokens[1:]]
    smiles: str | None = None
    file_path: str | None = None
    job_handle: str | None = None
    solvent_dft: str | None = None
    solvents_raw: str | None = None
    reference: str | None = None
    absolute = False
    literature_logp: float | None = None
    index = 0
    while index < len(args):
        if args[index] == "--smiles":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            smiles = args[index + 1]
            index += 2
            continue
        if args[index] == "--file":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            file_path = args[index + 1]
            index += 2
            continue
        if args[index] == "--job":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            job_handle = args[index + 1]
            index += 2
            continue
        if args[index] == "--solvent-dft":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            solvent_dft = args[index + 1]
            index += 2
            continue
        if args[index] == "--solvents":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            solvents_raw = args[index + 1]
            index += 2
            continue
        if args[index] == "--reference":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            reference = args[index + 1]
            index += 2
            continue
        if args[index] == "--absolute":
            absolute = True
            index += 1
            continue
        if args[index] == "--literature-logp":
            if index + 1 >= len(args):
                raise ValueError(_LOGP_USAGE)
            try:
                literature_logp = float(args[index + 1])
            except ValueError:
                raise ValueError(_LOGP_USAGE) from None
            index += 2
            continue
        raise ValueError(_LOGP_USAGE)
    if sum(bool(item) for item in (smiles, file_path, job_handle, solvent_dft)) != 1:
        raise ValueError(_LOGP_USAGE)
    if literature_logp is not None and not smiles:
        raise ValueError(_LOGP_USAGE)
    solvents = [
        item.strip() for item in (solvents_raw or "").split(",") if item.strip()
    ]
    return {
        "logp": True,
        "smiles": smiles,
        "file": file_path,
        "job": job_handle,
        "solvent_dft": solvent_dft,
        "solvents": solvents,
        "reference": reference,
        "absolute": absolute,
        "literature_logp": literature_logp,
    }


def _parse_contaminant_slash(tokens: Sequence[str]) -> dict[str, Any] | None:
    """None is the no-argument path. Raises ValueError on a bad token."""
    if not tokens:
        return None
    if str(tokens[0]).strip().casefold() == "logp":
        return _parse_contaminant_logp(tokens)
    if len(tokens) != 1:
        raise ValueError(_CONTAMINANT_USAGE)
    token = str(tokens[0]).strip().casefold()
    if token == "compare":
        return {"compare": True}
    if token == "swing":
        return {"mode": "strap"}
    if token in {"off", "leaching", "strap"}:
        return {"mode": token}
    raise ValueError(_CONTAMINANT_USAGE)


_LITERATURE_USAGE = "usage: /literature [off | corpus | scholarly]"


def _format_literature_default(stored: dict[str, Any] | None, *, origin: str) -> str:
    mode = (stored or {}).get("mode") or "off"
    return f"literature_mode={mode}  ({origin})"


def _parse_literature_slash(tokens: Sequence[str]) -> dict[str, Any] | None:
    """None is the no-argument path. Raises ValueError on a bad token. No 'on'."""
    if not tokens:
        return None
    if len(tokens) != 1:
        raise ValueError(_LITERATURE_USAGE)
    token = str(tokens[0]).strip().casefold()
    if token in {"off", "corpus", "scholarly"}:
        return {"mode": token}
    raise ValueError(_LITERATURE_USAGE)


def _literature_status_line(stored: dict[str, Any] | None, origin: str) -> str:
    shown = stored if origin == "session" else None
    return _format_literature_default(shown, origin=origin)


def _literature_picker_options(
    current_mode: str,
) -> tuple[list[tuple[str, str]], int]:
    rows = (
        ("off", "1. off         no literature tools offered"),
        ("corpus", "2. corpus      local pinned index, offline"),
        ("scholarly", "3. scholarly   corpus plus arXiv / Scholar / WoS / patents"),
    )
    options: list[tuple[str, str]] = []
    selected = 0
    for i, (key, base) in enumerate(rows):
        suffix = "      (current)" if key == current_mode else ""
        options.append((key, base + suffix))
        if key == current_mode:
            selected = i
    return options, selected


def _contaminant_status_line(stored: dict[str, Any] | None, origin: str) -> str:
    shown = stored if origin == "session" else None
    return _format_contaminant_default(shown, origin=origin)


def _contaminant_picker_options(
    current_mode: str,
) -> tuple[list[tuple[str, str]], int]:
    rows = (
        ("off", "1. off        no contaminant embedding"),
        ("leaching", "2. leaching   wash against remaining polymers"),
        ("strap", "3. strap      stamp dissolution as the removal"),
    )
    options: list[tuple[str, str]] = []
    selected = 0
    for i, (key, base) in enumerate(rows):
        suffix = "      (current)" if key == current_mode else ""
        options.append((key, base + suffix))
        if key == current_mode:
            selected = i
    return options, selected


def _last_contaminant_screen(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Prior screen used by /contaminant compare. Missing is usage, not a feed."""
    if record is None:
        return None
    stored = record.get("last_contaminant")
    if not isinstance(stored, dict):
        stored = getattr(record, "last_contaminant", None)
    if not isinstance(stored, dict):
        return None
    target = stored.get("target_polymer")
    contaminants = (
        stored.get("contaminants")
        or stored.get("supported_contaminants")
        or stored.get("requested_contaminants")
    )
    if not target or not contaminants:
        return None
    return stored


def _interactive_stdin(*, quiet: bool) -> bool:
    """`/process` confirmation and slash pickers share this. Never prompt a pipe or campaign."""
    return bool(sys.stdin.isatty()) and not quiet


def _slash_picker_live(*, quiet: bool) -> bool:
    """TTY both sides, not quiet, not a dumb TERM. Extra stdout/TERM checks because a TUI raises."""
    if not _interactive_stdin(quiet=quiet):
        return False
    if not sys.stdout.isatty():
        return False
    try:
        from prompt_toolkit.utils import is_dumb_terminal
    except ImportError:
        return False
    return not is_dumb_terminal()


def _slash_picker_armed(*, quiet: bool, picker_fn: _PickerFn | None) -> bool:
    if picker_fn is not None:
        return True
    return _slash_picker_live(quiet=quiet)


def _run_arrow_picker(
    title: str,
    options: Sequence[tuple[Any, str]],
    *,
    selected: int = 0,
) -> Any | None:
    """Inline highlighted list. Returns the chosen value, or None on cancel / TUI failure."""
    if not options:
        return None
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.layout.margins import ScrollbarMargin
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    index = max(0, min(int(selected), len(options) - 1))
    chosen: dict[str, Any] = {"value": None, "done": False}

    def _fragments() -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [("class:title", title + "\n")]
        for i, (_value, label) in enumerate(options):
            if i == index:
                rows.append(("[SetCursorPosition]", ""))
                rows.append(("class:selected", f"> {label}\n"))
            else:
                rows.append(("class:item", f"  {label}\n"))
        rows.append((
            "class:hint",
            "Arrow keys move, Enter selects, Esc cancels",
        ))
        return rows

    kb = KeyBindings()

    @kb.add("up")
    def _up(event: Any) -> None:
        nonlocal index
        index = max(0, index - 1)

    @kb.add("down")
    def _down(event: Any) -> None:
        nonlocal index
        index = min(len(options) - 1, index + 1)

    @kb.add("pageup")
    def _pageup(event: Any) -> None:
        nonlocal index
        index = max(0, index - 5)

    @kb.add("pagedown")
    def _pagedown(event: Any) -> None:
        nonlocal index
        index = min(len(options) - 1, index + 5)

    @kb.add("home")
    def _home(event: Any) -> None:
        nonlocal index
        index = 0

    @kb.add("end")
    def _end(event: Any) -> None:
        nonlocal index
        index = len(options) - 1

    @kb.add("enter")
    def _enter(event: Any) -> None:
        chosen["value"] = options[index][0]
        chosen["done"] = True
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event: Any) -> None:
        chosen["value"] = None
        chosen["done"] = True
        event.app.exit()

    control = FormattedTextControl(
        _fragments, key_bindings=kb, focusable=True, show_cursor=False,
    )
    visible = min(len(options) + 2, 16)
    window = Window(
        content=control,
        dont_extend_height=True,
        height=Dimension(min=3, max=visible),
        right_margins=[ScrollbarMargin(display_arrows=True)],
        wrap_lines=False,
    )
    app = Application(
        layout=Layout(window),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
        style=Style.from_dict({
            "title": "bold",
            "selected": "reverse",
            "hint": "italic",
        }),
    )
    try:
        app.run()
    except (OSError, EOFError, KeyboardInterrupt, RuntimeError):
        return None
    if not chosen["done"]:
        return None
    return chosen["value"]


def _ask_picker_line(message: str, *, input_fn: _InputFn | None = None) -> str | None:
    if input_fn is not None:
        try:
            return str(input_fn(message) or "").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return None
    try:
        from prompt_toolkit.shortcuts import prompt as pt_prompt
        return str(pt_prompt(f"{message}: ") or "").strip()
    except (EOFError, KeyboardInterrupt, OSError, RuntimeError):
        return None


def _invoke_picker(
    title: str,
    options: Sequence[tuple[Any, str]],
    *,
    selected: int,
    quiet: bool,
    picker_fn: _PickerFn | None,
) -> Any | None:
    if picker_fn is not None:
        return picker_fn(title=title, options=options, selected=selected)
    if not _slash_picker_live(quiet=quiet):
        return None
    return _run_arrow_picker(title, options, selected=selected)


def _solvents_picker_options(current_scope: str) -> tuple[list[tuple[str, str]], int]:
    from dissolve import thermodynamics as thermo

    n_common = len(thermo.COMMON_INTERP_KEYS)
    n_all = len(thermo._available_solvents())
    rows = (
        ("common", f"1. common   {n_common} curated solvents"),
        ("all", f"2. all      {n_all} grid solvents"),
    )
    options: list[tuple[str, str]] = []
    selected = 0
    for i, (key, base) in enumerate(rows):
        suffix = "      (current)" if key == current_scope else ""
        options.append((key, base + suffix))
        if key == current_scope:
            selected = i
    return options, selected


def _breadth_choice_key(stored: dict[str, Any] | None) -> str:
    if not isinstance(stored, dict) or not stored:
        return "count"
    if (stored.get("branch_rule") or "count") == "window":
        return "window"
    if stored.get("breadth") == "all":
        return "all"
    return "count"


def _breadth_picker_options(
    stored: dict[str, Any] | None,
) -> tuple[list[tuple[Any, str]], int]:
    key = _breadth_choice_key(stored)
    current_count: Any = 1
    if isinstance(stored, dict) and stored:
        if key == "count":
            current_count = stored.get("breadth")
        else:
            current_count = None
    options: list[tuple[Any, str]] = []
    for preset in _PICKER_BREADTH_PRESETS:
        number = len(options) + 1
        label = f"{number}. {preset}"
        if preset == 1:
            label += "     one solvent per stage (today's behaviour)"
        if key == "count" and current_count == preset:
            label += "      (current)"
        options.append((preset, label))
    all_n = len(options) + 1
    all_label = (
        f"{all_n}. all   every qualifying candidate, cap {_PLANNER_STAGE_CAP}"
    )
    if key == "all":
        all_label += "      (current)"
    options.append(("all", all_label))
    win_n = len(options) + 1
    win_label = f"{win_n}. window..."
    if key == "window" and isinstance(stored, dict):
        win_label += f"      (current: {stored.get('selectivity_window_pct')})"
    options.append(("window", win_label))
    custom_n = len(options) + 1
    custom_label = (
        f"{custom_n}. custom...   enter 1–{_PICKER_BREADTH_BOUND}"
    )
    custom_current = (
        key == "count"
        and current_count not in _PICKER_BREADTH_PRESETS
        and current_count is not None
    )
    if custom_current:
        custom_label += f"      (current: {current_count})"
    options.append(("custom", custom_label))
    selected = 0
    if key == "all":
        selected = next(i for i, (value, _) in enumerate(options) if value == "all")
    elif key == "window":
        selected = next(
            i for i, (value, _) in enumerate(options) if value == "window"
        )
    elif current_count in _PICKER_BREADTH_PRESETS:
        selected = next(
            i for i, (value, _) in enumerate(options) if value == current_count
        )
    else:
        selected = next(
            i for i, (value, _) in enumerate(options) if value == "custom"
        )
    return options, selected


def _parse_picker_custom_breadth(raw: str) -> dict[str, Any]:
    text = str(raw).strip()
    try:
        if "." in text:
            raise ValueError
        count = int(text)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"picker custom must be an integer 1–{_PICKER_BREADTH_BOUND}"
        ) from error
    if count < 1:
        raise ValueError(
            f"picker custom must be an integer 1–{_PICKER_BREADTH_BOUND}"
        )
    if count > _PICKER_BREADTH_BOUND:
        raise ValueError(
            f"picker custom bound is {_PICKER_BREADTH_BOUND}; "
            f"/breadth still accepts 1–{_PLANNER_STAGE_CAP}"
        )
    return {"branch_rule": "count", "breadth": count}


def _solvent_status_line(scope: str, origin: str) -> str:
    stored = {"scope": scope} if origin == "session" else None
    return _format_solvent_scope_default(stored, origin=origin)


def _breadth_status_line(stored: dict[str, Any] | None, origin: str) -> str:
    if stored:
        return _format_breadth_default(stored, origin=origin)
    return "breadth=1  branch_rule=count  (built-in)"


def _format_breadth_default(stored: dict[str, Any], *, origin: str) -> str:
    rule = stored.get("branch_rule") or "count"
    if rule == "window":
        window = stored.get("selectivity_window_pct")
        return f"branch_rule=window  selectivity_window_pct={window}  ({origin})"
    breadth = stored.get("breadth")
    return f"breadth={breadth}  branch_rule=count  ({origin})"


def _parse_breadth_slash(tokens: Sequence[str]) -> dict[str, Any] | None:
    """None is the no-argument path. Raises ValueError on a bad token."""
    if not tokens:
        return None
    head = str(tokens[0]).strip().casefold()
    if head == "all" and len(tokens) == 1:
        return {"branch_rule": "count", "breadth": "all"}
    if head == "window":
        if len(tokens) != 2:
            raise ValueError("usage: /breadth window <positive number>")
        try:
            window = float(tokens[1])
        except (TypeError, ValueError) as error:
            raise ValueError("selectivity_window_pct must be a positive number") from error
        if not math.isfinite(window) or window <= 0:
            raise ValueError("selectivity_window_pct must be a positive number")
        return {"branch_rule": "window", "selectivity_window_pct": window}
    if len(tokens) != 1:
        raise ValueError("usage: /breadth [1–50 | all | window <positive number>]")
    try:
        if isinstance(tokens[0], bool) or "." in str(tokens[0]):
            raise ValueError
        count = int(str(tokens[0]).strip())
    except (TypeError, ValueError) as error:
        raise ValueError("usage: /breadth [1–50 | all | window <positive number>]") from error
    if count < 1 or count > _PLANNER_STAGE_CAP:
        raise ValueError(f"breadth must be an integer 1–{_PLANNER_STAGE_CAP} or all")
    return {"branch_rule": "count", "breadth": count}


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
    "fetch_solvent_safety_by_cid",
    "evaluate_process",
    "rank_landscape",
    "lookup_hansen_parameters",
    "screen_hansen_compatibility",
    "lookup_glass_transition",
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
    safety_digest = None
    for name, path, expected in assets:
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if name == "safety":
            safety_digest = actual
        if actual != expected:
            bad_assets.append(name)
    add(
        "Scientific assets", "pass" if not bad_assets else "fail",
        f"{len(assets)} checksums verified" if not bad_assets
        else "mismatch/missing: " + ", ".join(bad_assets),
        checked=len(assets), failures=bad_assets,
    )
    hazard = published_hazard_methods_doctor_check(
        asset_sha256=safety_digest,
        census_json_path=Path(__file__).with_name("data") / "HAZARD_METHODS.v1.json",
    )
    add(
        hazard["name"],
        hazard["status"],
        hazard["detail"],
        n=hazard["n"],
        served_gsk=hazard["served_gsk"],
        served_green=hazard["served_green"],
        served_neither=hazard["served_neither"],
        both_table_hits=hazard["both_table_hits"],
        mae_2dp=hazard["mae_2dp"],
        signed_mean_2dp=hazard["signed_mean_2dp"],
        default_minimum_g_score=hazard["default_minimum_g_score"],
        measured_on_builder_sha=hazard["measured_on_builder_sha"],
        census_json=hazard["census_json"],
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
    snapshot = safety.snapshot_doctor_facts()
    add(
        snapshot["name"], snapshot["status"], snapshot["detail"],
        path=snapshot["path"], digest=snapshot["digest"],
        expected=snapshot["expected"],
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
            f"[dim]Type /context, /process, /solvents, /safety, /contaminant, /literature, /breadth, /model, or quit to exit.[/]"
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
            seed = self._process_buffer or tea.seed_public_process_config()
            snapshot = copy.deepcopy(seed)
            submitted = self._edit_process_sheet(
                seed,
                require_submit=False,
                origin=self._sheet_field_origin,
            )
            if submitted is None:
                self.console.print("[dim]Process sheet discarded.[/]")
            else:
                self._process_buffer = submitted
                self._sheet_field_origin = (
                    tea.confirmation_sheet_field_origin_after_edit(
                        submitted,
                        snapshot=snapshot,
                        previous=self._sheet_field_origin,
                    )
                )
                self.console.print("[dim]Process sheet kept in this session buffer.[/]")
        elif command == "/solvents":
            self._handle_solvents_command(parts[1:])
        elif command == "/safety":
            self._handle_safety_command(parts[1:])
        elif command == "/contaminant":
            self._handle_contaminant_command(parts[1:])
        elif command == "/literature":
            self._handle_literature_command(parts[1:])
        elif command == "/breadth":
            self._handle_breadth_command(parts[1:])
        elif command == "/harness":
            self.console.print("flat loop, no specialists")
        else:
            self.console.print(f"[yellow]Unknown command:[/] {command}")
        return False

    def _handle_safety_command(self, tokens: Sequence[str]) -> None:
        if tokens:
            self.console.print(_SAFETY_USAGE)
            return
        self.console.file.write(format_published_hazard_methods_copy())
        self.console.file.flush()

    def _handle_solvents_command(
        self,
        tokens: Sequence[str],
        *,
        picker_fn: _PickerFn | None = None,
    ) -> None:
        try:
            stored = _parse_solvents_slash(tokens)
        except ValueError as error:
            self.console.print(f"[red]{error}[/]")
            return
        if stored is None:
            current = self.session.get("solvent_scope")
            if isinstance(current, dict) and current.get("scope") in {"common", "all"}:
                scope, origin = str(current["scope"]), "session"
            else:
                scope, origin = "all", "built-in"
            if not _slash_picker_armed(quiet=self.quiet, picker_fn=picker_fn):
                self.console.print(_solvent_status_line(scope, origin))
                return
            stored = self._pick_solvent_scope(
                scope, picker_fn=picker_fn,
            )
            if stored is None:
                self.console.print(_solvent_status_line(scope, origin))
                return
        self.session["solvent_scope"] = stored
        self._save()
        self.console.print(_format_solvent_scope_default(stored, origin="session"))

    def _pick_solvent_scope(
        self,
        current_scope: str,
        *,
        picker_fn: _PickerFn | None = None,
    ) -> dict[str, Any] | None:
        options, selected = _solvents_picker_options(current_scope)
        chosen = _invoke_picker(
            "Select solvent scope",
            options,
            selected=selected,
            quiet=self.quiet,
            picker_fn=picker_fn,
        )
        if chosen in {"common", "all"}:
            return {"scope": chosen}
        return None

    def _handle_contaminant_command(
        self,
        tokens: Sequence[str],
        *,
        picker_fn: _PickerFn | None = None,
    ) -> None:
        try:
            stored = _parse_contaminant_slash(tokens)
        except ValueError as error:
            self.console.print(f"[red]{error}[/]")
            return
        if stored and stored.get("compare"):
            self._run_contaminant_compare()
            return
        if stored and stored.get("logp"):
            self._run_contaminant_logp(
                stored.get("smiles"),
                file=stored.get("file"),
                job=stored.get("job"),
                solvent_dft=stored.get("solvent_dft"),
                solvents=list(stored.get("solvents") or []),
                reference=stored.get("reference"),
                absolute=bool(stored.get("absolute")),
                literature_logp=stored.get("literature_logp"),
            )
            return
        if stored is None:
            current = self.session.get("contaminant_mode")
            if isinstance(current, dict) and current.get("mode") in {
                "off", "leaching", "strap",
            }:
                shown, origin = current, "session"
            else:
                shown, origin = None, "built-in"
            if not _slash_picker_armed(quiet=self.quiet, picker_fn=picker_fn):
                self.console.print(_contaminant_status_line(shown, origin))
                return
            stored = self._pick_contaminant_mode(
                (shown or {}).get("mode") or "off", picker_fn=picker_fn,
            )
            if stored is None:
                self.console.print(_contaminant_status_line(shown, origin))
                return
        self.session["contaminant_mode"] = stored
        self._save()
        self.console.print(_format_contaminant_default(stored, origin="session"))

    def _run_contaminant_logp(
        self, smiles: str | None, *, file: str | None = None,
        job: str | None = None, solvent_dft: str | None = None,
        solvents: Sequence[str] | None = None,
        reference: str | None = None, absolute: bool = False,
        literature_logp: float | None = None,
    ) -> None:
        """P-1–P-4e: intake, batch, job, wave-0 solvent library, or octanol/water sanity."""
        from dissolve import cosmo_logp as cl

        if job:
            self._emit_logp_job(cl.solute_dft_job_status(job), submit=False)
            return
        if solvent_dft:
            submitted = cl.submit_solvent_dft_job(solvent_dft, background=True)
            self._emit_logp_job(submitted, submit=True)
            return
        if file:
            self._run_contaminant_logp_batch(
                file, solvents=solvents, reference=reference, absolute=absolute,
            )
            return
        if not smiles:
            self.console.print(f"[red]{_LOGP_USAGE}[/]")
            return
        result = cl.ingest_smiles(smiles)
        if not result["success"]:
            self.console.print(f"[red]{result['error_code']}[/]")
            return
        coverage = cl.solvent_route_coverage()
        estimate = cl.estimate_dft_cost(
            int(result.get("n_atoms") or 1),
            int(result.get("n_rotatable_bonds") or 0),
        )
        self.console.print(
            "logp intake  "
            f"inchikey={result['inchikey']}  "
            f"n_atoms={result['n_atoms']}  "
            f"n_rotatable_bonds={result['n_rotatable_bonds']}  "
            f"estimated_wall_min={estimate['estimated_wall_min']}  "
            "dft=not_run  "
            f"coverage table={coverage['n_table']} "
            f"orca={coverage['n_orca']}/{coverage['n_table']} "
            f"cosmobase={coverage['n_cosmobase']}/{coverage['n_table']} "
            f"orca_param={coverage['orca_parameterisation']} "
            f"cosmobase_param={coverage['cosmobase_parameterisation']}"
        )
        if literature_logp is not None:
            self._emit_logp_sanity(cl.octanol_water_sanity(smiles, literature_logp))
            return
        surface = cl.orca_solute_cosmo_path(result["inchikey"])
        if surface is None:
            submitted = cl.submit_solute_dft_job(
                smiles,
                list(solvents or []),
                reference="water" if reference is None else reference,
                absolute=absolute,
            )
            self._emit_logp_job(submitted, submit=True)
            return
        if not solvents and not absolute:
            self.console.print("reference=water (not computed)")
            return
        computed = cl.compute_delta_logd(
            smiles,
            list(solvents or []),
            reference="water" if reference is None else reference,
            absolute=absolute,
        )
        if computed.get("error_code") == "absolute_logp_refused":
            self.console.print(f"[red]{computed['error_code']}[/]  reference is required")
            return
        if computed.get("error_code") == "solvent_not_available" and not computed.get("results"):
            self.console.print(
                f"[red]{computed['error_code']}[/]  reference={computed.get('reference_query')}"
            )
            return
        self.console.print(
            "delta provenance  "
            f"reference={computed.get('reference')}  "
            f"validation_status={computed.get('validation_status')}  "
            f"validated_ok={'yes' if computed.get('validated_ok') else 'no'}  "
            "dft=not_run"
        )
        for row in computed.get("results") or []:
            if not row.get("success"):
                self.console.print(
                    f"[red]{row.get('error_code')}[/]  solvent={row.get('query')}"
                )
                continue
            self.console.print(
                "delta  "
                f"solvent={row['solvent_key']}  "
                f"reference={row['reference']}  "
                f"delta_logd={row['delta_logd']:.4f}  "
                f"route={row['route']}  "
                f"param={row['parameterisation']}  "
                f"engine={row.get('engine') or 'orca'}  "
                f"theory={row['level_of_theory']}  "
                f"n_conformers={row['n_conformers']}  "
                f"T={row['temperature']}  "
                f"validation_status={row['validation_status']}  "
                f"validated_ok={'yes' if row['validated_ok'] else 'no'}  "
                "dft=not_run"
            )
            self.console.print(
                "score  "
                f"ln_gamma_solvent={row['ln_gamma_solvent']:.6f}  "
                f"ln_gamma_reference={row['ln_gamma_reference']:.6f}  "
                f"volume_correction={row['volume_correction']:.6f}"
            )
            self.console.print(
                "digests  "
                f"solute_sha256={row['solute_sha256']}  "
                f"solvent_sha256={row['solvent_sha256']}  "
                f"reference_sha256={row['reference_sha256']}"
            )


    def _emit_logp_job(self, record: dict[str, Any], *, submit: bool) -> None:
        """P-4c: handle + estimate. Submit always prints dft=not_run."""
        handle = record.get("handle") or ""
        estimate = record.get("estimate") or {}
        reused = "yes" if record.get("reused") else "no"
        dft_token = "dft=not_run" if submit or not record.get("dft_ran") else "dft_ran=yes"
        line = (
            "logp job  "
            f"handle={handle}  "
            f"status={record.get('status')}  "
            f"reused={reused}  "
            f"estimated_wall_min={estimate.get('estimated_wall_min')}  "
            f"n_atoms={estimate.get('n_atoms')}  "
            f"n_rotatable_bonds={estimate.get('n_rotatable_bonds')}  "
            f"{dft_token}"
        )
        if record.get("validation_status"):
            line += f"  validation_status={record.get('validation_status')}"
            line += f"  validated_ok={'yes' if record.get('validated_ok') else 'no'}"
        self.console.print(line)
        if record.get("error_code"):
            self.console.print(
                f"[red]{record['error_code']}[/]  handle={handle}"
            )
        for refusal in record.get("refusals") or []:
            self.console.print(
                f"[red]{refusal.get('error_code')}[/]  "
                f"solvent={refusal.get('query') or refusal.get('solvent_key')}"
            )
        if submit:
            return
        computed = record.get("result")
        if not isinstance(computed, dict):
            return
        self.console.print(
            "delta provenance  "
            f"reference={computed.get('reference')}  "
            f"validation_status={computed.get('validation_status')}  "
            f"validated_ok={'yes' if computed.get('validated_ok') else 'no'}  "
            f"{dft_token}"
        )
        for row in computed.get("results") or []:
            if not row.get("success"):
                self.console.print(
                    f"[red]{row.get('error_code')}[/]  solvent={row.get('query')}"
                )
                continue
            self.console.print(
                "delta  "
                f"solvent={row['solvent_key']}  "
                f"reference={row['reference']}  "
                f"delta_logd={row['delta_logd']:.4f}  "
                f"route={row['route']}  "
                f"param={row['parameterisation']}  "
                f"engine={row.get('engine') or 'orca'}  "
                f"theory={row['level_of_theory']}  "
                f"n_conformers={row['n_conformers']}  "
                f"T={row['temperature']}  "
                f"validation_status={row['validation_status']}  "
                f"validated_ok={'yes' if row['validated_ok'] else 'no'}  "
                f"{dft_token}"
            )

    def _emit_logp_sanity(self, record: dict[str, Any]) -> None:
        """P-4e: literature Kow comparison. Never a gate, never validated."""
        line = (
            "sanity octanol/water  "
            f"literature={record.get('literature_logp')}  "
            f"computed={record.get('computed_logp')}  "
            f"residual={record.get('residual')}  "
            f"role={record.get('role') or 'sanity_check'}  "
            "gate=no  "
            f"validation_status={record.get('validation_status')}  "
            "validated_ok=no  "
            f"route={record.get('route')}  "
            f"param={record.get('parameterisation')}  "
            "dft=not_run"
        )
        self.console.print(line)
        if record.get("error_code"):
            self.console.print(
                f"[red]{record['error_code']}[/]  role=sanity_check  gate=no"
            )

    def _run_contaminant_logp_batch(
        self, file: str, *, solvents: Sequence[str] | None = None,
        reference: str | None = None, absolute: bool = False,
    ) -> None:
        """P-4: one SMILES per line. A refusal does not abort the rest. No new DFT."""
        from dissolve import cosmo_logp as cl

        batch = cl.compute_delta_logd_batch(
            file,
            list(solvents or []),
            reference="water" if reference is None else reference,
            absolute=absolute,
        )
        if batch.get("error_code") == "batch_file_unavailable":
            self.console.print(f"[red]{batch['error_code']}[/]  file={file}")
            return
        coverage = cl.solvent_route_coverage()
        self.console.print(
            "logp batch  "
            f"n_lines={batch['n_lines']}  "
            f"n_ok={batch['n_ok']}  "
            f"n_refused={batch['n_refused']}  "
            f"n_skipped={batch['n_skipped']}  "
            "dft=not_run  "
            f"max_concurrent_dft={batch['max_concurrent_dft']}  "
            f"coverage table={coverage['n_table']} "
            f"orca={coverage['n_orca']}/{coverage['n_table']} "
            f"orca_param={coverage['orca_parameterisation']}"
        )
        for skip in batch.get("skipped") or []:
            self.console.print(
                f"skipped  line={skip['line']}  reason={skip['reason']}"
            )
        for row in batch.get("refused") or []:
            self.console.print(
                f"[red]{row.get('error_code')}[/]  "
                f"line={row.get('line')}  smiles={row.get('smiles')}"
            )
        for computed in batch.get("results") or []:
            if not computed.get("success"):
                continue
            self.console.print(
                "delta provenance  "
                f"line={computed.get('line')}  "
                f"inchikey={computed.get('inchikey')}  "
                f"reference={computed.get('reference')}  "
                f"validation_status={computed.get('validation_status')}  "
                f"validated_ok={'yes' if computed.get('validated_ok') else 'no'}  "
                "dft=not_run"
            )
            for sol in computed.get("results") or []:
                if not sol.get("success"):
                    self.console.print(
                        f"[red]{sol.get('error_code')}[/]  "
                        f"line={computed.get('line')}  solvent={sol.get('query')}"
                    )
                    continue
                self.console.print(
                    "delta  "
                    f"line={computed.get('line')}  "
                    f"solvent={sol['solvent_key']}  "
                    f"reference={sol['reference']}  "
                    f"delta_logd={sol['delta_logd']:.4f}  "
                    f"route={sol['route']}  "
                    f"param={sol['parameterisation']}  "
                    f"engine={sol.get('engine') or 'orca'}  "
                    f"n_conformers={sol['n_conformers']}  "
                    f"validation_status={sol['validation_status']}  "
                    f"validated_ok={'yes' if sol['validated_ok'] else 'no'}  "
                    "dft=not_run"
                )


    def _pick_contaminant_mode(
        self,
        current_mode: str,
        *,
        picker_fn: _PickerFn | None = None,
    ) -> dict[str, Any] | None:
        options, selected = _contaminant_picker_options(current_mode)
        chosen = _invoke_picker(
            "Select contaminant mode",
            options,
            selected=selected,
            quiet=self.quiet,
            picker_fn=picker_fn,
        )
        if chosen in {"off", "leaching", "strap"}:
            return {"mode": chosen}
        return None

    def _handle_literature_command(
        self,
        tokens: Sequence[str],
        *,
        picker_fn: _PickerFn | None = None,
    ) -> None:
        try:
            stored = _parse_literature_slash(tokens)
        except ValueError as error:
            self.console.print(f"[red]{error}[/]")
            return
        if stored is None:
            current = self.session.get("literature_mode")
            if isinstance(current, dict) and current.get("mode") in {
                "off", "corpus", "scholarly",
            }:
                shown, origin = current, "session"
            else:
                shown, origin = None, "built-in"
            if not _slash_picker_armed(quiet=self.quiet, picker_fn=picker_fn):
                self.console.print(_literature_status_line(shown, origin))
                return
            stored = self._pick_literature_mode(
                (shown or {}).get("mode") or "off", picker_fn=picker_fn,
            )
            if stored is None:
                self.console.print(_literature_status_line(shown, origin))
                return
        self.session["literature_mode"] = stored
        self._save()
        self.console.print(_format_literature_default(stored, origin="session"))

    def _pick_literature_mode(
        self,
        current_mode: str,
        *,
        picker_fn: _PickerFn | None = None,
    ) -> dict[str, Any] | None:
        options, selected = _literature_picker_options(current_mode)
        chosen = _invoke_picker(
            "Select literature mode",
            options,
            selected=selected,
            quiet=self.quiet,
            picker_fn=picker_fn,
        )
        if chosen in {"off", "corpus", "scholarly"}:
            return {"mode": chosen}
        return None

    def _run_contaminant_compare(self) -> None:
        prior = _last_contaminant_screen(self.session)
        if prior is None:
            self.console.print(_CONTAMINANT_COMPARE_USAGE)
            return
        from dissolve import contaminants
        from dissolve.contracts import parse_tool_result

        raw = contaminants.compare_contaminant_removal_modes(
            prior["target_polymer"],
            prior.get("contaminants")
            or prior.get("supported_contaminants")
            or prior.get("requested_contaminants"),
            other_polymers=prior.get("other_polymers"),
            solvents=prior.get("solvents"),
            max_temperature_c=prior.get("max_temperature_c", prior.get("temperature_max_c")),
        )
        parsed = parse_tool_result(raw)
        display = parsed.get("display")
        data = parsed["data"]
        if display:
            self.console.print(display)
        else:
            self.console.print(
                f"compare recommended_mode={data.get('recommended_mode')}  "
                f"success={data.get('success')}"
            )

    def _handle_breadth_command(
        self,
        tokens: Sequence[str],
        *,
        picker_fn: _PickerFn | None = None,
        input_fn: _InputFn | None = None,
    ) -> None:
        try:
            stored = _parse_breadth_slash(tokens)
        except ValueError as error:
            self.console.print(f"[red]{error}[/]")
            return
        if stored is None:
            current = self.session.get("planner_breadth")
            origin = (
                "session"
                if isinstance(current, dict) and current
                else "built-in"
            )
            shown = current if origin == "session" else None
            if not _slash_picker_armed(quiet=self.quiet, picker_fn=picker_fn):
                self.console.print(_breadth_status_line(shown, origin))
                return
            stored = self._pick_planner_breadth(
                shown, picker_fn=picker_fn, input_fn=input_fn,
            )
            if stored is None:
                self.console.print(_breadth_status_line(shown, origin))
                return
        self.session["planner_breadth"] = stored
        self._save()
        self.console.print(_format_breadth_default(stored, origin="session"))

    def _pick_planner_breadth(
        self,
        current: dict[str, Any] | None,
        *,
        picker_fn: _PickerFn | None = None,
        input_fn: _InputFn | None = None,
    ) -> dict[str, Any] | None:
        options, selected = _breadth_picker_options(current)
        chosen = _invoke_picker(
            "Select planner breadth",
            options,
            selected=selected,
            quiet=self.quiet,
            picker_fn=picker_fn,
        )
        if chosen in _PICKER_BREADTH_PRESETS:
            return {"branch_rule": "count", "breadth": int(chosen)}
        if chosen == "all":
            return _parse_breadth_slash(["all"])
        if chosen == "window":
            follow = _ask_picker_line(
                "selectivity_window_pct", input_fn=input_fn,
            )
            if not follow:
                return None
            try:
                return _parse_breadth_slash(["window", follow])
            except ValueError as error:
                self.console.print(f"[red]{error}[/]")
                return None
        if chosen == "custom":
            follow = _ask_picker_line(
                f"custom breadth 1–{_PICKER_BREADTH_BOUND}",
                input_fn=input_fn,
            )
            if not follow:
                return None
            try:
                return _parse_picker_custom_breadth(follow)
            except ValueError as error:
                self.console.print(f"[red]{error}[/]")
                return None
        return None

    def _print_process_sheet(
        self,
        buffer: dict[str, Any],
        origin: dict[str, str] | None = None,
    ) -> None:
        self.console.print(_PROCESS_SHEET_HEADER)
        energy = str(buffer.get("energy_case") or "C1")
        rows: list[tuple[str, str, str, str]] = []
        for field in tea.public_process_field_names(energy_case=energy):
            shown = _format_sheet_value(buffer.get(field))
            if field == "irr" and buffer.get("irr") is not None:
                shown = f"{buffer['irr']} ({float(buffer['irr']) * 100:g}%)"
            token = tea.confirmation_sheet_row_origin(
                field, buffer, origin=origin,
            )
            units = tea.confirmation_sheet_row_units(field, buffer)
            rows.append((field, shown, units, token))
        for field, value in tea.confirmation_sheet_derived_energy_case_rows(
            buffer,
        ):
            rows.append((
                field,
                _format_sheet_value(value),
                "",
                tea.confirmation_sheet_derived_energy_case_label(),
            ))
        for field in tea.confirmation_sheet_not_on_this_instance(buffer):
            rows.append((
                field,
                tea.confirmation_sheet_not_on_this_instance_label(buffer),
                "",
                "",
            ))
        widths = tea.confirmation_sheet_column_widths(rows)
        line_width = max(int(self.console.width), 1)
        header = tea.confirmation_sheet_format_row(
            "field", "value", "units", "origin", widths,
            line_width=line_width,
        )
        for visual in header.splitlines():
            self.console.print(visual)
        for field, shown, units, token in rows:
            formatted = tea.confirmation_sheet_format_row(
                field, shown, units, token, widths,
                line_width=line_width,
            )
            for visual in formatted.splitlines():
                self.console.print(visual)
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
        origin: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Question pane. Returns the same buffer object, or None on abort."""
        buffer = seed
        ask = prompt_fn or Prompt.ask
        self._print_process_sheet(buffer, origin=origin)
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

    def _preview_sheet_field_origin(
        self,
        submitted: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        seed_from_handoff: bool,
        shortlist: Any = None,
        held: Any = None,
        caller: Any = None,
    ) -> dict[str, str]:
        screening_item = (
            self._first_shortlist_item(shortlist) if seed_from_handoff else None
        )
        caller_keys = list(caller) if isinstance(caller, dict) else []
        held_keys = (
            list(held) if seed_from_handoff and isinstance(held, dict) else []
        )
        return tea.confirmation_sheet_field_origin(
            submitted,
            snapshot=snapshot,
            screening_item=screening_item,
            caller_keys=caller_keys,
            held_keys=held_keys,
        )

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
        self._sheet_field_origin = self._preview_sheet_field_origin(
            submitted,
            snapshot,
            seed_from_handoff=seed_from_handoff,
            shortlist=shortlist,
            held=held,
            caller=caller,
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
        if name == "evaluate_process":
            mode = str(kwargs.get("mode") or "").strip().casefold()
            if mode == "route":
                return self._bind_route_sheet_scalars(kwargs, buffer)
            out["process_config"] = buffer
            out.pop("process_configs", None)
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
        if name == "evaluate_process":
            mode = str(kwargs.get("mode") or "").strip().casefold()
            if mode == "route":
                seed = self._confirm_seed(None)
                snapshot = copy.deepcopy(seed)
                preview = self._preview_sheet_field_origin(
                    seed, snapshot, seed_from_handoff=False,
                )
                submitted = self._edit_process_sheet(
                    seed, prompt_fn=prompt_fn, origin=preview,
                )
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
            ingest_items: list[dict[str, Any]] = []
            if isinstance(kwargs.get("process_config"), dict):
                ingest_items.append(kwargs["process_config"])
            ingest_items.extend(
                item for item in configs if isinstance(item, dict)
            )
            for item in ingest_items:
                tea._refuse_process_config_ingest(item)
            seed = self._confirm_seed(seed_src if isinstance(seed_src, dict) else None)
            snapshot = copy.deepcopy(seed)
            caller = seed_src if isinstance(kwargs.get("process_config"), dict) or configs else None
            preview = self._preview_sheet_field_origin(
                seed,
                snapshot,
                seed_from_handoff=caller is None,
                shortlist=kwargs.get("screening_shortlist"),
                held=kwargs.get("held_process_basis"),
                caller=caller,
            )
            submitted = self._edit_process_sheet(
                seed, prompt_fn=prompt_fn, origin=preview,
            )
            if submitted is None:
                return None
            self._process_buffer = submitted
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
        return kwargs

    def _cli_direct_dispatch(
        self, original: Callable[..., Any], name: str, kwargs: dict[str, Any],
    ) -> Any:
        armed = (
            self._cli_direct_active
            and self._process_confirm_applies(name, kwargs)
            and _interactive_stdin(quiet=self.quiet)
        )
        if not armed:
            return original(name, **kwargs)
        if self._confirmation_sheet_submitted:
            kwargs = self._bind_confirmed_process(name, kwargs)
            return self._stamp_confirmation_field_origin(original(name, **kwargs))
        try:
            confirmed = self._confirm_tool_kwargs(name, kwargs)
        except tea._ScenarioInputError as error:
            payload: dict[str, Any] = {
                "success": False,
                "error": str(error),
                "error_code": error.error_code,
            }
            payload.update(error.details)
            return payload
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
        from dissolve import agent_harness
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
