#!/usr/bin/env python3
"""Write polymer oligomer xyz + ORCA inputs. Never launches ORCA.

    PYTHONPATH=src python3 scripts/cosmo/prepare_polymer_job.py --polymers pvc pvdf

PE is skipped: that job is already running under ~/cosmo-artifacts/polymers/.
Default %maxcore 2500, serial (no %pal). The orchestrator runs DFT, one job
at a time, cheapest first: pe 38, pvc 44, pvdf 44, evoh 48, pp 62, nylon6 84,
pet 91, ps 104.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_sys_path_src = str(_ROOT / "src")
if _sys_path_src not in sys.path:
    sys.path.insert(0, _sys_path_src)

from dissolve.polymer_cosmo import (
    DFT_JOB_ORDER,
    HANDOFF_DIR,
    first_source_file,
    write_dft_handoff,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Write polymer DFT inputs. Does not run ORCA.")
    ap.add_argument(
        "--polymers",
        nargs="+",
        default=["pvc", "pvdf"],
        help="keys to write (default: pvc pvdf). 'pe' is refused.",
    )
    ap.add_argument("--outdir", default=str(HANDOFF_DIR))
    ap.add_argument("--maxcore", type=int, default=2500)
    args = ap.parse_args()

    known = {key for key, _ in DFT_JOB_ORDER}
    outdir = Path(args.outdir)
    rows = []
    for key in args.polymers:
        if key == "pe":
            print("skip pe: live DFT job; will not overwrite ~/cosmo-artifacts/polymers/",
                  file=sys.stderr)
            rows.append({"tag": "pe", "skipped": True, "reason": "pe_dft_already_running",
                         "dft_ran": False})
            continue
        if key not in known:
            print(f"unknown polymer {key}; known: {sorted(known)}", file=sys.stderr)
            return 2
        payload = write_dft_handoff(
            first_source_file(key), outdir / key, tag=key, maxcore_mb=args.maxcore,
        )
        print(f"{key}: {payload['n_atoms']} atoms -> {payload['opt_input']} (dft_ran=false)")
        rows.append(payload)
    (outdir / "handoff.json").write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
