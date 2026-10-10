"""Exploratory conformer study (owner request 2026-10-10; not a release input): openCOSMO-RS 24a for every converged,
identity-verified conformer, with the campaign's own code and phases. The numerical package files are byte-identical
to the pins of the coverage thermodynamics batches (checked below), the profile helpers are the pinned
phase9_profiles.py and phase9_worker_cpu.py, and the 32 solvents and 10 polymer ensembles (236 repeat-unit entries) are
the surfaces of batch t12's inputs.json, copied from Euler and checked by SHA-256.

For each conformer c and each phase X (a solvent, or one polymer entry) at infinite dilution and 298.15 K:
  ln g_c^X(conductor) with refst='cosmo', i.e. the pseudo-chemical potential mu_c^X/RT against the ideal conductor;
  delta_c = ln g_c(conductor) - ln g_c(pure component), the same in every phase (checked on two phases), so that
  ln g_c^X(pure component), the quantity the release stores, is ln g_c^X(conductor) - delta_c.
The polymer value is the campaign's repeat-unit ensemble (both conventions); logP_x = (ln g_P - ln g_S)/ln 10 and
logP_concentration = logP_x + log10(V_P/V_S), as the release computes them.

Writes state/conformers-v1/thermo/<conformer>.json; existing ones are kept (a new conformer adds its own).

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_thermo.py"""
import os

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"
import hashlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
P = ROOT / "state/conformers-v1"
SPEC = Path("/mnt/r/plastchem-euler/coverage-thermo-v1/t12/inputs.json")
PINS = {"phase9_profiles.py": "0113145c2aa1fe9e2581678741734bcae6651a2303d53d78344798da4ac0bb77",
        "phase9_worker_cpu.py": "1c2e5910904db8d8068fd964278f92188e31c988cb3d5711426b1d0361bf3f16"}
T = 298.15


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_code(spec):
    import opencosmorspy
    root = Path(opencosmorspy.__file__).resolve().parent.parent
    for rel, digest in spec["package_pins"].items():
        assert sha(root / rel) == digest, rel
    for name, digest in PINS.items():
        assert sha(ROOT / "scripts" / name) == digest, name


def phase_surfaces(spec):
    """Local copies of the solvent and polymer-entry surfaces (from Euler, by SHA-256)."""
    from euler_transport import run
    local = P / "phases"
    local.mkdir(parents=True, exist_ok=True)
    phases = {f"solvent:{k}": v for k, v in spec["solvents"].items()}
    for polymer, entries in spec["polymers"].items():
        for e in entries:
            phases[f"polymer:{polymer}:{e['entry_id']}"] = e
    missing = sorted({v["surface"] for v in phases.values() if not (local / Path(v["surface"]).name).exists()})
    for start in range(0, len(missing), 60):
        result = run("scp", ["-q", *[f"euler:{m}" for m in missing[start:start + 60]], str(local)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-400:]
    for v in phases.values():
        assert sha(local / Path(v["surface"]).name) == v["surface_sha256"]
    return {k: dict(v, local=str(local / Path(v["surface"]).name)) for k, v in phases.items()}


def main():
    spec = json.loads(SPEC.read_text())
    assert spec["temperature_K"] == T and spec["reference_state"] == "pure_component"
    check_code(spec)
    from phase9_profiles import ProfileCache, engine_from_profiles, infinite_dilution
    from phase9_worker_cpu import ensemble

    phases = phase_surfaces(spec)
    out = P / "thermo"
    out.mkdir(parents=True, exist_ok=True)
    records = [json.loads(p.read_text()) for p in sorted((P / "records").glob("*.json"))]
    todo = [r for r in records if r.get("status") == "converged" and not (out / f"{r['inchikey']}.json").exists()]
    if not todo:
        print(json.dumps(dict(new=0)))
        return
    cache = ProfileCache()
    profiles = [cache.get(str(Path(r["archive_path"]) / "surface.orcacosmo"), r["surface_sha256"]) for r in todo]
    conductor = {}
    for name, ph in phases.items():
        p = cache.get(ph["local"], ph["surface_sha256"])
        engine = engine_from_profiles([*profiles, p])
        x = np.zeros(len(profiles) + 1)
        x[-1] = 1.0
        engine.add_job(x=x, T=T, refst="cosmo")
        values = engine.calculate()["tot"]["lng"][0, :-1].copy()
        assert np.isfinite(values).all(), name
        conductor[name] = values
    # the pure-component offset, once per conformer, and its phase independence on a second phase
    first, second = "solvent:" + next(iter(spec["solvents"])), next(k for k in phases if k.startswith("polymer:"))
    delta = np.empty(len(profiles))
    check = []
    for start in range(0, len(profiles), 16):
        chunk = profiles[start:start + 16]
        pure_first = infinite_dilution(chunk, cache.get(phases[first]["local"]))
        delta[start:start + 16] = conductor[first][start:start + 16] - pure_first
        if start == 0:
            pure_second = infinite_dilution(chunk, cache.get(phases[second]["local"]))
            check = (conductor[second][:len(chunk)] - pure_second) - delta[:len(chunk)]
    assert np.max(np.abs(check)) < 1e-8, f"pure-component offset differs between phases: {np.max(np.abs(check))}"
    for i, r in enumerate(todo):
        solvents = {k.split(":", 1)[1]: float(conductor[k][i] - delta[i]) for k in phases if k.startswith("solvent:")}
        polymers = {}
        for polymer, entries in spec["polymers"].items():
            values = [float(conductor[f"polymer:{polymer}:{e['entry_id']}"][i] - delta[i]) for e in entries]
            polymers[polymer] = ensemble(entries, values)
        rows = []
        for solvent, s in spec["solvents"].items():
            for polymer, e in polymers.items():
                for convention in ("normalized", "existing"):
                    kx = (e[convention + "_gamma"] - solvents[solvent]) / math.log(10)
                    rows.append(dict(solvent=solvent, polymer=polymer, convention=convention, logP_x=kx,
                                     logP_concentration=kx + math.log10(e[convention + "_volume"] / s[convention + "_volume_cm3_mol"])))
        result = dict(conformer=r["inchikey"], parent_inchikey=r["input"]["parent_inchikey"], rank=r["input"]["conformer_rank"],
                      surface_sha256=r["surface_sha256"], cosmo_energy_hartree=r["cosmo_solute_energy_hartree"],
                      cosmo_area_A2=profiles[i]["area"], cosmo_volume_A3=profiles[i]["volume"],
                      pure_component_offset=float(delta[i]), offset_check_max_abs=float(np.max(np.abs(check))),
                      ln_gamma_conductor={k: float(v[i]) for k, v in conductor.items()},
                      ln_gamma_solvent=solvents, polymer_ensembles=polymers, partition=rows,
                      code=dict(package_pins=spec["package_pins"], helpers=PINS, inputs=str(SPEC), inputs_sha256=sha(SPEC)))
        (out / f"{r['inchikey']}.json").write_text(json.dumps(result) + "\n")
    print(json.dumps(dict(new=len(todo), phases=len(phases), offset_check_max_abs=float(np.max(np.abs(check))))))


if __name__ == "__main__":
    main()
