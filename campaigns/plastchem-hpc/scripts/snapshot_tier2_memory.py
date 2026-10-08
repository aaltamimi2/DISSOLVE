"""Capture reported Slurm batch MaxRSS; never change requested resources."""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output.resolve()
    assert out.is_relative_to(Path('/mnt/r/plastchem-euler/tier2-v1')) and not out.exists()
    rows, pins = [], {}
    for path in sorted((ROOT / 'state/tier2-v1/records').glob('*.json')):
        raw = path.read_bytes()
        record = json.loads(raw)
        a = record.get('slurm_accounting', {})
        rss = a.get('maxrss_kib')
        if rss is None:
            continue
        pins[str(path)] = hashlib.sha256(raw).hexdigest()
        rows.append(dict(input_inchikey=record['inchikey'], name=record['input']['name'],
            disposition=record['status'], scheduler_state=a['state'],
            maxrss_kib=float(rss), maxrss_GiB=float(rss) / 1048576,
            source=a.get('maxrss_source', ''), request_memory=record.get('resources', {}).get('request_memory', '')))
    assert rows and all(r['maxrss_kib'] >= 0 for r in rows)
    peak = max(rows, key=lambda r: r['maxrss_kib'])
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), denominator=270,
        records_with_MaxRSS=len(rows), records_without_MaxRSS=270-len(rows),
        maximum=peak, median_GiB=statistics.median(r['maxrss_GiB'] for r in rows),
        above_3_5_GiB=sum(r['maxrss_GiB'] > 3.5 for r in rows),
        observed_OUT_OF_MEMORY=sum(r['scheduler_state'] == 'OUT_OF_MEMORY' for r in rows),
        request_memory_values=sorted({r['request_memory'] for r in rows}), source_sha256=pins,
        limits='Reported sacct batch-step MaxRSS, not continuous aggregate cgroup measurement or a guarantee of future fit. Missing MaxRSS is not zero usage. No request or cap was changed.')
    out.mkdir()
    with (out / 'memory.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(files=hashes), indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'source_sha256'}))


if __name__ == '__main__':
    main()
