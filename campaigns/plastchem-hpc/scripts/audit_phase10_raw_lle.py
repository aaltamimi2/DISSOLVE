"""A-10 raw-cache validation, preserving the tested A-9 numerical auditor.

Only after the original release and its raw audit are complete. Checkpoints are
per archive; this is a supplement, not a second COSMOspace calculation.
"""
import collections,csv,datetime,gzip,hashlib,json,math,os,resource,tarfile
from pathlib import Path
import audit_phase9_raw_lle as core
from audit_phase9_raw_lle import audit_value,sha,QUALIFIED
D=Path('/mnt/r/plastchem-euler/phase10-v1')
OUT=D/'raw-lle-audit-v1'
CORE_PIN='c30957667add9d089520e47ddb11116aefb32bec111e274526decc83fd2a372f'

def main():
    assert sha(Path(core.__file__))==CORE_PIN
    primary=D.parent/'phase9-v1'
    verification=json.loads((primary/'delivery-verification.json').read_text())
    assert verification['status']=='complete_delivery_verified'
    assert verification['manifest_sha256']==sha(D.parent/'promotion-v1/manifest.json')
    assert json.loads((primary/'raw-lle-audit-v2/summary.json').read_text())['status']=='complete','Primary raw validation has priority'
    OUT.mkdir(exist_ok=True)
    import fcntl
    with (OUT/'audit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code_sha = sha(Path(__file__))
        pin = OUT/'script.sha256'
        if pin.exists(): assert pin.read_text().strip() == code_sha, 'Do not mix auditor versions'
        else: pin.write_text(code_sha+'\n')
        registry = json.loads((D/'collection.json').read_text())
        totals = collections.Counter()
        for item in registry['archives']:
            archive = Path(item['path'])
            receipt = OUT/(archive.name+'.json')
            if receipt.exists():
                old = json.loads(receipt.read_text())
                assert old['archive_sha256'] == item['sha256'] and old['auditor_sha256'] == code_sha
                assert sha(OUT/old['rows_file']) == old['rows_sha256']
                totals.update(old['statuses'])
                continue
            available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
            assert available >= 2.5*1024**3, 'Pause cleanly between archives for memory'
            assert sha(archive) == item['sha256']
            records = []
            with tarfile.open(archive, 'r:gz') as stream:
                pins = None
                for member in stream:
                    if member.name == 'return-pins.json':
                        pins = json.load(stream.extractfile(member)); continue
                    if not member.name.startswith(('production-results-v1/', 'calibration-results-v2/')): continue
                    if '/lle/' not in member.name or member.name.endswith('.sha256.json'): continue
                    raw = stream.extractfile(member).read()
                    assert pins and hashlib.sha256(raw).hexdigest() == pins[member.name]
                    assert registry['files'][member.name]['sha256'] == pins[member.name]
                    value = json.loads(raw)
                    checked = audit_value(value)
                    records.append(dict(path=member.name, unit=value['unit'],
                        solvent=value['solvent'], regime=value['regime'], **checked))
            name = archive.name+'.rows.jsonl.gz'
            tmp = OUT/(name+'.tmp')
            with gzip.open(tmp, 'wt') as stream:
                for row in records: stream.write(json.dumps(row)+'\n')
            tmp.replace(OUT/name)
            counts = dict(collections.Counter(r['status'] for r in records))
            result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          archive_sha256=item['sha256'], auditor_sha256=code_sha,
                          rows=len(records), statuses=counts, rows_file=name,
                          rows_sha256=sha(OUT/name), peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            tmp = receipt.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(receipt)
            totals.update(counts)
            print(json.dumps(dict(utc=result['utc'], audited_LLE=sum(totals.values()), last_archive_rows=len(records), peak_rss_kib=result['peak_rss_kib'])), flush=True)
        manifest = json.loads((D/'manifest.json').read_text())
        positions = {key:i for i,key in enumerate(sorted((r['name'],'RT') for r in manifest['solvents']))}
        assert len(positions)==39
        masks = collections.defaultdict(int)
        aggregate = collections.Counter()
        maxima = collections.defaultdict(float)
        evidence_files = {}
        for item in registry['archives']:
            receipt = OUT/(Path(item['path']).name+'.json')
            checked = json.loads(receipt.read_text())
            evidence_files[receipt.name] = sha(receipt)
            with gzip.open(OUT/checked['rows_file'], 'rt') as stream:
                for line in stream:
                    row = json.loads(line)
                    bit = 1 << positions[row['solvent'], row['regime']]
                    assert not masks[row['unit']] & bit, ('duplicate LLE ownership', row['path'])
                    masks[row['unit']] |= bit
                    for key in ['checked_grids', 'checked_ties', 'checked_cache_points']:
                        aggregate[key] += row[key]
                    for key, value in row.items():
                        if key.startswith('maximum_'): maxima[key] = max(maxima[key], value)
        expected_units = {f'cohort-{i:05d}' for i in range(5830)}
        assert set(masks) <= expected_units
        complete = set(masks) == expected_units and all(m == (1 << 39)-1 for m in masks.values())
        if complete:
            assert sum(totals.values())==227370
            result_path=D/'results-audit.json'
            if result_path.exists():
                independent=json.loads(result_path.read_text())
                if independent['status']=='complete':assert dict(totals)==independent['LLE_statuses']
        assert aggregate['checked_grids'] == 2*sum(totals[k] for k in QUALIFIED)
        summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       status='complete' if complete else 'passed_for_collected_subset',
                       systems=sum(totals.values()), denominator=227370,
                       fully_evaluated_contaminants=sum(m == (1 << 39)-1 for m in masks.values()),
                       statuses=dict(totals), reconstructed_counts=dict(aggregate), maximum_errors=dict(maxima),
                       auditor_sha256=code_sha, numerical_validator_sha256=CORE_PIN,
                       archive_audit_receipts=evidence_files,
                       registry_snapshot_sha256=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest(),
                       scope='Independent exact-saved-coordinate reconstruction of full-cache convexity, both-grid endpoint chemical potential equality and full-cache tangent stability for qualified rows; unresolved rows retained without qualification. Does not re-solve COSMOspace or establish experimental accuracy.')
        (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary),flush=True)


if __name__ == '__main__': main()
