import sys, glob, collections, statistics
sys.path.insert(0, "bin"); import campaign_common as c
for block in ("surface", "oat", "sobol"):
    n = ok = 0; walls = []; errs = collections.Counter()
    for f in sorted(glob.glob(f"results/{block}/*.gz"))[:10]:
        for r in c.read_gz(f):
            n += 1; ok += r["ok"]; walls.append(r["wall_s"])
            if not r["ok"]: errs[str(r["error"])[:130]] += 1
    print(block, "sampled", n, "ok", ok, "median s", statistics.median(walls) if walls else None, errs.most_common(4))
for block in ("feeds", "mc"):
    files = sorted(glob.glob(f"results/{block}/*.scenarios.jsonl.gz")); n = 0; walls = []; bad = collections.Counter(); nw = []; nc = []
    for f in files[:30]:
        for r in c.read_gz(f):
            n += 1; walls.append(r["wall_s"])
            for loc, v in r["locations"].items():
                a = v.get("answer") or {}
                if not v["ok"] or "selected" not in a: bad[str(v.get("error") or a.get("error_type") or a.get("message") or list(a)[:5])[:160]] += 1
                else: nw.append(len(a.get("wash_comparison") or [])); nc.append(a.get("candidates_considered"))
    print(block, "files", len(files), "sampled", n, "median s", statistics.median(walls) if walls else None, "washes median/max", (statistics.median(nw), max(nw)) if nw else None, "candidates median/max", (statistics.median(nc), max(nc)) if nc else None, bad.most_common(4))
