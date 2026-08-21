#!/usr/bin/env python3
"""TEA pre-flight. Proves the config is what you asked for BEFORE spending a run.

Why this exists: on 2026-08-21 the model sent
  process_config={"polymer":"LDPE","solvent":"Dodecane","temperature_c":105.0,"solubility_pct":14.5}
Three of those four field names do not exist on process_config.
seed_public_process_config dropped them, filled dissolution_capacity=3.0 from
first_run_sheet_defaults, and missing_public_process_fields only looked at the
three-name sheet tuple. A four-field public rewrite then advertised SAFE TO RUN
while _scenario_config(..., require_complete_twelve=True) still missed
solvent_price_usd_per_kg, which is not in first_run_sheet_defaults.

SAFE TO RUN means the caller dict is accepted by that evaluate-mode gate, every
intent value was actually named on the dict, and nothing was substituted from a
default. Silent defaults are listed by predicate, not a nine-name watch list.
This module never calls evaluate_process / _live / _run.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
for _path in (str(_REPO), str(_REPO / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea

_C2_DROPPED = frozenset({
    "natural_gas_price_usd_per_m3",
    "steam_power_depreciation",
})
_GRID_VOCABULARY = frozenset({"polymer", "solubility_pct"})


def _screening_vocabulary() -> frozenset[str]:
    return (
        (frozenset(tea._SCREENING_ITEM_KEYS) - tea._SCENARIO_ALLOWED_KEYS)
        | _GRID_VOCABULARY
    )


def _public_name(key: str) -> str | None:
    """Public sheet name for a process_config key, or None if it is not one."""
    token = str(key)
    if token not in tea._SCENARIO_ALLOWED_KEYS:
        return None
    return tea._public_name_for_inherit_key(token) or token


def _named_publics(config: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in config:
        public = _public_name(str(key))
        if public is not None:
            names.add(public)
    return names


def _values_match(public_name: str, want: Any, got: Any) -> bool:
    if want is got:
        return True
    if got is None or want is None:
        return False
    if public_name == "solvent":
        try:
            return (
                tea._resolve_tea_solvent(want)["canonical"]
                == tea._resolve_tea_solvent(got)["canonical"]
            )
        except (tea._ScenarioInputError, ValueError, TypeError, KeyError):
            return str(want).strip().lower() == str(got).strip().lower()
    if public_name == "target_polymer":
        try:
            return tea._resolve_polymer(want) == tea._resolve_polymer(got)
        except Exception:
            return str(want).strip() == str(got).strip()
    try:
        return abs(float(want) - float(got)) < 1e-9
    except (TypeError, ValueError):
        return want == got


def _got_for(public_name: str, intent_key: str, seeded: dict[str, Any], config: dict[str, Any]) -> Any:
    if public_name in seeded and tea._scenario_value_present(seeded.get(public_name)):
        return seeded[public_name]
    if intent_key in config:
        return config[intent_key]
    if public_name in config:
        return config[public_name]
    return seeded.get(public_name)


def _collisions(config: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, tuple[str, Any]] = {}
    found: list[dict[str, Any]] = []
    for key, value in config.items():
        public = _public_name(str(key))
        if public is None:
            continue
        if public in seen:
            prior_key, prior_value = seen[public]
            if not _values_match(public, prior_value, value):
                found.append({
                    "public": public,
                    "keys": [prior_key, str(key)],
                    "values": [prior_value, value],
                })
        else:
            seen[public] = (str(key), value)
    return found


def _energy_case(config: dict[str, Any]) -> str:
    raw = config.get("energy_case") or "C1"
    token = str(raw).upper().strip() or "C1"
    return token


@dataclass
class PreflightReport:
    verdict: str
    unrecognised: list[str] = field(default_factory=list)
    screening_vocabulary: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    lost_reasons: dict[str, str] = field(default_factory=dict)
    collisions: list[dict[str, Any]] = field(default_factory=list)
    dropped_for_energy_case: list[str] = field(default_factory=list)
    silent_defaults: dict[str, Any] = field(default_factory=dict)
    sheet_missing: list[str] = field(default_factory=list)
    evaluate_missing: list[str] = field(default_factory=list)
    runnable: bool = False
    run_error_code: str | None = None
    run_error: str | None = None
    serve_key_n: int | None = None
    energy_case: str = "C1"

    @property
    def ok(self) -> bool:
        return self.verdict == "SAFE TO RUN"


def check(intent: dict[str, Any], config: dict[str, Any]) -> PreflightReport:
    """Inspect a process_config against intent. Never runs BioSTEAM."""
    config = dict(config)
    intent = dict(intent)
    energy = _energy_case(config)
    accepted = tea._SCENARIO_ALLOWED_KEYS
    unrecognised = sorted(str(key) for key in config if str(key) not in accepted)
    screening = _screening_vocabulary()
    screening_hits = sorted(key for key in unrecognised if key in screening)

    named = _named_publics(config)
    collisions = _collisions(config)
    if unrecognised or collisions:
        seeded = {}
    else:
        seeded = tea.seed_public_process_config(dict(config))

    dropped: list[str] = []
    if energy == "C2":
        for key in config:
            public = _public_name(str(key))
            if public in _C2_DROPPED:
                dropped.append(public)
        dropped = sorted(set(dropped))

    lost: list[str] = []
    reasons: dict[str, str] = {}
    for key, want in sorted(intent.items(), key=lambda item: str(item[0])):
        public = _public_name(str(key)) or str(key)
        got = _got_for(public, str(key), seeded, config)
        if public not in named and str(key) not in config:
            lost.append(str(key))
            reasons[str(key)] = (
                f"not named in config; seeded {public}={got!r}"
            )
            continue
        if public in dropped:
            lost.append(str(key))
            reasons[str(key)] = f"dropped for energy_case={energy}"
            continue
        if not _values_match(public, want, got):
            lost.append(str(key))
            reasons[str(key)] = f"asked={want!r} got={got!r}"

    silent: dict[str, Any] = {}
    for key, value in seeded.items():
        if key in named:
            continue
        if not tea._scenario_value_present(value):
            continue
        silent[str(key)] = value

    sheet_missing = list(tea.missing_public_process_fields(seeded))
    evaluate_missing = list(tea._missing_required_public_fields(config))

    runnable = False
    run_error_code = None
    run_error = None
    serve_key_n = None
    try:
        normalized = tea._scenario_config(
            dict(config), require_complete_twelve=True,
        )
        runnable = True
        serve_key_n = len(json.loads(tea._config_key(normalized)))
    except tea._ScenarioInputError as error:
        run_error_code = error.error_code
        run_error = str(error)
        details = getattr(error, "details", {}) or {}
        if evaluate_missing == [] and details.get("missing"):
            evaluate_missing = list(details["missing"])
    except (ValueError, KeyError, TypeError) as error:
        run_error_code = type(error).__name__
        run_error = str(error)

    refuse = bool(
        unrecognised or lost or collisions or dropped or not runnable
    )
    report = PreflightReport(
        verdict="REFUSE" if refuse else "SAFE TO RUN",
        unrecognised=unrecognised,
        screening_vocabulary=screening_hits,
        lost=lost,
        lost_reasons=reasons,
        collisions=collisions,
        dropped_for_energy_case=dropped,
        silent_defaults=silent,
        sheet_missing=sheet_missing,
        evaluate_missing=evaluate_missing,
        runnable=runnable,
        run_error_code=run_error_code,
        run_error=run_error,
        serve_key_n=serve_key_n,
        energy_case=energy,
    )
    return report


def preflight(intent: dict[str, Any], config: dict[str, Any]) -> int:
    """intent: what was asked for, in any accepted process_config name.
       config: what is about to be passed as process_config."""
    report = check(intent, config)
    print("=" * 74)
    print("INTENT (what you asked for)")
    for key, value in sorted(intent.items()):
        print(f"   {key:<30} {value!r}")

    print("\nFIELD-NAME CHECK — process_config keys vs tea._SCENARIO_ALLOWED_KEYS")
    print(f"   unrecognised keys: {report.unrecognised if report.unrecognised else 'none'}")
    if report.screening_vocabulary:
        print(
            "   screening/planner vocabulary, not process_config: "
            f"{report.screening_vocabulary}"
        )

    print("\nDUAL-KEY COLLISION — public + internal names for the same field")
    if report.collisions:
        for item in report.collisions:
            print(
                f"   CONFLICT  {item['public']:<28} "
                f"keys={item['keys']} values={item['values']!r}"
            )
    else:
        print("   none")
    if report.dropped_for_energy_case:
        print(
            f"   dropped for energy_case={report.energy_case}: "
            f"{report.dropped_for_energy_case}"
        )

    print("\nINTENT SURVIVAL — named on the caller dict, value reached the seed")
    named = _named_publics(config)
    if report.unrecognised or report.collisions:
        seeded = {}
    else:
        seeded = tea.seed_public_process_config(dict(config))
    lost_set = set(report.lost)
    for key, want in sorted(intent.items()):
        public = _public_name(str(key)) or str(key)
        got = _got_for(public, str(key), seeded, config)
        mark = "LOST" if key in lost_set else "OK  "
        extra = ""
        if key in report.lost_reasons:
            extra = f"  ({report.lost_reasons[key]})"
        elif public not in named:
            extra = "  (not named in config)"
        print(
            f"   {mark}  {key:<30} asked={want!r:<18} got={got!r}{extra}"
        )

    print(
        "\nSILENT DEFAULTS — seeded fields the caller did not name "
        f"({len(report.silent_defaults)})"
    )
    if not report.silent_defaults:
        print("   none")
    else:
        for key in sorted(report.silent_defaults):
            star = ""
            if key == "dissolution_capacity":
                star = (
                    "  <-- NOT in _SHEET_REQUIRED_FIELDS: a wrong value "
                    "here runs SILENTLY"
                )
            print(
                f"   default  {key:<32} {report.silent_defaults[key]!r}{star}"
            )

    print(
        f"\nSHEET REQUIRED (engine three-tuple): "
        f"{report.sheet_missing if report.sheet_missing else 'none'}"
    )
    print(
        "EVALUATE TWELVE "
        "(_scenario_config require_complete_twelve=True on the caller dict): "
        f"{report.evaluate_missing if report.evaluate_missing else 'none'}"
    )
    if report.run_error_code:
        print(f"   error_code={report.run_error_code}: {report.run_error}")

    print("\nCOMPLETE BASIS — serve key of the evaluate-normalized config")
    if report.runnable and report.serve_key_n is not None:
        print(
            f"   {report.serve_key_n} fields under energy_case="
            f"{report.energy_case}"
        )
    else:
        print("   n/a — caller dict is not evaluate-runnable")

    print("\n" + "=" * 74)
    print(f"VERDICT: {report.verdict}")
    if report.unrecognised:
        print(
            f"  - {len(report.unrecognised)} unrecognised field name(s): "
            f"{report.unrecognised}"
        )
    if report.lost:
        print(
            f"  - {len(report.lost)} requested value(s) did NOT survive: "
            f"{report.lost}"
        )
    if report.collisions:
        print(f"  - {len(report.collisions)} dual-key conflict(s)")
    if not report.runnable:
        print("  - not runnable: SAFE TO RUN requires the evaluate twelve-gate")
    print("=" * 74)
    return 0 if report.ok else 1


def admitted_solvent_price_usd_per_kg(solvent: str) -> float:
    identity = tea._resolve_tea_solvent(solvent)
    assumption = identity.get("assumption") or {}
    price = assumption.get("price_usd_per_kg")
    if price is None:
        raise RuntimeError(f"no admitted solvent-price basis for {solvent!r}")
    return float(price)


def complete_public_twelve(
    *,
    target_polymer: str = "LDPE",
    solvent: str = "Dodecane",
    dissolution_temperature_c: float = 105.0,
    dissolution_capacity: float = 14.54225715,
    energy_case: str = "C1",
) -> dict[str, Any]:
    """The twelve public D-8 fields evaluate actually requires."""
    defaults = tea.first_run_sheet_defaults(energy_case=energy_case)
    config: dict[str, Any] = {}
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        if name == "target_polymer":
            config[name] = target_polymer
        elif name == "solvent":
            config[name] = solvent
        elif name == "dissolution_temperature_c":
            config[name] = dissolution_temperature_c
        elif name == "dissolution_capacity":
            config[name] = dissolution_capacity
        elif name == "solvent_price_usd_per_kg":
            config[name] = admitted_solvent_price_usd_per_kg(solvent)
        elif name == "energy_case":
            config[name] = energy_case
        else:
            config[name] = defaults[name]
    return config


def main(argv: list[str] | None = None) -> int:
    del argv
    print("\n### CASE A — the dict the model actually sent (from the transcript)\n")
    preflight(
        intent={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 105.0,
            "dissolution_capacity": 14.54225715,
        },
        config={
            "polymer": "LDPE",
            "solvent": "Dodecane",
            "temperature_c": 105.0,
            "solubility_pct": 14.54225715,
        },
    )
    print("\n\n### CASE B — the same intent written in REAL field names\n")
    print(
        "(Four public names are not a plant. Evaluate still needs the twelve, "
        "including solvent_price_usd_per_kg, which first_run_sheet_defaults "
        "does not supply.)\n"
    )
    preflight(
        intent={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 105.0,
            "dissolution_capacity": 14.54225715,
        },
        config={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 105.0,
            "dissolution_capacity": 14.54225715,
        },
    )
    print("\n\n### CASE C — the evaluate twelve, including admitted Dodecane price\n")
    twelve = complete_public_twelve()
    preflight(intent=dict(twelve), config=twelve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
