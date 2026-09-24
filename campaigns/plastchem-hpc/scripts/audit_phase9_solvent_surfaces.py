"""Audit returned A-9 solvents and load their 24a profiles; no activity solve.

Uses the existing geometry/surface auditor unchanged through a bulk-only path
adapter. Licensed-derived records, profile metadata and surfaces stay outside git.
"""
import os
for key in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key]='1'
import datetime
import gc
import hashlib
import json
import math
import resource
import subprocess
import sys
from pathlib import Path

R=Path(__file__).resolve().parents[1]
BASE=Path('/mnt/r/plastchem-euler/phase8-v1')
D=BASE.parent/'phase9-solvent-library-v1'


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def guard():
    available=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
    rss=int(next(s.split()[1] for s in Path('/proc/self/status').read_text().splitlines() if s.startswith('VmRSS:')))*1024
    if available<2.5*1024**3 or rss>2*1024**3:
        raise MemoryError('Surface audit paused: host free memory below 2.5 GiB or RSS above 2 GiB')


def main():
    guard()
    pins=json.loads((BASE/'package-pins.json').read_text())
    for name,digest in pins.items():assert sha(BASE/name)==digest,name
    sys.path.insert(0,str(BASE))
    import opencosmorspy
    import numpy as np
    from phase9_profiles import ProfileCache
    assert Path(opencosmorspy.__file__).resolve()==(BASE/'opencosmorspy/__init__.py').resolve()
    registry_path=D/'retrieved.json';registry=json.loads(registry_path.read_text())
    records=[]
    for key,pin in sorted(registry.items()):
        if pin['status']!='converged':continue
        path=D/'results'/key/'verified-result.json'
        assert sha(path)==pin['result_sha256']
        records.append((key,path,json.loads(path.read_text())))
    assert records,'No converged solvent returns to audit'
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=D/'surface-audits'/stamp
    folder=out/'freeze/state/solvent-library-v1/records';folder.mkdir(parents=True)
    inputs=[]
    for key,path,record in records:
        adapted=dict(record,archive_path=str(path.parent))
        (folder/(key+'.json')).write_text(json.dumps(adapted,indent=2)+'\n')
        inputs.append(dict(inchikey=key,source_record=str(path),source_record_sha256=sha(path),
                           surface_sha256=record['surface_sha256']))
    (out/'snapshot.json').write_text(json.dumps(dict(utc=stamp,denominator=69,
        converged_in_snapshot=len(records),input_records=inputs,
        adaptation='Only archive_path added to copied records for the unchanged auditor; original verified records untouched.',
        registry_sha256=sha(registry_path),package_pins_sha256=sha(BASE/'package-pins.json')),indent=2)+'\n')
    auditor=R/'scripts/audit_completed_surfaces.py'
    run=subprocess.run([sys.executable,str(auditor),'--frozen','--support-solvents'],
        env=dict(os.environ,PLASTCHEM_PROGRESS_ROOT=str(out),PYTHONPATH=str(BASE)),
        capture_output=True,text=True)
    (out/'surface-auditor-stdout.txt').write_text(run.stdout)
    (out/'surface-auditor-stderr.txt').write_text(run.stderr)
    assert run.returncode==0,run.stderr
    evidence=json.loads((out/'support-solvent-surface-audit.json').read_text())
    bykey={row['inchikey']:row for row in evidence['rows']}
    profile_rows=[]
    for key,path,record in records:
        guard()
        row=dict(inchikey=key,surface_sha256=record['surface_sha256'])
        if bykey[key]['status']!='passed':
            row.update(status='not_loaded_surface_audit_failed',error=bykey[key].get('error'))
        else:
            try:
                cache=ProfileCache()
                profile=cache.get(path.parent/'surface.orcacosmo',record['surface_sha256'])
                assert math.isfinite(profile['area']) and profile['area']>0
                assert math.isfinite(profile['volume']) and profile['volume']>0
                areas=np.asarray(list(profile['areas'].values()),dtype=float)
                assert len(areas)>0 and np.isfinite(areas).all() and (areas>=0).all()
                relative=abs(float(areas.sum())-profile['area'])/profile['area']
                assert relative<1e-4,'Clustered segment area differs from surface area'
                row.update(status='loaded_24a_profile',segment_types=len(profile['descriptors']),
                           clustered_area_relative_error=relative,area=profile['area'],volume=profile['volume'])
                del profile,cache
            except Exception as exc:
                row.update(status='profile_load_failed',error=repr(exc))
        profile_rows.append(row);gc.collect()
        (out/'profile-loading.json').write_text(json.dumps(profile_rows,indent=2)+'\n')
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),denominator=69,
        converged_snapshot=len(records),surface_checks_passed=evidence['passed'],surface_checks_failed=evidence['failed'],
        profiles_loaded=sum(r['status']=='loaded_24a_profile' for r in profile_rows),
        profile_load_failures=sum(r['status']=='profile_load_failed' for r in profile_rows),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        path=str(out),surface_auditor_sha256=sha(auditor),profile_loader_sha256=sha(R/'scripts/phase9_profiles.py'),
        scope='Return provenance, geometry/surface structure, and 24a profile loading only. No activity solve, no 69-solvent contaminant grid, no experimental-accuracy claim.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    files={str(p.relative_to(out)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in out.rglob('*') if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(files,indent=2)+'\n')
    (D/'latest-surface-audit.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
