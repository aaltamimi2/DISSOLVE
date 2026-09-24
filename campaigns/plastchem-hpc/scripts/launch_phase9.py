"""Local A-9 launch coordinator. No launch until complete correctness AND cost.

Can be invoked repeatedly by the collector. Remote deterministic reconciliation
prevents duplicate submission. Side-task DFT always follows confirmed A launch.
"""
import datetime, fcntl, hashlib, json, re, shutil, sys, tarfile
from pathlib import Path
from analyze_phase9_gate import collected_records
from euler_transport import run

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-v1')
B = D.parent / 'phase9-solvent-library-v1'
S = R / 'state/phase9-v1'
REPORT = R / 'reports/phase9-2026-09-23'


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def transport(kind, args, **kwargs):
    p = run(kind, args, capture_output=True, text=True, timeout=180, **kwargs)
    assert p.returncode == 0, p.stderr
    return p.stdout


def write(p, value):
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n'); tmp.replace(p)


def report(marker, text):
    path = REPORT / 'REPORT.md'
    current = path.read_text()
    if marker == 'A-9 production launch':
        current = re.sub(r'\*\*Correctness gate complete:.*?\*\*', '**A-9 correctness and cost gates passed; replacement production launched. The UTC receipt and measured costs are recorded below.**', current, count=1)
    if marker not in current:
        path.write_text(current + '\n## ' + marker + '\n\n' + text + '\n')
    shutil.copyfile(path, D / 'REPORT.md')


def prepare_A():
    gate = json.loads((D / 'gate-comparison.json').read_text())
    cost = json.loads((D / 'chunk-cost.json').read_text())
    if gate['status'] != 'reproduction_passed_cost_pending' or cost['status'] == 'incomplete':
        return dict(status='waiting_for_full_gate', gate=gate['status'], cost=cost['status'],
                    lle_compared=gate['lle_compared'], chunk_lle_completed=cost['lle_systems'])
    assert cost['production_sized_calibration_comparison']['status']=='passed', 'Full-sized chunk does not reproduce calibration'
    genoa=json.loads((D/'genoa-comparison.json').read_text()) if (D/'genoa-comparison.json').exists() else None
    if (D/'genoa-gate-submission.json').exists() and (genoa is None or genoa['status']=='incomplete'):
        return dict(status='waiting_for_Genoa_usability_comparison')
    if not cost['cost_gate_passes']:
        status = dict(status='cost_stop_above_500', utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), cost=cost)
        write(D / 'production-cost-stop.json', status)
        report('A-9 cost stop', f"Production was not launched: the complete-chunk planning estimate including diagnostics is {cost['planning_cpu_hours_including_diagnostics']:.2f} CPU-hours, above the 500 CPU-hour ceiling. B DFT is not submitted because A has not launched.")
        return status
    final = D / 'production-clearance.json'
    if not final.exists():
        shutil.copyfile(D / 'gate-comparison.json', D / 'gate-comparison-final.json')
        shutil.copyfile(D / 'chunk-cost.json', D / 'chunk-cost-final.json')
        plan = json.loads((D / 'production-plan-proposed.json').read_text())
        plan['scope'] = 'A-9 production under owner-authorized exact-zero conventions and complete correctness/cost gates'
        plan['constraint']='(milan|genoa)&cpu' if genoa and genoa['status']=='passed' else 'milan&cpu'
        if genoa and genoa['status']=='passed':plan['worker_module']='phase9_worker_cpu'
        write(D / 'production-plan.json', plan)
        for name in ['phase9_worker.py', 'phase9_profiles.py', 'phase9_grid.py', 'phase9_entry.py',
                     'phase9_worker_cpu.py', 'phase9_throttle_remote.py', 'submit_phase9_production_remote.py']:
            shutil.copyfile(R / 'scripts' / name, D / name)
        complete = [(path, value) for path, value in collected_records('chunk-probe-results-v1') if path.endswith('/complete.json')]
        assert len(complete) == 1
        registry = json.loads((D / 'collection.json').read_text())
        relative, value = complete[0]
        assert len(value['unit_ids']) == 100
        names = ['gate-comparison-final.json', 'chunk-cost-final.json', 'production-plan.json',
                 'phase9_worker.py', 'phase9_profiles.py', 'phase9_grid.py', 'phase9_entry.py',
                 'phase9_throttle_remote.py', 'submit_phase9_production_remote.py', 'entry-resume-test.json',
                 'finite-dilution-clarification.json', 'chunk-probe-plan.json','phase9_worker_cpu.py','cpu-worker-guard-diff.json']
        if genoa:names.append('genoa-comparison.json')
        decision = dict(status='passed', utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        authority='A-9 NOTE and A-9 CLARIFICATION, CHARTER.txt', charter_sha256=sha(R / 'CHARTER.txt'),
                        limit_cpu_hours=500, constraint=plan['constraint'],file_pins={name: sha(D / name) for name in names},
                        probe_complete_sha256=registry['files'][relative]['sha256'],
                        entry_resume_test=json.loads((D / 'entry-resume-test.json').read_text()))
        write(final, decision)
    decision = json.loads(final.read_text())
    for name, digest in decision['file_pins'].items(): assert sha(D / name) == digest
    files = [D / name for name in decision['file_pins']] + [final]
    transport('scp', [*[str(p) for p in files], 'euler:plastchem-euler/phase9-v1/'])
    receipt = json.loads(transport('ssh', ['euler', 'python3 ~/plastchem-euler/phase9-v1/submit_phase9_production_remote.py']))
    assert receipt.get('launch_utc'), receipt
    write(D / 'production-submission.json', receipt); write(S / 'production-submission.json', receipt)
    for name in ['gate-comparison-final.json', 'chunk-cost-final.json', 'production-clearance.json']:
        shutil.copyfile(D / name, REPORT / name)
    report('A-9 production launch', f"UTC launch **{receipt['launch_utc']}**, array **{receipt['job_id']}**. The full correctness gate contains 17,733 numerical partition passes, 59 separately documented finite-dilution reference differences, and 2,240/2,240 matching LLE verdicts. Complete-chunk cost: {cost['population_weighted_cpu_hours']:.2f} CPU-hours point estimate; {cost['planning_cpu_hours_including_diagnostics']:.2f} CPU-hours including the 25% allowance and diagnostics. The 100 measured contaminants are reused; 5,730 remaining contaminants run in 58 chunks. The obsolete held tasks of 68234 were cancelled only after clearance; finished raw outputs remain. Shared cap stays 64. Exact commands, queue reconciliation and readback: `/mnt/r/plastchem-euler/phase9-v1/production-submission.json`. Controller changes: `/mnt/r/plastchem-euler/phase9-v1/throttle-changes.jsonl`.")
    return dict(status='A_launched', receipt=receipt)


