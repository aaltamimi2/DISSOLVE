"""Prepare exact disjoint A-10 production ownership, without submitting jobs."""
import hashlib
import json
from pathlib import Path

D = Path('/mnt/r/plastchem-euler/phase10-v1')
A = D.parent / 'phase9-v1'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    cal = json.loads((D / 'calibration-plan-v2.json').read_text())
    original = json.loads((D / 'cohort-units.json').read_text())
    byunit = {u['id']: u for u in original}
    assert len(byunit) == len(original) == 5830
    reused = {u['id'] for c in cal['chunks'] for u in c}
    assert len(reused) == 40
    groups = {}
    for name in ['production-plan.json', 'chunk-probe-plan.json']:
        plan = json.loads((A / name).read_text())
        for chunk in plan['chunks']:
            for offset in range(0, len(chunk), plan['subbatch']):
                group = [{k: u[k] for k in ['id', 'surface', 'surface_sha256']}
                         for u in chunk[offset:offset+plan['subbatch']]]
                for u in group:
                    assert u['id'] not in groups
                    groups[u['id']] = group
    units = []
    for key in sorted(byunit):
        if key in reused:
            continue
        u = dict(byunit[key], control_original_batch=groups[key])
        units.append(u)
    assert len(units) == 5790
    chunks = [units[i:i+100] for i in range(0, len(units), 100)]
    assert len(chunks) == 58
    plan = dict(scope='A-10 production; requires separately verified measured calibration gate',
        manifest_sha256=cal['manifest_sha256'], chunks=chunks,
        subbatch=10, grid_batch=256, output='production-results-v1',
        cost_limit_CPU_h=510, production_chunk_size=100, constraint='(milan|genoa)&cpu',
        worker_pins_file='worker-pins-v2.json',
        control_design=cal['control_design'],
        calibration_plan_sha256=sha(D / 'calibration-plan-v2.json'),
        reused_calibration_units=sorted(reused), full_denominator=5830)
    p = D / 'production-plan.json'
    raw = json.dumps(plan, separators=(',', ':')) + '\n'
    if p.exists():
        assert p.read_text() == raw, 'Pinned production plan differs'
    else:
        p.write_text(raw)
    print(json.dumps(dict(production_units=len(units), reused_units=len(reused),
                         chunks=len(chunks), plan_sha256=sha(p), bytes=p.stat().st_size)))


if __name__ == '__main__': main()
