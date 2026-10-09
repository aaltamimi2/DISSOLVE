"""A-13 item 7g: mirror the campaign into DISSOLVE's campaigns/plastchem-hpc, as 03d54178 did: the campaign's committed
scripts, inputs and charter (git HEAD of ~/plastchem-euler, never its working tree), the coverage tier's small state files
(policies, chunk manifests, gate G1, CPU checks, summaries; no per-structure records, no surfaces) and the thermodynamics
batch receipts. Copies only what differs and prints what it changed; it never deletes.

    python3 scripts/mirror_campaign_to_dissolve.py [--dry-run]"""
import hashlib
import subprocess
import sys
from pathlib import Path

LANE = Path(__file__).resolve().parents[1]
DISSOLVE = Path.home() / "dissolve-main-cleanup/campaigns/plastchem-hpc"
R = Path("/mnt/r/plastchem-euler")
DRY = "--dry-run" in sys.argv
#: the archive folders keep their own copies of the export scripts
EXPORT_COPIES = {"build_export_v2.py": "opencosmo-outputs", "classify_contaminants.py": "opencosmo-outputs",
                 "make_contaminants_tsv.py": "opencosmo-outputs", "write_readmes.py": "opencosmo-outputs",
                 "make_release_set.py": "opencosmo-outputs", "build_release_set.py": "opencosmo-outputs",
                 "pack_orca_files.py": "orca-calculation-files", "run_export_v2.sh": "orca-calculation-files",
                 "run_pack.sh": "orca-calculation-files", "verify_orca_archive.py": "orca-calculation-files"}


def committed(path):
    return subprocess.run(["git", "-C", str(LANE), "show", f"HEAD:{path}"], capture_output=True, check=True).stdout


def tracked(prefix):
    out = subprocess.run(["git", "-C", str(LANE), "ls-files", prefix], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


changed = []


def put(data, target):
    target = Path(target)
    if target.exists() and target.read_bytes() == data:
        return
    changed.append(("updated" if target.exists() else "added", str(target.relative_to(DISSOLVE))))
    if not DRY:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


# committed scripts: scripts/*.py and *.sbatch flat in scripts/, export-v2 into the archive folders
for path in tracked("scripts"):
    name = Path(path).name
    if path.startswith("scripts/export-v2/"):
        if name in EXPORT_COPIES:
            put(committed(path), DISSOLVE / EXPORT_COPIES[name] / name)
        continue
    if "/" in path[len("scripts/"):]:
        continue
    put(committed(path), DISSOLVE / "scripts" / name)
for path in ("CHARTER.txt", "inputs/coverage_pubchem_names_a13.json", "inputs/plastchem_coverage_a13_worklist.csv",
             "inputs/plastchem_coverage_a13_parent_aliases.csv"):
    put(committed(path), DISSOLVE / path)
for path in tracked("reports/coverage-v1"):
    put(committed(path), DISSOLVE / path)
# the coverage tier's small state files (state/ is not in git: copied as they stand)
S = LANE / "state/coverage-v1"
C = DISSOLVE / "calculation-files/coverage-v1"
for name in ("policy.json", "input-verification.json", "harvest.json", "summary.json", "gate-g1.json", "throttle-log.jsonl",
             "walltime-log.jsonl", "routing-log.jsonl"):
    if (S / name).exists():
        put((S / name).read_bytes(), C / name)
for folder in sorted(S.glob("[cr][0-9][0-9]")):
    for name in ("manifest.json", "staging-summary.json", "submission-indices.json"):
        if (folder / name).exists():
            put((folder / name).read_bytes(), C / folder.name / name)
checks = S / "cpu-checks"
for path in sorted(checks.rglob("*.json")):
    if "records" not in path.parts:
        put(path.read_bytes(), C / "cpu-checks" / path.relative_to(checks))
# thermodynamics receipts: every coverage batch, and h04 now that it is collected in full
for root, label in ((R / "coverage-thermo-v1", "coverage-thermo-v1"), (R / "newcontam-thermo-v1", "newcontam-thermo-v1")):
    for meta in sorted(root.glob("*/batch.json")):
        if label == "newcontam-thermo-v1" and meta.parent.name != "h04":
            continue
        for name in ("batch.json", "submission.json", "collection.json", "collection-partial.json"):
            if (meta.parent / name).exists():
                put((meta.parent / name).read_bytes(), DISSOLVE / "calculation-files" / label / meta.parent.name / name)
for kind, path in changed:
    print(f"{kind:8} {path}")
print(f"{len(changed)} files {'would change' if DRY else 'changed'}; digest of the list "
      f"{hashlib.sha256(repr(changed).encode()).hexdigest()[:12]}")