def prepare_B():
    # This staging step occurs only after a locally confirmed A launch.
    assert json.loads((D / 'production-submission.json').read_text())['launch_utc']
    for name in ['phase9_solvent_runner.py', 'submit_phase9_solvents_remote.py']:
        shutil.copyfile(R / 'scripts' / name, B / name)
    paths = [B / 'solvent_library/manifest.json', B / 'phase9_solvent_runner.py', B / 'submit_phase9_solvents_remote.py']
    paths += sorted((B / 'prepared').glob('*/input.xyz')) + sorted((B / 'prepared').glob('*/preparation.json'))
    write(B / 'staging-pins.json', {str(p.relative_to(B)): sha(p) for p in paths})
    archive = B / 'staging.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for p in paths + [B / 'staging-pins.json']: tar.add(p, arcname=str(p.relative_to(B)), recursive=False)
    transport('ssh', ['euler', 'mkdir -p ~/plastchem-euler/phase9-solvent-library-v1/logs'])
    transport('scp', [str(archive), 'euler:plastchem-euler/phase9-solvent-library-v1/staging.tar.gz'])
    code = """import hashlib,tarfile
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-solvent-library-v1'
p=D/'staging.tar.gz'
assert hashlib.sha256(p.read_bytes()).hexdigest()==DIGEST
with tarfile.open(p) as t:
 for m in t:
  assert m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
 t.extractall(D)
""".replace('DIGEST', repr(sha(archive)))
    transport('ssh', ['euler', 'python3 -'], input=code)
    receipt = json.loads(transport('ssh', ['euler', 'python3 ~/plastchem-euler/phase9-solvent-library-v1/submit_phase9_solvents_remote.py']))
    write(B / 'submission.json', receipt); write(S / 'solvent-submission.json', receipt)
    report('A-9 common-solvent DFT queued', f"Array **{receipt['job_id']}**, 69 solvents, was submitted after A's **{receipt['A_launch_utc']} UTC** launch. It is initially held; the shared-cap controller releases up to four tasks only when A leaves safe capacity. Each task requests one CPU, 4 GB and eight hours on research/Milan, under the unchanged ORCA recipe. Prepared licensed-derived coordinates and resulting surfaces remain exclusively in lane bulk/Euler storage, outside git. The common-set measured cost will be reported before any remainder-grid DFT or 69-solvent contaminant-grid extension.")
    return receipt


def main():
    lock = (S / 'launch.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Retain original receipts rather than regenerating final evidence after launch.
    if (D / 'production-submission.json').exists():
        outcome = dict(status='A_already_launched', receipt=json.loads((D / 'production-submission.json').read_text()))
    else: outcome = prepare_A()
    if outcome['status'] not in ['A_launched', 'A_already_launched']:
        print(json.dumps(outcome)); return outcome
    if not (B / 'submission.json').exists(): outcome['B'] = prepare_B()
    else: outcome['B'] = json.loads((B / 'submission.json').read_text())
    outcome['status'] = 'A_launched_B_queued'
    write(S / 'launch-complete.json', outcome)
    print(json.dumps(outcome)); return outcome


if __name__ == '__main__': main()
