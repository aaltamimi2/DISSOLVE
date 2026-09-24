"""Pack each molecule's ORCA/openCOSMO calculation files, read straight from the campaign run folders, into one tar
stream: orca-calculation-files/{contaminants,polymers,solvents}/..., with MANIFEST.tsv (every file) and RUNS.tsv (every
run folder) inside. Runs on Euler from ~/opencosmo-export:

    python3 pack_orca_files.py plan          # find every run folder, tie it to its released surface
    python3 pack_orca_files.py pack | xz ... # stream the tar

Kept per run: every ORCA input (*.inp), the optimised geometry, the result and COSMO records and the COSMO-step log.
Left out: binary intermediates (.gbw, densities, .cpcm), the optimisation log and trajectory, and every starting
geometry read from a licensed COSMObase/COSMOtherm file (.cosmo, .mcos; the run's result.json names it). For those runs
opt.inp stays out and result.json goes in as result.redacted.json, its copies of the starting geometry replaced by a note.

A run is tied to the surfaces archive (opencosmo-outputs) when the SHA-256 of its cosmo.solute.orcacosmo equals a
released surface; runs whose surface was not released are kept under <category>/not-in-release/<stage>/."""
import collections, concurrent.futures as cf, hashlib, io, json, os, pathlib, re, sys, tarfile, time

HOME = pathlib.Path.home()
LANE = HOME / "plastchem-euler"
EXP = HOME / "opencosmo-export"
PLAN = EXP / "orca-plan.json"
OUTDIR = EXP / "orca-archive"
ROOT = "orca-calculation-files"
NAMES = {"optimized.xyz", "result.json", "cosmo.json", "cosmo_out.json", "cosmo.out", "verified-result.json"}
STAGES = {  # stage -> category of its runs, in priority order when two runs gave the same released surface
    "campaign-v1": "contaminants", "tier2-v1": "contaminants", "pilot-v1": "contaminants",
    "polymer-v1": "polymers", "solvent-library-v1": "solvents", "phase9-solvent-library-v1": "solvents",
    "diagnostics-a1": "contaminants",
}
MARKERS = (b"password", b"passwd", b"secret", b"api_key", b"apikey", b"token", b"begin rsa", b"begin openssh",
           b"@wisc.edu", b"@gmail")
COORD = re.compile(rb"-?\d+\.\d{5,}")
POOL = cf.ThreadPoolExecutor(16)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


NOTE = "withheld: starting geometry read from a licensed COSMObase/COSMOtherm file"


def leaks(blob, start, share=0.5):
    return bool(start) and len(start & set(COORD.findall(blob))) > max(3, len(start) * share)


def redact(obj, start):
    if isinstance(obj, dict):
        return {k: redact(v, start) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, start) for v in obj]
    if isinstance(obj, str) and COORD.search(obj.encode()) and leaks(obj.encode(), start):
        return NOTE
    return obj


def describe(run):
    path = LANE / run
    try:
        with os.scandir(path) as it:
            names = sorted(e.name for e in it if not e.name.startswith(".") and e.is_file(follow_symlinks=False)
                           and (e.name.endswith(".inp") or e.name in NAMES))
    except (FileNotFoundError, NotADirectoryError):
        names = []
    result = load(path / "result.json") if "result.json" in names else {}
    inp = result.get("input") if isinstance(result.get("input"), dict) else {}
    seeded = any(isinstance(v, str) and v.lower().endswith((".cosmo", ".mcos")) for v in inp.values())
    seeded = seeded or (not result and run.split("/")[0] in ("polymer-v1", "phase9-solvent-library-v1"))  # no record
    withheld, generated = [], {}
    if seeded:  # nothing kept may carry the starting geometry
        start = set(COORD.findall((path / "opt.inp").read_bytes())) if "opt.inp" in names else set()
        if not start:
            withheld = ["opt.inp", "result.json"]  # cannot check without the starting geometry: keep neither
        for n in names:
            blob = (path / n).read_bytes()
            if n == "opt.inp" or (not start and n == "result.json"):
                if n not in withheld:
                    withheld.append(n)
            elif leaks(blob, start):
                withheld.append(n)
                if n == "result.json":
                    text = json.dumps(redact(result, start), indent=2) + "\n"
                    if not leaks(text.encode(), start, share=0.1):  # strict on what is published
                        generated["result.redacted.json"] = text
    try:
        with open(path / "cosmo.solute.orcacosmo", "rb") as f:
            surface = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        surface = ""
    return {"run": run, "stage": run.split("/")[0], "folder": run.split("/")[-1],
            "files": [n for n in names if n not in withheld], "withheld": sorted(set(withheld) & set(names)),
            "generated": generated, "seeded": seeded,
            "status": str(result.get("status", "")), "name": str(inp.get("name", "")), "surface_sha256": surface}


