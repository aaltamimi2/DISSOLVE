"""Preserve the failed control attempt; prepare an exact original-batch control.

No scheduler action and no tolerance or solver change. Run before staging v2.
"""
import json
from pathlib import Path

D = Path('/mnt/r/plastchem-euler/phase10-v1')
A = D.parent / 'phase9-v1'


def main():
    plan = json.loads((D / 'calibration-plan.json').read_text())
    groups = {}
    for name in ['production-plan.json', 'chunk-probe-plan.json']:
        source = json.loads((A / name).read_text())
        for chunk in source['chunks']:
            for offset in range(0, len(chunk), source['subbatch']):
                group = [{key: u[key] for key in ['id', 'surface', 'surface_sha256']}
                         for u in chunk[offset:offset+source['subbatch']]]
                for member in group:
                    assert member['id'] not in groups
                    groups[member['id']] = group
    assert len(groups) == 5830
    for chunk in plan['chunks']:
        for u in chunk:
            group = groups[u['id']]
            own = next(m for m in group if m['id'] == u['id'])
            assert own['surface_sha256'] == u['surface_sha256']
            u['control_original_batch'] = group
    plan.update(output='calibration-results-v2', worker_pins_file='worker-pins-v2.json',
        control_design='Exact original A-9 subbatch membership and order; tolerance unchanged at 1e-9')
    target = D / 'calibration-plan-v2.json'
    raw = json.dumps(plan, indent=2) + '\n'
    if target.exists():
        assert target.read_text() == raw, 'Existing pinned v2 plan differs'
    else:
        target.write_text(raw)
    print('Exact original batch membership verified for 40 calibration molecules')


if __name__ == '__main__': main()
