"""A-11 driver: every 10 minutes, keep the halogen tier and its thermodynamics moving inside the shared cap of 64, and
build promotion-v2 when everything is in. Logs one JSON line per cycle to logs/drive-a11.log; writes
state/halogen-v1/driver-done.json and exits when the release is built. Never deletes, cancels or resubmits work.

Cap: ORCA (array contam-halogen-v1) keeps at most 56 while it has queued tasks; the thermodynamics arrays share what
ORCA leaves, at least 8, and everything once ORCA has drained.
Batches: a halogen thermodynamics batch is staged once 250 accepted structures wait (or whatever waits once ORCA is
done); one batch at a time is in flight so slots go to the earliest batch first."""
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from euler_transport import run

R = Path(__file__).resolve().parents[1]
B = Path("/mnt/r/plastchem-euler")
OUT = B / "newcontam-thermo-v1"
P = R / "state/halogen-v1"
LOG = R / "logs/drive-a11.log"
PY = str(Path.home() / ".venvs/cosmo-logp/bin/python")
CAP, ORCA_MAX, THERMO_MIN, BATCH = 64, 56, 8, 250
ORCA_ARRAY = "75669"


def log(**entry):
    entry["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def remote(command):
    result = run("ssh", ["euler", command], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{command[:60]}: {result.stderr[:300]}")
    return result.stdout


def local(*args):
    result = subprocess.run([PY, *args], cwd=R, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip().splitlines()[-1:] or [""]


def queue():
    """(name, state, array id) for every task of the campaign's arrays."""
    rows = []
    for line in remote("squeue -u aaltamimi2 -h -r -o '%i|%j|%T'").splitlines():
        task, name, state = line.split("|")
        if name.startswith("contam-"):
            rows.append((name, state, task.split("_")[0]))
    return rows


def batches():
    return sorted(p.parent.name for p in OUT.glob("*/batch.json") if p.parent.name != "g00")


def halogen_summary():
    return json.loads((P / "summary.json").read_text())["counts"]


def waiting_halogen():
    sys.path.insert(0, str(R / "scripts"))
    from newcontam_thermo import candidates
    return len(candidates("halogen"))


def throttle(rows):
    """ORCA first while it has queued work (56, less any thermodynamics jobs above their 8 still draining); after
    that the thermodynamics arrays get every slot ORCA no longer uses. Partition arrays are short and get two slots
    each; LLE arrays share the rest, earliest batch first."""
    orca_pending = sum(1 for n, s, _ in rows if n == "contam-halogen-v1" and s == "PENDING")
    orca_running = sum(1 for n, s, _ in rows if n.startswith("contam-halogen") and s == "RUNNING")
    retry_running = sum(1 for n, s, _ in rows if n.startswith("contam-halogen-r") and s == "RUNNING")
    thermo_running = sum(1 for n, s, _ in rows if n.startswith("contam-a11-") and s == "RUNNING")
    updates = {}
    if orca_pending:
        updates[ORCA_ARRAY] = max(1, min(ORCA_MAX, CAP - max(THERMO_MIN, thermo_running)) - retry_running)
        budget = THERMO_MIN
    else:
        budget = max(THERMO_MIN, CAP - orca_running)
    arrays = sorted({(n, a) for n, s, a in rows if n.startswith("contam-a11-")}, key=lambda x: int(x[1]))
    partition = [a for n, a in arrays if "-partition-" in n]
    lle = [a for n, a in arrays if "-lle-" in n]
    for a in partition:
        updates[a] = 2
    left = max(len(lle), budget - 2 * len(partition))
    for i, a in enumerate(lle):
        updates[a] = left // len(lle) + (1 if i < left % len(lle) else 0)
    for array, limit in updates.items():
        remote(f"scontrol update JobId={array} ArrayTaskThrottle={limit} 2>/dev/null || true")
    return dict(orca_running=orca_running, orca_pending=orca_pending, thermo_running=thermo_running,
                throttles=updates)


def cycle():
    rows = queue()
    state = dict(throttle=throttle(rows))
    # collect finished thermodynamics batches
    for batch in batches():
        if (OUT / batch / "submission.json").exists() and not (OUT / batch / "collection.json").exists():
            code, tail = local("scripts/newcontam_thermo.py", "collect", batch)
            state.setdefault("collect", {})[batch] = (code, tail[0][:200])
    for action in ("collect", "launch"):  # preempted runs rerun in their own rounds
        code, tail = local("scripts/halogen_retry.py", action)
        state.setdefault("retry", {})[action] = (code, tail[0][:200])
    counts = halogen_summary()
    orca_done = (counts["running"] == 0 and counts["not_yet_run"] == 0 and counts["awaiting_verification"] == 0
                 and counts["retry_pending"] == 0)
    in_flight = [b for b in batches() if not (OUT / b / "collection.json").exists()]
    waiting = waiting_halogen()
    state.update(halogen=counts, waiting_for_thermo=waiting, in_flight=in_flight)
    # While ORCA runs, one batch of 250 at a time (thermodynamics has 8 slots anyway); once it has drained, everything
    # that waits goes out together so the tail can use every slot.
    # Once ORCA's queue has drained, the slots its tail frees go to thermodynamics: send everything waiting in one
    # batch (at least 50, so stragglers do not each cost a 268-surface staging), and the remainder when ORCA is done.
    drained = state["throttle"]["orca_pending"] == 0
    if waiting and (orca_done or (drained and waiting >= 50)
                    or (not [b for b in in_flight if b.startswith("h")] and waiting >= BATCH)):
        name = f"h{len([b for b in batches() if b.startswith('h')]) + 1:02d}"
        limit = waiting if (orca_done or drained) else BATCH
        code, tail = local("scripts/newcontam_thermo.py", "stage", name, "halogen", "--limit", str(limit))
        state["staged"] = (name, code, tail[0][:200])
        if code == 0:
            code, tail = local("scripts/newcontam_thermo.py", "submit", name)
            state["submitted"] = (name, code, tail[0][:200])
    done = orca_done and not waiting and not [b for b in batches() if not (OUT / b / "collection.json").exists()]
    if done and counts["retry_pending"] == 0:
        code, tail = local("scripts/build_promotion_v2.py", "build")
        state["release"] = (code, tail[0][:300])
        if code == 0:
            (P / "driver-done.json").write_text(json.dumps(dict(state, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), indent=2) + "\n")
            log(event="A11_RELEASE_BUILT", **state)
            return True
    log(event="cycle", **state)
    return False


if __name__ == "__main__":
    while True:
        try:
            if cycle():
                print("A11_RELEASE_BUILT", flush=True)
                break
        except Exception as exc:
            log(event="error", error=str(exc)[:500], trace=traceback.format_exc()[-800:])
        time.sleep(600)
