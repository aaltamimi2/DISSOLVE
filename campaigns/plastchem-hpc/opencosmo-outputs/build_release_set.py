"""A-13 item 7d, on Euler: build one release's archive set from set.tsv (make_release_set.py), in ~/opencosmo-export-<release>:

  opencosmo-outputs-<release>.tar.xz.partNN       opencosmo-outputs/contaminants/<category>/<name>.orcacosmo, one per
                                                  new contaminant, each the surface whose SHA-256 the release recorded
  orca-calculation-files-<release>.tar.xz.partNN  orca-calculation-files/contaminants/<category>/<name>/ with the run's
                                                  ORCA inputs, optimised geometry, run record and COSMO-step log, as
                                                  pack_orca_files.py keeps them (no binaries, no trajectory)
  SHA256SUMS.surfaces, SHA256SUMS.orca             each part and each reassembled archive
  MANIFEST.rows.tsv, RUNS.rows.tsv                 rows to append to the archives' MANIFEST.tsv and RUNS.tsv

95 MB parts (split -b 95000000), xz -6, like the v2 archives. A run seeded from a licensed COSMObase/COSMOtherm geometry
is refused (none of the campaign's contaminant tiers uses one), and every packed file is scanned for credential
markers. The parts are read back at the end: every row has its surface and its run folder.

    python3 build_release_set.py RELEASE        (run in ~/opencosmo-export-<release>, with set.tsv beside it)"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

RELEASE = sys.argv[1]
HOME = Path.home()
LANE = HOME / "plastchem-euler"
EXP = HOME / f"opencosmo-export-{RELEASE}"
NAMES = {"optimized.xyz", "result.json", "cosmo.json", "cosmo_out.json", "cosmo.out", "verified-result.json"}
MARKERS = (b"password", b"passwd", b"secret", b"api_key", b"apikey", b"token", b"begin rsa", b"begin openssh",
           b"@wisc.edu", b"@gmail")
PART = 95000000


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def split_xz(tar_bytes_source, prefix):
    """Stream a tar through xz -6 into 95 MB parts; return the part paths."""
    xz = subprocess.Popen(["xz", "-T2", "-6", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    splitter = subprocess.Popen(["split", "-b", str(PART), "-d", "-a", "2", "-", prefix], stdin=xz.stdout)
    xz.stdout.close()
    tar_bytes_source(xz.stdin)
    xz.stdin.close()
    assert xz.wait() == 0 and splitter.wait() == 0
    return sorted(Path(prefix).parent.glob(Path(prefix).name + "*"))


def sums(parts, whole_name, out):
    h = hashlib.sha256()
    lines = []
    for p in parts:
        lines.append(f"{sha(p)}  {p.name}")
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    lines.append(f"{h.hexdigest()}  {whole_name}")
    out.write_text("\n".join(lines) + "\n")
    return lines


def main():
    rows = [line.rstrip("\n").split("\t") for line in open(EXP / "set.tsv") if line.strip()]
    entries, missing = [], []
    for key, digest, name, folder, category, candidates in rows:
        src = next((rel for rel in candidates.split(",") if (LANE / rel).is_file() and sha(LANE / rel) == digest), None)
        if src is None:
            missing.append(key)
            continue
        parts = src.split("/")
        run = "/".join(parts[:-3] + ["runs", parts[-2]]) if parts[-3] == "returns" else "/".join(parts[:-1])
        if not (LANE / run).is_dir():
            missing.append(key + " (run folder)")
            continue
        entries.append(dict(key=key, digest=digest, name=name, folder=folder, category=category, src=src, run=run,
                            path=f"contaminants/{category}/{folder}.orcacosmo"))
    if missing:
        sys.exit(f"surfaces or run folders not found: {len(missing)}: {missing[:5]}")
    # surfaces
    manifest = []
    def surfaces_tar(stream):
        with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for e in entries:
                data = (LANE / e["src"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == e["digest"], e["key"]
                info = tarfile.TarInfo(f"opencosmo-outputs/{e['path']}")
                info.size, info.mtime, info.mode = len(data), int((LANE / e["src"]).stat().st_mtime), 0o644
                tar.addfile(info, io.BytesIO(data))
                manifest.append("\t".join(["contaminant", e["path"], e["key"], e["name"], e["digest"], e["src"]]))
    surface_parts = split_xz(surfaces_tar, str(EXP / f"opencosmo-outputs-{RELEASE}.tar.xz.part"))
    (EXP / "MANIFEST.rows.tsv").write_text("\n".join(manifest) + "\n")
    sums(surface_parts, f"opencosmo-outputs-{RELEASE}.tar.xz", EXP / "SHA256SUMS.surfaces")
    # ORCA files of each run folder
    runs, files_manifest, hits = [], [], {}
    def orca_tar(stream):
        with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for e in entries:
                path = LANE / e["run"]
                names = sorted(n for n in os.listdir(path) if not n.startswith(".") and (path / n).is_file()
                               and (n.endswith(".inp") or n in NAMES))
                record = json.loads((path / "result.json").read_text()) if "result.json" in names else {}
                status = str(record.get("status", ""))
                assert status not in ("starting",) and not status.startswith("running"), f"live run {e['run']}"
                inp = record.get("input") if isinstance(record.get("input"), dict) else {}
                assert not any(isinstance(v, str) and v.lower().endswith((".cosmo", ".mcos")) for v in inp.values()), \
                    f"{e['run']} was seeded from a licensed geometry"
                arcdir = f"orca-calculation-files/{e['path'][:-len('.orcacosmo')]}"
                for n in names:
                    data = (path / n).read_bytes()
                    low = data.lower()
                    for m in MARKERS:
                        if m in low:
                            hits.setdefault(m.decode(), []).append(f"{arcdir}/{n}")
                    info = tarfile.TarInfo(f"{arcdir}/{n}")
                    info.size, info.mtime, info.mode = len(data), int((path / n).stat().st_mtime), 0o644
                    tar.addfile(info, io.BytesIO(data))
                    files_manifest.append("\t".join(["contaminants", f"{arcdir}/{n}", e["key"], e["name"], e["run"],
                                                     e["path"], status, str(len(data)), hashlib.sha256(data).hexdigest()]))
                runs.append("\t".join([arcdir, e["key"], e["name"], e["run"], e["path"], status, str(len(names)), "-", "-"]))
            for name, lines in ((f"MANIFEST-{RELEASE}.tsv", ["category\tpath\tidentity\tname\trun_folder\treleased_surface\tstatus\tbytes\tsha256", *files_manifest]),
                                (f"RUNS-{RELEASE}.tsv", ["path\tidentity\tname\trun_folder\treleased_surface\tstatus\tfiles\twithheld\tgenerated", *runs])):
                blob = ("\n".join(lines) + "\n").encode()
                info = tarfile.TarInfo(f"orca-calculation-files/{name}")
                info.size, info.mtime, info.mode = len(blob), int(time.time()), 0o644
                tar.addfile(info, io.BytesIO(blob))
    orca_parts = split_xz(orca_tar, str(EXP / f"orca-calculation-files-{RELEASE}.tar.xz.part"))
    (EXP / "RUNS.rows.tsv").write_text("\n".join(runs) + "\n")
    sums(orca_parts, f"orca-calculation-files-{RELEASE}.tar.xz", EXP / "SHA256SUMS.orca")
    # read both back from their parts
    found = {}
    for label, parts in (("surfaces", surface_parts), ("orca", orca_parts)):
        cat = subprocess.Popen(["cat", *map(str, parts)], stdout=subprocess.PIPE)
        unxz = subprocess.Popen(["xz", "-dc"], stdin=cat.stdout, stdout=subprocess.PIPE)
        with tarfile.open(fileobj=unxz.stdout, mode="r|") as tar:
            found[label] = [m.name for m in tar]
    have_surface = {name.split("/", 1)[1] for name in found["surfaces"]}
    have_run = {name.rsplit("/", 1)[0].split("/", 1)[1] for name in found["orca"] if name.count("/") > 1}
    lost = [e["key"] for e in entries if e["path"] not in have_surface or e["path"][:-len(".orcacosmo")] not in have_run]
    summary = dict(release=RELEASE, contaminants=len(entries), surface_parts=[p.name for p in surface_parts],
                   orca_parts=[p.name for p in orca_parts], surface_bytes=sum(p.stat().st_size for p in surface_parts),
                   orca_bytes=sum(p.stat().st_size for p in orca_parts), orca_files=len(files_manifest),
                   readback_missing=lost, credential_marker_hits={k: len(v) for k, v in hits.items()})
    (EXP / "build-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary))
    (EXP / "marker-hits.json").write_text(json.dumps(hits, indent=1) + "\n")  # reviewed by hand, as pack_orca_files.py reports them
    sys.exit(1 if lost else 0)


if __name__ == "__main__":
    main()
