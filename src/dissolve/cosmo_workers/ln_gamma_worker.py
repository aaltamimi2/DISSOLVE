#!/usr/bin/env python3
"""JSON stdin → ln(γ^∞) on stdout. File-path loads cosmo_logp (no dissolve import).

P-4b engine bridge. Request is JSON. No shell. User SMILES is not argv.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["DISSOLVE_COSMO_IN_WORKER"] = "1"

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "dissolve_cosmo_logp",
    str(Path(__file__).resolve().parents[1] / "cosmo_logp.py"),
)
cl = _ilu.module_from_spec(_spec)
sys.modules["dissolve_cosmo_logp"] = cl
_spec.loader.exec_module(cl)


def main() -> int:
    try:
        raw = sys.stdin.read()
        request = json.loads(raw)
    except Exception as exc:
        json.dump({"ok": False, "error": f"invalid JSON request: {exc}"}, sys.stdout)
        sys.stdout.write("\n")
        return 2
    if not isinstance(request, dict):
        json.dump({"ok": False, "error": "request must be a JSON object"}, sys.stdout)
        sys.stdout.write("\n")
        return 2
    try:
        value = cl.ln_gamma_infinite_dilution(
            request["solute"],
            request["solvent"],
            temperature=float(request.get("temperature") or cl.STANDARD_T),
            solute_fraction=float(request.get("solute_fraction") or 1e-5),
            parameterization=str(
                request.get("parameterization") or "openCOSMORS24a"
            ),
        )
    except Exception as exc:
        json.dump(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 1
    json.dump({"ok": True, "ln_gamma": float(value)}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