def plan():
    released = []
    for line in open(EXP / "opencosmo-outputs" / "MANIFEST.tsv"):
        category, path, identity, name, sha, _ = line.rstrip("\n").split("\t")
        if category != "category":
            released.append({"sha": sha, "path": path, "identity": identity, "name": name})
    by_sha = {}
    for hit in released:
        by_sha.setdefault(hit["sha"], []).append(hit)  # 26 contaminants reuse a solvent's surface
    runs = [f"{stage}/runs/{d.name}" for stage in STAGES for d in os.scandir(LANE / stage / "runs") if d.is_dir()]
    log("run folders:", len(runs))
    described = list(POOL.map(describe, runs))
    order = list(STAGES)
    described.sort(key=lambda d: (order.index(d["stage"]), d["folder"]))
    used, entries = set(), []
    for d in described:
        hits = by_sha.get(d["surface_sha256"], [])
        if hits and d["surface_sha256"] not in used:
            used.add(d["surface_sha256"])
            for hit in hits:  # the same run folder under every released path that uses its surface
                arcdir = str(pathlib.PurePosixPath(hit["path"]).with_suffix(""))
                entries.append(dict(d, arcdir=arcdir, identity=hit["identity"], name=hit["name"],
                                    release_surface=hit["path"]))
        else:
            entries.append(dict(d, arcdir=f"{STAGES[d['stage']]}/not-in-release/{d['stage']}/{d['folder']}",
                                identity=d["folder"], release_surface=""))
    entries = [d for d in entries if d["files"] or d["generated"]]
    entries.sort(key=lambda d: d["arcdir"])
    with open(PLAN, "w") as f:
        json.dump(entries, f)
    cover = {}
    for hit in released:
        parts = hit["path"].split("/")
        cat = "/".join(parts[:2]) if parts[0] == "solvents" else parts[0]
        cover.setdefault(cat, [0, 0])[0] += 1
        cover[cat][1] += hit["sha"] in used
    for cat, (n, covered) in sorted(cover.items()):
        log(f"released surfaces {cat}: {n}, with their run folder: {covered}")
    arcdirs = [d["arcdir"] for d in entries]
    log("archive folders:", len(arcdirs), "distinct:", len(set(arcdirs)))
    extra = [d for d in entries if not d["release_surface"]]
    log("run folders not behind a released surface:", len(extra), dict(collections.Counter(d["stage"] for d in extra)))
    seeded = [d for d in entries if d["seeded"]]
    log("COSMObase/COSMOtherm-seeded runs:", len(seeded), dict(collections.Counter(d["stage"] for d in seeded)),
        "| withheld:", dict(collections.Counter(tuple(d["withheld"]) for d in seeded)),
        "| redacted result.json:", sum("result.redacted.json" in d["generated"] for d in seeded))
    log("files planned:", sum(len(d["files"]) + len(d["generated"]) for d in entries), "in", len(entries), "folders")


def read(item):
    arc, src = item
    if not src.startswith("/"):  # generated content (a redacted record), not a path
        return arc, src.encode(), int(time.time())
    with open(src, "rb") as f:
        st = os.fstat(f.fileno())
        return arc, f.read(), int(st.st_mtime)


def add(tar, arc, data, mtime):
    info = tarfile.TarInfo(f"{ROOT}/{arc}")
    info.size, info.mtime, info.mode = len(data), mtime, 0o644
    tar.addfile(info, io.BytesIO(data))


def pack():
    entries = json.load(open(PLAN))
    items = [(f"{d['arcdir']}/{n}", str(LANE / d["run"] / n), d) for d in entries for n in d["files"]]
    items += [(f"{d['arcdir']}/{n}", text, d) for d in entries for n, text in d["generated"].items()]
    items.sort(key=lambda item: item[0])
    manifest = ["category\tpath\tidentity\tname\trun_folder\treleased_surface\tstatus\tbytes\tsha256"]
    hits, total, t0 = {}, 0, time.time()
    tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT)
    for i in range(0, len(items), 2000):
        chunk = items[i:i + 2000]
        for (arc, data, mtime), (_, _, d) in zip(POOL.map(read, [(a, s) for a, s, _ in chunk]), chunk):
            add(tar, arc, data, mtime)
            total += len(data)
            low = data.lower()
            for m in MARKERS:
                if m in low:
                    hits.setdefault(m.decode(), []).append(arc)
            manifest.append("\t".join([d["arcdir"].split("/")[0], f"{ROOT}/{arc}", d["identity"], d["name"],
                                       d["run"], d["release_surface"] or "not in release", d["status"], str(len(data)),
                                       hashlib.sha256(data).hexdigest()]))
        log(f"packed {min(i + 2000, len(items))}/{len(items)} files, {total / 1e6:.0f} MB, {time.time() - t0:.0f} s")
    runs = ["path\tidentity\tname\trun_folder\treleased_surface\tstatus\tfiles\twithheld\tgenerated"]
    runs += ["\t".join([f"{ROOT}/{d['arcdir']}", d["identity"], d["name"], d["run"],
                        d["release_surface"] or "not in release", d["status"], str(len(d["files"]) + len(d["generated"])),
                        ",".join(d["withheld"]) or "-", ",".join(d["generated"]) or "-"]) for d in entries]
    OUTDIR.mkdir(exist_ok=True)
    for name, lines in (("MANIFEST.tsv", manifest), ("RUNS.tsv", runs)):
        blob = ("\n".join(lines) + "\n").encode()
        add(tar, name, blob, int(time.time()))
        (OUTDIR / name).write_bytes(blob)
    tar.close()
    sys.stdout.buffer.flush()
    log(f"packed {len(items)} files, {total / 1e6:.0f} MB uncompressed; marker hits:",
        {k: (len(v), v[:3]) for k, v in hits.items()} or "none")


{"plan": plan, "pack": pack}[sys.argv[1]]()
