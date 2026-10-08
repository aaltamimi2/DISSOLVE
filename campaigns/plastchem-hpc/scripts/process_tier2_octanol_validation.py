"""One serial validation-only octanol pass for the pinned tier-2 measured set.

Requires the ordinary panel worker to have stopped at an idle boundary. The
shared worker lock excludes overlap. Leaves its ledger and all releases intact.
"""
import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import csv
import argparse
import datetime
import fcntl
import hashlib
import json
import math
from pathlib import Path
import platform
import resource

from thermodynamic_prediction import activity, pair, CONFIG, VOLUMES, VOLUME_DATA

ROOT = Path(__file__).resolve().parents[1]
BULK = Path('/mnt/r/plastchem-euler')
SOURCES = BULK / 'tier2-v1/pubchem-measured-20260925'
OUT = BULK / 'tier2-v1/octanol-validation-20260925'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    content = (json.dumps(value, indent=2) + '\n').encode()
    temp = path.with_suffix('.tmp')
    temp.write_bytes(content)
    assert sha(temp) == hashlib.sha256(content).hexdigest()
    temp.replace(path)


def main():
    global SOURCES, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, default=SOURCES)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--key', default='AMFGWXWBFGVCKG-UHFFFAOYSA-N')
    args = parser.parse_args()
    SOURCES, OUT = args.sources.resolve(), args.output.resolve()
    assert SOURCES.is_relative_to(BULK / 'tier2-v1')
    assert OUT.is_relative_to(BULK / 'tier2-v1')
    lock = (ROOT / 'state/thermodynamics-v1/worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not OUT.exists(), 'Preserve prior run; inspect before any retry'
    available = int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])
    assert available >= 2500 * 1024, 'Memory guard'
    refs_path = SOURCES / 'pubchem-measured-validation/best-measured-logKow.csv'
    reference_manifest = json.loads((SOURCES / 'manifest.json').read_text())
    assert sha(refs_path) == reference_manifest['files'][str(refs_path.relative_to(SOURCES))]
    refs = list(csv.DictReader(refs_path.open()))
    assert len(refs) == 1 and refs[0]['input_inchikey'] == args.key
    assert refs[0]['qualification'] == 'qualified' and refs[0]['observed_operator'] == '='
    oct_path = ROOT / 'state/campaign-v1/records/KBPLFHHGFOOTCA-UHFFFAOYSA-N.json'
    oct_record = json.loads(oct_path.read_text())
    assert oct_record['status'] == 'converged' and oct_record['connectivity_match']
    assert oct_record['cpu_model'] == 'AMD EPYC 7763 64-Core Processor'
    assert oct_record['orca_version'] == '6.1.1' and oct_record['orca_git'] == '487d211c'
    surface = Path(oct_record['archive_path']) / 'surface.orcacosmo'
    assert sha(surface) == oct_record['surface_sha256']
    for name, stage in oct_record['stages'].items():
        assert sha(Path(oct_record['archive_path']) / (name + '.inp')) == stage['input_sha256']
    solvent = dict(solvent_key='octanol', surface=str(surface), surface_sha256=oct_record['surface_sha256'])
    volume_path = ROOT / 'state/progress-2026-09-14/octanol-molar-volume.json'
    volume = json.loads(volume_path.read_text())
    assert sha(volume['source_xml_path']) == volume['source_xml_sha256']
    assert volume['inchikey'] == oct_record['inchikey'] and volume['temperature_K'] == 298.15
    VOLUMES['octanol'] = volume['molar_volume_cm3_mol']
    VOLUME_DATA['octanol'] = volume
    ledger_path = ROOT / 'state/tier2-v1/thermodynamics/processing-ledger.json'
    ledger_raw = ledger_path.read_bytes()
    ledger = json.loads(ledger_raw)
    OUT.mkdir()
    (OUT / 'reference.csv').write_bytes(refs_path.read_bytes())
    (OUT / 'processing-ledger-snapshot.json').write_bytes(ledger_raw)
    provenance = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        config=CONFIG, script_sha256=sha(__file__), activity_code_sha256=sha(ROOT / 'scripts/thermodynamic_prediction.py'),
        reference_manifest_sha256=sha(SOURCES / 'manifest.json'), octanol_source_record_sha256=sha(oct_path),
        octanol_surface_sha256=solvent['surface_sha256'], volume=volume,
        execution_host=platform.node(), execution_cpu=next(x.split(':', 1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),
        scope='Validation-only legacy finite-dilution octanol/water at 298.15 K. Not a panel or frozen exact-zero release change.')
    save(OUT / 'provenance.json', provenance)
    rows = []
    for ref in refs:
        key = ref['input_inchikey']
        entry = ledger[key]
        raw = Path(entry['result_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry['result_sha256']
        panel = json.loads(raw)
        record_path = ROOT / 'state/tier2-v1/records' / (key + '.json')
        record = json.loads(record_path.read_text())
        assert record['status'] == 'converged' and record['connectivity_match']
        assert panel['config'] == CONFIG and panel['solute_surface_sha256'] == record['surface_sha256']
        water = panel['activities']['water']
        assert water['status'] == 'converged' and water['config'] == CONFIG
        solute = dict(surface=str(Path(record['archive_path']) / 'surface.orcacosmo'), surface_sha256=record['surface_sha256'])
        octanol = activity(solute, solvent)
        prediction = pair('octanol', 'water', dict(water=water, octanol=octanol))
        result = dict(input_inchikey=key, name=panel['name'], perceived_inchikey=panel['perceived_inchikey'],
            identity_match_basis=panel['identity_match_basis'], reference=ref,
            solute_surface_sha256=record['surface_sha256'], solute_record_sha256=sha(record_path),
            water_source_result_sha256=entry['result_sha256'], water_activity=water,
            octanol_activity=octanol, prediction=prediction, utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        if prediction['status'] == 'predicted':
            direct = (water['ln_gamma'] - octanol['ln_gamma']) / math.log(10)
            correction = math.log10(18.07 / volume['molar_volume_cm3_mol'])
            assert abs(direct - prediction['log10_K_mole_fraction']) < 1e-12
            assert abs(direct + correction - prediction['log10_K_concentration']) < 1e-12
            result['arithmetic_check'] = dict(logK_x=direct, volume_correction=correction, logK_concentration=direct + correction)
            residual = prediction['log10_K_concentration'] - float(ref['measured_logKow'])
            result['residual'] = residual
            rows.append(dict(input_inchikey=key, name=panel['name'], measured_logKow=float(ref['measured_logKow']),
                predicted_logKow=prediction['log10_K_concentration'], residual=residual,
                reference_class=ref['source_class'], source_url=ref['source_url'], reference_citation=ref['raw_reference_string'],
                conditions_note=ref['conditions_note']))
        save(OUT / (key + '.json'), result)
    fields = list(rows[0]) if rows else ['input_inchikey', 'name', 'measured_logKow', 'predicted_logKow', 'residual']
    with (OUT / 'parity.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), reference_denominator=len(refs),
        predicted=len(rows), failed=len(refs) - len(rows), n=len(rows),
        MAE=sum(abs(r['residual']) for r in rows) / len(rows) if rows else None,
        RMSE=math.sqrt(sum(r['residual'] ** 2 for r in rows) / len(rows)) if rows else None,
        bias=sum(r['residual'] for r in rows) / len(rows) if rows else None,
        slope=None, intercept=None, regression_status='Not estimable from one reference molecule',
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        limitations='One reference molecule; no broad accuracy conclusion. Dry neutral model versus measured phase conditions; consult the preserved reference conditions_note. No recalibration. Frozen releases unchanged.')
    save(OUT / 'summary.json', summary)
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    files = {p.name: sha(p) for p in OUT.iterdir() if p.is_file()}
    save(OUT / 'manifest.json', dict(files=files, summary=summary))
    assert ledger_path.read_bytes() == ledger_raw, 'Panel ledger changed during exclusive serial pass'
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
