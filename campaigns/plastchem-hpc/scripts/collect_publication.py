"""A-12: collect the publication tier's returns from Euler and verify identity (D-IDENT: the optimised geometry's
connectivity first block must equal the input's). Bond perception is given the molecule's net charge; Open Babel
reads the XYZ as it is. Writes state/publication-v1/records/<InChIKey>.json and prints a summary.

    python3 scripts/collect_publication.py          (repeat until every task is final)"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdDetermineBonds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from euler_transport import run  # noqa: E402
from identity_campaign import decide  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "state/publication-v1"
OBABEL = "/home/aaltamimi2/anaconda3/bin/obabel"


def perceive(xyz, charge):
    observations = []
    try:
        mol = Chem.MolFromXYZFile(str(xyz))
        rdDetermineBonds.DetermineBonds(mol, charge=charge)
        observations.append({"method": "rdkit_determine_bonds", "charge": charge, "full_inchikey": Chem.MolToInchiKey(mol)})
    except Exception as exc:  # noqa: BLE001 - kept as an observation, as in the campaign's identity check
        observations.append({"method": "rdkit_determine_bonds", "charge": charge, "error": str(exc)})
    try:
        out = subprocess.run([OBABEL, str(xyz), "-oinchikey"], capture_output=True, text=True, check=True)
        keys = re.findall(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$", out.stdout, re.M)
        observations.append({"method": "openbabel_reference", "full_inchikey": keys[0] if keys else None,
                             "warnings": out.stderr.strip()})
    except Exception as exc:  # noqa: BLE001
        observations.append({"method": "openbabel_reference", "error": str(exc)})
    return observations


def main():
    manifest = {"molecules": [m for group in ("publication", "neutral") if (P / group / "manifest.json").exists()
                              for m in json.loads((P / group / "manifest.json").read_text())["molecules"]]}
    incoming = P / "incoming"
    incoming.mkdir(exist_ok=True)
    result = run("scp", ["-r", "-q", "euler:plastchem-euler/publication-v1/returns", str(incoming)])
    if result.returncode != 0:
        sys.exit(f"scp failed: {result.returncode}")
    (P / "records").mkdir(exist_ok=True)
    summary = {}
    for m in manifest["molecules"]:
        key, back = m["inchikey"], incoming / "returns" / m["inchikey"]
        status = json.loads((back / "result.json").read_text())["status"] if (back / "result.json").exists() else "not started"
        record = {"inchikey": key, "labels": m["labels"], "set": m["set"], "charge": m["charge"], "run_status": status}
        if status == "converged_identity_pending":
            remote = json.loads((back / "result.json").read_text())
            surface = (back / "surface.orcacosmo").read_bytes()
            digest = hashlib.sha256(surface).hexdigest()
            assert digest == remote["surface_sha256"], key
            identity = decide(key, perceive(back / "optimized.xyz", int(m["charge"])))
            record.update(surface_sha256=digest, surface_bytes=len(surface), identity=identity,
                          status="verified" if identity["identity_verified"] else "identity_rejected",
                          elapsed_hours=round(remote["elapsed_seconds"] / 3600, 2), node=remote["node"])
        else:
            record["status"] = status
        (P / "records" / f"{key}.json").write_text(json.dumps(record, indent=1) + "\n")
        summary[record["status"]] = summary.get(record["status"], 0) + 1
    print(json.dumps(summary))
    for path in sorted((P / "records").glob("*.json")):
        r = json.loads(path.read_text())
        if r["status"] not in ("verified",):
            print("  ", "/".join(r["labels"]), r["status"])


if __name__ == "__main__":
    main()
