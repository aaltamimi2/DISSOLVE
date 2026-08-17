"""Two-sided tests for the direct-lookup engine.

Every test here has a must-serve side and a must-refuse side. A lookup engine
can fail by inventing values it does not have and by refusing values it does;
a suite that only checks one direction cannot tell you which you have.
"""

from __future__ import annotations

import pytest

from dissolve import thermo


# --- must serve -------------------------------------------------------------

def test_grid_node_returns_a_measured_value():
    result = thermo.lookup("LDPE", "dodecane", 140)
    assert result["available"] is True
    assert isinstance(result["solubility_pct"], float)
    assert result["temperature_c"] == 140
    assert result["source_table"]


def test_every_polymer_serves_at_some_node():
    for polymer in thermo.available_polymers():
        served = any(
            thermo.lookup(polymer, "dodecane", t)["available"]
            for t in thermo.GRID_TEMPERATURES_C
        )
        assert served, f"{polymer} served nothing across the whole grid"


def test_alias_resolves_to_the_same_row():
    direct = thermo.lookup("LDPE", "dodecane", 100)
    assert direct["available"] is True
    assert thermo.resolve_solvent("DODECANE") == thermo.resolve_solvent("dodecane")


# --- must refuse ------------------------------------------------------------

@pytest.mark.parametrize("temperature", [26, 27.5, 142, 24, 161, 1000])
def test_off_grid_temperature_is_refused_not_interpolated(temperature):
    """The defining property of v12. v11 would have returned a number here."""
    result = thermo.lookup("LDPE", "dodecane", temperature)
    assert result["available"] is False
    assert result["refusal"] == "temperature_off_grid"
    assert "solubility_pct" not in result


def test_unknown_polymer_is_refused_by_name():
    result = thermo.lookup("PEEK", "dodecane", 140)
    assert result["available"] is False
    assert result["refusal"] == "unknown_polymer"


def test_unknown_solvent_is_refused_by_name():
    result = thermo.lookup("LDPE", "not_a_real_solvent", 140)
    assert result["available"] is False
    assert result["refusal"] == "unknown_solvent"


def test_every_refusal_names_its_reason():
    """A caller must never have to infer why a value is absent."""
    refusals = [
        thermo.lookup("PEEK", "dodecane", 140),
        thermo.lookup("LDPE", "not_a_real_solvent", 140),
        thermo.lookup("LDPE", "dodecane", 142),
    ]
    for result in refusals:
        assert result["available"] is False
        assert result["refusal"]
        assert "solubility_pct" not in result


# --- the engine must not quietly widen its own domain -----------------------

def test_grid_ladder_matches_the_asset():
    """If the asset gains temperatures, this fails loudly rather than silently
    serving nodes the constant does not know about."""
    rows = thermo.connection().execute(
        "select distinct temperature_c from solubility_grid order by 1"
    ).fetchall()
    assert tuple(int(r[0]) for r in rows) == thermo.GRID_TEMPERATURES_C


def test_rejected_measurements_are_never_served():
    """5,078 rows carry an explicit invalid_reason. None may reach a caller."""
    rows = thermo.connection().execute(
        "select polymer, solvent, temperature_c from solubility_grid "
        "where is_valid = false limit 25"
    ).fetchall()
    assert rows, "positive control: the asset must contain invalid rows to test"
    for polymer, solvent, temperature in rows:
        result = thermo.lookup(polymer, solvent, int(temperature))
        assert result["available"] is False
        assert result["refusal"] == "measurement_rejected"
        assert result["invalid_reason"]
