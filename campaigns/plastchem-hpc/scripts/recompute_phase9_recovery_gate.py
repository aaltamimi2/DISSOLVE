"""Fresh serial LLE gate solves with the unchanged worker plus failure boundary.

Runs locally under a 2 GiB RSS / 2.5 GiB available-memory guard, with the pinned
package. Each of the 35 solutes is a durable unit. Does not submit cluster jobs.
"""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[key]='1'
import collections
import datetime
import fcntl
import gc
import gzip
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase9-v1')
BASE=D.parent/'phase8-v1'
OUT=D/'recovery-gate-fresh-local'
sys.path.insert(0,str(D))
sys.path.insert(0,str(BASE))
import opencosmorspy
import phase9_worker_cpu as worker
import phase9_failure_policy as policy
from analyze_phase9_gate import collected_records


def guard():
    rss=int(next(l.split()[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('VmRSS:')))*1024
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    if rss>2*1024**3 or available<2.5*1024**3:
        raise MemoryError('Fresh gate paused: 2 GiB RSS / 2.5 GiB available-memory guard')


def main():
    OUT.mkdir(exist_ok=True)
    lock=(R/'state/phase9-v1/recovery-gate-fresh.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    guard()
    assert Path(opencosmorspy.__file__).resolve().parent==BASE/'opencosmorspy'
    for name,digest in json.loads((BASE/'package-pins.json').read_text()).items():
        assert policy.digest(BASE/name)==digest,name
    clearance=json.loads((D/'production-clearance.json').read_text())
    for name,digest in clearance['file_pins'].items():assert policy.digest(D/name)==digest,name
    receipt=json.loads((D/'production-retry-01-submission.json').read_text())
    assert policy.digest(policy.__file__)==receipt['recovery_code_pins']['phase9_failure_policy.py']
    assert policy.digest(worker.__file__)==clearance['file_pins']['phase9_worker_cpu.py']
    plan=json.loads((D/'gate-plan.json').read_text())
    manifest=json.loads((BASE/'manifest.json').read_text())
    validation=json.loads((BASE/'validation-inputs.json').read_text())
    assert policy.digest(BASE/'manifest.json')==plan['manifest_sha256']
    assert policy.digest(BASE/'validation-inputs.json')==plan['validation_sha256']
    signature=dict(script_sha256=policy.digest(__file__),worker_sha256=policy.digest(worker.__file__),
                   failure_policy_sha256=policy.digest(policy.__file__),gate_plan_sha256=policy.digest(D/'gate-plan.json'),
                   manifest_sha256=plan['manifest_sha256'],validation_sha256=plan['validation_sha256'])
    execution=dict(cpu_model=next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')),
                   node=os.uname().nodename,route='fresh local serial solve; not a Milan runtime calibration')
    expected={}
    for path,value in collected_records('gate-results-v1'):
        if '/lle/' in path:
            key=(value['unit'],value['solvent'],value['regime'])
            assert key not in expected;expected[key]=value
    assert len(expected)==2240
    worker.guard=guard
    calculate=policy.wrap(worker.lle_result)
    units=[u for chunk in plan['chunks'] for u in chunk]
    totals=collections.Counter();all_mismatches=[];sealed=[]
    for unit in units:
        target=OUT/(unit['id']+'.jsonl.gz');seal=target.with_suffix(target.suffix+'.sha256.json')
        if seal.exists():
            record=json.loads(seal.read_text())
            assert record['signature']==signature and policy.digest(target)==record['sha256']
        else:
            guard();start=time.monotonic();cache=worker.ProfileCache()
            solute=cache.get(D/unit['surface'],unit['surface_sha256'])
            temp=target.with_suffix('.tmp');mismatches=[];counts=collections.Counter()
            with gzip.open(temp,'wt',compresslevel=1) as f:
                for solvent in manifest['solvents']:
                    profile=cache.get(BASE/solvent['B'])
                    high=next(x for x in validation['lle_units'] if x['solvent']==solvent['name'] and x['regime']=='high')
                    for regime,T in [('RT',298.15),('high',high['temperature_K'])]:
                        guard()
                        value=calculate(solute,profile,T,unit['molecular_weight_g_mol'],
                                        validation['solvent_identities'][solvent['name']]['molecular_weight_g_mol'],plan['grid_batch'])
                        key=(unit['id'],solvent['name'],regime);reference=expected[key]
                        differences={k:dict(reference=reference.get(k),fresh=value.get(k))
                                     for k in ['status','above_15_mol_percent','above_15_wt_percent']
                                     if reference.get(k)!=value.get(k)}
                        if differences:mismatches.append(dict(unit=key[0],solvent=key[1],regime=key[2],differences=differences))
                        counts[value['status']]+=1
                        f.write(json.dumps(dict(unit=key[0],solvent=key[1],regime=key[2],temperature_K=T,result=value),separators=(',',':'))+'\n')
                        f.flush();del value;gc.collect()
            assert sum(counts.values())==64
            temp.replace(target)
            record=dict(signature=signature,execution=execution,unit=unit['id'],systems=64,
                        reference=unit['reference'],status_counts=dict(counts),mismatches=mismatches,
                        wall_seconds=time.monotonic()-start,sha256=policy.digest(target))
            worker.save(seal,record)
            del solute,profile,cache;gc.collect()
        totals.update(record['status_counts']);all_mismatches.extend(record['mismatches'])
        sealed.append(record)
        summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),signature=signature,execution=execution,
                     status='in_progress',completed_units=len(sealed),unit_denominator=35,systems=sum(totals.values()),system_denominator=2240,
                     status_counts=dict(totals),mismatches=all_mismatches,mismatch_count=len(all_mismatches),wall_seconds=sum(x['wall_seconds'] for x in sealed),
                     peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     scope='Fresh local solves using the frozen numerical worker and pinned package plus the exact nonconvergence boundary; all original gate statuses and both verdict bases compared. Local timings are not Milan cost estimates.')
        if len(sealed)==35:
            assert sum(totals.values())==2240
            summary['status']='failed' if all_mismatches else 'passed'
        worker.save(OUT/'summary.json',summary)
        print(json.dumps({k:summary[k] for k in ['utc','status','completed_units','systems','status_counts','mismatch_count','wall_seconds','peak_rss_kib']}),flush=True)
    assert not all_mismatches,all_mismatches


if __name__=='__main__':main()
