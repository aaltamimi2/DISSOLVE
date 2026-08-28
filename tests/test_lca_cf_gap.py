"""C-1: describe the two CF gaps; do not admit rows or move the C1/C2/C3 refuse."""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea

_OLD_LCA_FACTORS_SHA256 = (
    "adba150af8192c57ccbd1eb3fe550206598ad7eda1f1e4abb49d07d636ea2adf"
)
_LCA_FACTORS_SHA256 = (
    "825e9e02a9366250ef0357407cc26bea1ef4ee04aa12720c3892e9eb44d8a083"
)
_ASSET_RELATIVE = "src/dissolve/data/tea_lca_characterization_factors.json"
_UNMAPPED_SOLVENTS = ("Acetaldehyde", "Triethylamine")
_ENERGY_CASES = ("C1", "C2", "C3")
_ADMITTED_SOLVENT_KEYS = frozenset({
    "1,3-Benzenediol",
    "1,4-Dioxane",
    "1-Butanol",
    "1-Propanol",
    "2,3-Dihydropyran",
    "2,4-Pentanedione",
    "2-Propanol",
    "Acetic Acid",
    "Acetic Anhydride",
    "Acetone",
    "Acetonitrile",
    "Benzaldehyde",
    "Benzene",
    "Benzonitrile",
    "Camphene",
    "Carbon Disulfide (0 Dipole Moment)",
    "Carbon Tetrachloride (0 Dipole Moment)",
    "Chlorobenzene",
    "Chloroform",
    "Cyclohexane",
    "Cyclohexanol",
    "Di-(2-Methoxyethyl) Ether",
    "Dichloromethane",
    "Diethyl Ether",
    "Diethylene Glycol",
    "Dimethyl Cellosolve",
    "Dimethyl sulfoxide",
    "Dipentene (Dl-Limonene)",
    "Diphenyl ether",
    "Dodecane",
    "Ethanol",
    "Ethyl Aceto Acetate (Keto)",
    "Ethyl acetate",
    "Ethylene Dichloride",
    "Ethylene glycol",
    "Heptane",
    "Hexamethylphosphoramide",
    "Hexane",
    "Isophorone",
    "Isopropylamine",
    "Methanol",
    "Methyl acetate",
    "Methyl ethyl ketone",
    "Methyl-t-Butyl Ether",
    "N,N-Dimethylformamide",
    "N-Methyl-2-Pyrrolidone (NMP)",
    "Naphthalene",
    "Nitromethane",
    "Propylene Carbonate",
    "Propylene glycol",
    "Pyridine",
    "Pyrrole",
    "Styrene",
    "Tetrahydrofuran (THF)",
    "Tetrahydropyran",
    "Toluene",
    "Water",
    "p-Xylene",
    "tert-Butanol",
})
_MEASURED_UNMAPPED = [
    [
        "triethylamine",
        "Triethylamine",
        "source row carries GWP only, no HTC/HTNC/ETOX, lca_confidence low",
    ],
    [
        "acetaldehyde",
        "acetaldehyde",
        "no row in the pinned CF source solvent-econ-lca-summary.csv "
        "under name, synonym ethanal, or CAS 75-07-0",
    ],
]


def _committed_factors() -> dict:
    raw = subprocess.check_output(
        ["git", "show", f"HEAD:{_ASSET_RELATIVE}"],
        cwd=_ROOT,
    )
    return json.loads(raw)


def test_lca_factor_asset_digest_matches_the_c1_pin():
    digest = hashlib.sha256(tea._LCA_FACTORS_ASSET.read_bytes()).hexdigest()
    assert digest == _LCA_FACTORS_SHA256
    assert tea._LCA_FACTORS_ASSET_SHA256 == _LCA_FACTORS_SHA256
    assert digest != _OLD_LCA_FACTORS_SHA256
    tea.lca_factor_payload.cache_clear()
    payload = tea.lca_factor_payload()
    assert payload["unmapped_source_rows"] == _MEASURED_UNMAPPED


@pytest.mark.parametrize("solvent", _UNMAPPED_SOLVENTS)
@pytest.mark.parametrize("energy_case", _ENERGY_CASES)
def test_a3_unmapped_solvents_still_refuse_at_c1_c2_c3(
    monkeypatch, solvent, energy_case,
):
    config = {"solvent": solvent, "energy_case": energy_case}
    with pytest.raises(ValueError, match="No governed cache-generator LCA factors"):
        tea._default_live_lca_cfs(config)
    with pytest.raises(ValueError, match="No governed cache-generator LCA factors"):
        tea._governed_live_lca_application(config)

    monkeypatch.setattr(tea, "live_engine_status", lambda: {
        "available": True,
        "live_provenance": {"status": "test-stub"},
    })
    result = tea._live(config, timeout_seconds=1)
    assert result["success"] is False
    assert result["error_type"] == "lca_factor_basis_unavailable"


def test_a3_cf_lookup_stays_outside_the_c1_branch():
    source = inspect.getsource(tea._governed_live_lca_application)
    lookup_at = source.find("recorded = _default_live_lca_cfs(config)")
    c1_at = source.find('if energy_case == "C1":')
    assert lookup_at != -1
    assert c1_at != -1
    assert lookup_at < c1_at


def test_a4_cf_solvent_key_set_is_identity_unchanged():
    tea.lca_factor_payload.cache_clear()
    payload = tea.lca_factor_payload()
    served = set(payload["solvents"])
    committed = set(_committed_factors()["solvents"])
    assert served - committed == set()
    assert committed - served == set()
    assert served == committed == _ADMITTED_SOLVENT_KEYS
    assert "Acetaldehyde" not in served
    assert "Triethylamine" not in served
    assert "acetaldehyde" not in served
    assert "triethylamine" not in served
    assert len(served) == 59
    tiers = Counter(row[1] for row in payload["solvents"].values())
    assert tiers == {
        "generator_validated_table": 27,
        "generator_class_average": 23,
        "generator_curated_with_class_fallbacks": 9,
    }
    assert sum(tiers.values()) == 59
