"""Bind every overview cell to the completed numerical seal, without rerendering."""
from pathlib import Path
import csv, datetime, hashlib, json, math
R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/reports/final-panel-2026-09-21')
receipt_path=R/'state/final-resolved-panel-audit.json'
if not receipt_path.exists():
    raise SystemExit('Final numerical seal is not ready; verification not run.')
receipt=json.loads(receipt_path.read_text());seal=Path(receipt['root'])
assert receipt['passed']==5803 and receipt['failed']==0
assert hashlib.sha256((seal/'SHA256SUMS').read_bytes()).hexdigest()==receipt['manifest_sha256']
ledger=json.loads((seal/'sealed-thermodynamics/processing-ledger.json').read_text())
seen=set(); predicted=missing=0
with (D/'overview-matrix.csv').open(newline='') as f:
    reader=csv.DictReader(f);solvents=reader.fieldnames[1:]
    assert len(solvents)==len(set(solvents))==32
    for row in reader:
        key=row['input_inchikey'];assert key not in seen;seen.add(key)
        raw=Path(ledger[key]['result_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==ledger[key]['result_sha256']
        record=json.loads(raw)
        pairs={p['solvent']:p for p in record['partitions_against_water']}
        assert set(pairs)==set(solvents)
        for solvent in solvents:
            p=pairs[solvent];cell=row[solvent]
            if p['status']=='predicted':
                assert math.isfinite(float(cell)) and float(cell)==p['log10_K_mole_fraction']
                predicted+=1
            else:
                assert cell=='';missing+=1
assert seen==set(ledger) and len(seen)==5803
assert predicted==179752 and missing==5944
figure=json.loads((D/'overview-verification.json').read_text())
assert hashlib.sha256((D/'contaminant-panel-overview.png').read_bytes()).hexdigest()==figure['png_sha256']
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rows':len(seen),
     'predicted_cells':predicted,'missing_cells':missing,'seal_manifest_sha256':receipt['manifest_sha256'],
     'matrix_sha256':hashlib.sha256((D/'overview-matrix.csv').read_bytes()).hexdigest(),
     'png_sha256':figure['png_sha256'],'status':'Every figure matrix cell equals the sealed numerical record; no rerender needed'}
(D/'overview-seal-verification.json').write_text(json.dumps(out,indent=2)+'\n')
(R/'state/final-overview-seal-verification.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out),flush=True)
