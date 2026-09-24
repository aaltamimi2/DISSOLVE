"""Execute the owner-authorized A-10 measured gate and launch without idle delay.

No inference of approval: the charter explicitly authorizes <=510 CPU-hours.
Changed code/charter, failed calibration, above-gate cost, or ambiguous launch
stops for inspection. No blind retry after a submission error.
"""
import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')
REPORT=R/'reports/phase10-2026-09-24'


def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,data):
    temp=p.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(p)


def status(name,**details):
    row=dict(utc=utc(),status=name,**details)
    save(D/'launch-watch-status.json',row);print(json.dumps(row),flush=True)


def invoke(name):
    p=subprocess.run([sys.executable,str(R/'scripts'/name)],cwd=R,text=True,capture_output=True)
    with (D/'launch-watch-commands.jsonl').open('a') as f:
        f.write(json.dumps(dict(utc=utc(),script=name,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr))+'\n')
    assert p.returncode==0,(name,p.stderr[-3000:])
    return json.loads(p.stdout)


def main():
    lock=(R/'state/phase10-launch-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    names=['CHARTER.txt','scripts/watch_phase10_launch.py','scripts/finalize_phase10_calibration.py',
           'scripts/launch_phase10_production.py','scripts/submit_phase10_production_remote.py',
           'scripts/observe_phase10_calibration_remote.py']
    pins={n:sha(R/n) for n in names}
    save(D/'launch-watch-process.json',dict(pid=os.getpid(),utc=utc(),pins=pins))
    last=None
    while True:
        try:
            for name,digest in pins.items():assert sha(R/name)==digest,'Review changed input: '+name
            primary=json.loads((D.parent/'phase9-v1/release-watch-status.json').read_text())
            reason='waiting_for_complete_calibration'
            obs=json.loads((D/'calibration-v2-observation.json').read_text())
            assert obs['status'] not in ['observer_error','inspection_required'],obs.get('failures',obs['status'])
            if primary['status'] in ['running_full_audit_and_build','running_independent_delivery_verification']:
                reason='deferred_primary_release_busy'
            elif obs['status']=='measurement_complete_review_required':
                expected={obs['job_id']+'_'+str(i) for i in range(9)}
                states={r.split('|')[0]:r.split('|')[1] for r in obs['accounting'].splitlines()
                        if r.split('|')[0] in expected}
                if states!={j:'COMPLETED' for j in expected}:
                    status('terminal_accounting_refresh_required',states=states)
                    return 1
                gate_path=D/'calibration-clearance.json'
                if not gate_path.exists():
                    gate=invoke('finalize_phase10_calibration.py')
                    shutil.copyfile(gate_path,REPORT/'calibration-clearance.json')
                    with (REPORT/'REPORT.md').open('a') as f:
                        f.write(f"\n## Completed measured cost gate ({gate['utc']})\n\nAll 40 calibration molecules and 1,560 RT LLE systems completed. All 80 identical-batch control comparisons pass (maximum difference {gate['max_control_difference']}). The conservative calibrated projection is **{gate['projected_CPU_h']:.3f} CPU-hours**, including the 25% allowance and both calibration attempts; limit **510 CPU-hours**. Engineering sensitivity is {gate['engineering_sensitivity_CPU_h'][0]:.3f}–{gate['engineering_sensitivity_CPU_h'][1]:.3f} CPU-hours, not a confidence interval. Peak RSS: {gate['peak_RSS_MiB']:.1f} MiB. Measured status counts: `{json.dumps(gate['LLE_statuses'],sort_keys=True)}`. Gate decision: **{gate['status']}**. Source/code pins and component costs are in `calibration-clearance.json`. No original panel value was changed.\n")
                else:gate=json.loads(gate_path.read_text())
                if gate['status']!='passed':
                    status('above_cost_gate_stop',projected_CPU_h=gate['projected_CPU_h']);return
                outcome=invoke('launch_phase10_production.py')
                if outcome['status'].startswith('deferred_'):reason=outcome['status']
                else:
                    assert outcome['status']=='submitted_held_allocator_release_pending'
                    shutil.copyfile(D/'production-submission.json',REPORT/'production-submission.json')
                    with (REPORT/'REPORT.md').open('a') as f:
                        f.write(f"\nProduction array **{outcome['job_id']}** submitted at **{outcome['submitted_utc']}**, 58 chunks, 5,790 new molecules plus 40 reused calibration molecules. Reconciliation decision: `{outcome['decision']}`. Requests: one CPU, 4 GB, eight hours, research `(milan|genoa)&cpu`; throttle 27, submitted held for the shared allocator. This is a submission receipt, not yet evidence of starts. The 64-slot global cap is unchanged. Original `promotion-v1` release has priority; extension bulk collection defers until its independent verification passes.\n")
                    status('submitted_held_allocator_release_pending',**{k:v for k,v in outcome.items() if k!='status'})
                    return
            if reason!=last:status(reason);last=reason
            time.sleep(30)
        except Exception as exc:
            status('inspection_required',error=repr(exc),traceback=traceback.format_exc());return 1


if __name__=='__main__':raise SystemExit(main())
