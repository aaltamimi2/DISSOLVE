"""Combine sealed one-molecule validation outputs without modifying prior releases."""
import argparse
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path

BULK = Path('/mnt/r/plastchem-euler/tier2-v1')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, action='append', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    assert out.is_relative_to(BULK) and not out.exists()
    rows, pins = [], {}
    for source in args.input:
        source = source.resolve()
        assert source.is_relative_to(BULK)
        manifest = json.loads((source / 'manifest.json').read_text())
        for rel, expected in manifest['files'].items():
            assert sha(source / rel) == expected, str(source / rel)
        pins[str(source / 'manifest.json')] = sha(source / 'manifest.json')
        rows.extend(csv.DictReader((source / 'parity.csv').open()))
    assert len(rows) == len({r['input_inchikey'] for r in rows})
    x = [float(r['measured_logKow']) for r in rows]
    y = [float(r['predicted_logKow']) for r in rows]
    residual = [b-a for a,b in zip(x,y)]
    assert all(math.isfinite(v) for v in x+y+residual)
    n = len(rows)
    mean = lambda values: sum(values)/len(values)
    xx = sum((v-mean(x))**2 for v in x)
    slope = sum((a-mean(x))*(b-mean(y)) for a,b in zip(x,y))/xx if xx else None
    intercept = mean(y)-slope*mean(x) if slope is not None else None
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), n=n,
        MAE=mean([abs(v) for v in residual]), RMSE=math.sqrt(mean([v*v for v in residual])),
        bias=mean(residual), slope=slope, intercept=intercept,
        regression_residual_degrees_of_freedom=max(0,n-2),
        regression_interpretation='Descriptive only; two points give no residual degrees of freedom or useful uncertainty estimate.',
        source_manifest_sha256=pins,
        limitations='Small selected reference subset; differing measurement methods and phase conditions. No recalibration; prior releases and prior validation outputs unchanged.')
    out.mkdir(parents=True)
    with (out/'parity.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    files={p.name:sha(p) for p in out.iterdir() if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(dict(status='supplement_complete',files=files),indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
