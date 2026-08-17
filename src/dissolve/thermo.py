"""Direct-lookup thermodynamic solubility.

v12 premise: **the engine returns measured values or it refuses.** There is no
interpolation, no curve fit, no extrapolation, and therefore no method label,
no regime label, and no per-pair validity window to keep in sync with anything.
A temperature that is not a grid node is not a question this engine answers.

That deletes an entire class of defect by construction. In v11 the same surface
carried `grid_exact` vs `grid_interpolation` labels, an Apelblat fit with
per-pair `t_min_c`/`t_max_c` bounds, three extrapolation regimes with two
thresholds, and a governed parity digest that could not observe any of it. Every
one of those existed to describe *how far from the data* an answer had strayed.
Answer only from the data and the description is unnecessary.

The data: `solubility_grid`, 253,456 rows over 12 polymers x 990 solvents x 28
temperatures (25-160 C, 5 C steps). 248,378 rows are valid; 5,078 carry an
explicit `invalid_reason` (`exact_100_artifact`, `nonpositive`) and are refused
rather than served.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import duckdb

_ASSET = Path(str(files("dissolve").joinpath("data/thermodynamics.duckdb")))
_LOCAL = threading.local()

# The grid is a fixed ladder. Nothing between these rungs is answerable.
GRID_TEMPERATURES_C: tuple[int, ...] = tuple(range(25, 161, 5))


def connection() -> duckdb.DuckDBPyConnection:
    """One read-only handle per thread. The asset is never written."""
    existing = getattr(_LOCAL, "connection", None)
    if existing is None:
        existing = duckdb.connect(str(_ASSET), read_only=True)
        _LOCAL.connection = existing
    return existing


@lru_cache(maxsize=1)
def available_polymers() -> tuple[str, ...]:
    rows = connection().execute(
        "select distinct polymer from solubility_grid order by 1"
    ).fetchall()
    return tuple(row[0] for row in rows)


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """Map every registered alias to the key the grid actually stores.

    Identity is data, not string shape. `water` and `h2o` are the same solvent
    because the asset says so, not because they look alike.
    """
    rows = connection().execute(
        "select alias, interp_key from solvent_aliases"
    ).fetchall()
    index = {str(alias).strip().casefold(): str(key) for alias, key in rows}
    for (name,) in connection().execute(
        "select distinct solvent from solubility_grid"
    ).fetchall():
        index.setdefault(str(name).strip().casefold(), str(name))
    return index


def resolve_solvent(name: str) -> str | None:
    """Registered identity for a solvent name, or None if unregistered."""
    return _alias_index().get(str(name).strip().casefold())


def resolve_polymer(name: str) -> str | None:
    key = str(name).strip().casefold()
    for polymer in available_polymers():
        if polymer.casefold() == key:
            return polymer
    return None


def lookup(polymer: str, solvent: str, temperature_c: float) -> dict[str, Any]:
    """Return the measured solubility, or an explicit refusal.

    Every refusal names its own reason. A caller must never have to infer why a
    value is absent, and must never receive a number that was not measured.
    """
    resolved_polymer = resolve_polymer(polymer)
    if resolved_polymer is None:
        return _refusal("unknown_polymer", polymer=polymer)

    resolved_solvent = resolve_solvent(solvent)
    if resolved_solvent is None:
        return _refusal("unknown_solvent", solvent=solvent)

    if float(temperature_c) != int(temperature_c) or int(temperature_c) not in GRID_TEMPERATURES_C:
        # The defining refusal of v12. v11 would have interpolated here.
        return _refusal(
            "temperature_off_grid",
            temperature_c=temperature_c,
            nearest_grid_temperatures_c=_nearest_nodes(float(temperature_c)),
        )

    row = connection().execute(
        "select solubility_pct, is_valid, invalid_reason, source_table "
        "from solubility_grid where polymer = ? and solvent = ? and temperature_c = ?",
        [resolved_polymer, resolved_solvent, int(temperature_c)],
    ).fetchone()

    if row is None:
        return _refusal(
            "pair_not_measured",
            polymer=resolved_polymer, solvent=resolved_solvent,
            temperature_c=int(temperature_c),
        )

    solubility_pct, is_valid, invalid_reason, source_table = row
    if not is_valid:
        return _refusal(
            "measurement_rejected",
            polymer=resolved_polymer, solvent=resolved_solvent,
            temperature_c=int(temperature_c),
            invalid_reason=invalid_reason,
        )

    return {
        "available": True,
        "polymer": resolved_polymer,
        "solvent": resolved_solvent,
        "temperature_c": int(temperature_c),
        "solubility_pct": float(solubility_pct),
        "source_table": source_table,
    }


def _refusal(reason: str, **detail: Any) -> dict[str, Any]:
    return {"available": False, "refusal": reason, **detail}


def _nearest_nodes(temperature_c: float) -> list[int]:
    below = [t for t in GRID_TEMPERATURES_C if t <= temperature_c]
    above = [t for t in GRID_TEMPERATURES_C if t >= temperature_c]
    return [t for t in (below[-1] if below else None, above[0] if above else None)
            if t is not None]


def measured_temperatures(polymer: str, solvent: str) -> tuple[int, ...]:
    """Grid nodes where this pair has a valid measurement. May be empty."""
    resolved_polymer = resolve_polymer(polymer)
    resolved_solvent = resolve_solvent(solvent)
    if resolved_polymer is None or resolved_solvent is None:
        return ()
    rows = connection().execute(
        "select temperature_c from solubility_grid "
        "where polymer = ? and solvent = ? and is_valid order by 1",
        [resolved_polymer, resolved_solvent],
    ).fetchall()
    return tuple(int(row[0]) for row in rows)
