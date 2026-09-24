"""Check the built orca-calculation-files archive from its parts: every released surface has its folder, no opt.inp
from a COSMObase/COSMOtherm-seeded run, and no packed file of such a run shares more than 10% of that run's starting
coordinates. Runs on Euler from ~/opencosmo-export."""
import collections, json, pathlib, re, subprocess, sys, tarfile

HOME = pathlib.Path.home()
LANE = HOME / "plastchem-euler"
EXP = HOME / "opencosmo-export"
COORD = re.compile(rb"-?\d+\.\d{5,}")
ROOT = "orca-calculation-files/"

plan = {d["arcdir"]: d for d in json.load(open(EXP / "orca-plan.json"))}
start = {}
for d in plan.values():
    if d["seeded"] and d["run"] not in start:
        opt = LANE / d["run"] / "opt.inp"
        start[d["run"]] = set(COORD.findall(opt.read_bytes())) if opt.exists() else set()
parts = sorted((EXP / "orca-archive").glob("orca-calculation-files.tar.xz.part*"))
cat = subprocess.Popen(["cat", *map(str, parts)], stdout=subprocess.PIPE)
xz = subprocess.Popen(["xz", "-dc"], stdin=cat.stdout, stdout=subprocess.PIPE)
folders, bad, count, seeded_files = collections.Counter(), [], 0, 0
with tarfile.open(fileobj=xz.stdout, mode="r|") as tar:
    for m in tar:
        count += 1
        name = m.name[len(ROOT):]
        folder, _, base = name.rpartition("/")
        folders[folder] += 1
        d = plan.get(folder)
        if d and d["seeded"]:
            seeded_files += 1
            data = tar.extractfile(m).read()
            s = start[d["run"]]
            shared = len(s & set(COORD.findall(data))) if s else 0
            if base == "opt.inp" or (s and shared > max(3, len(s) * 0.1)):
                bad.append((name, shared, len(s)))
released = [line.split("\t")[1] for line in open(EXP / "opencosmo-outputs" / "MANIFEST.tsv")][1:]
missing = [p for p in released if folders[p.rsplit(".", 1)[0]] == 0]
print("entries:", count, "| folders:", len(folders) - 1, "| released surfaces without their folder:", len(missing), missing[:3])
print("files checked in seeded runs:", seeded_files, "| starting-geometry leaks:", len(bad), bad[:5])
sys.exit(1 if missing or bad else 0)
