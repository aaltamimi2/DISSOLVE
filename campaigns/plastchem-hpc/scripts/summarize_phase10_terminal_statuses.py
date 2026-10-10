"""Enumerate solver-reported unresolved outcomes before full return validation.

This checks complete footer ownership/coverage, not the underlying numerics.
"""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path

D=Path('/mnt/r/plastchem-euler/phase10-v1')
QUALIFIED={'single_liquid_phase','two_liquid_phases'}
ALLOWED=QUALIFIED|{'grid_or_tie_line_unresolved','grid_not_converged','activity_nonconvergence'}


def main():
    raw=(D/'latest-gate-status.json').read_bytes();snapshot=json.loads(raw)
    manifest=json.loads((D/'manifest.json').read_text())
    solvents={s['name'] for s in manifest['solvents']};assert len(solvents)==39
    expected={};units={};pins={}
    for root,name in [('calibration-results-v2','calibration-plan-v2.json'),('production-results-v1','production-plan.json')]:
        data=(D/name).read_bytes();pins[name]=hashlib.sha256(data).hexdigest()
        plan=json.loads(data)
        for i,group in enumerate(plan['chunks']):
            expected[f'{root}/{i:04d}']=group
            for u in group:
                assert u['id'] not in units
                units[u['id']]=u
    assert len(units)==5830 and len(expected)==67 and set(snapshot['complete'])==set(expected)
    counts=collections.Counter();unresolved=[]
    for chunk,group in expected.items():
        footer=snapshot['complete'][chunk]
        assert set(footer['unit_ids'])=={u['id'] for u in group}
        statuses=footer['lle_statuses']
        assert set(statuses)=={f"{u['id']}__{s}__RT" for u in group for s in solvents}
        assert set(statuses.values())<=ALLOWED
        counts.update(statuses.values())
        for key,state in statuses.items():
            if state in QUALIFIED:continue
            unit,solvent,regime=key.split('__');u=units[unit]
            unresolved.append(dict(unit_id=unit,input_inchikey=u['inchikey'],name=u['name'],
                input_smiles=u['input_smiles'],atom_count=u['atoms'],product_solvent_key=solvent,
                temperature_K=298.15,status=state,chunk=chunk,
                qualification='solver_reported_unresolved; full independent return audit pending'))
    assert sum(counts.values())==227370
    reference=json.loads((D/'terminal-status-summary.json').read_text())
    assert dict(counts)==reference['reported_solver_statuses']
    out=D/'terminal-outcome-inventory';out.mkdir(exist_ok=True)
    (out/'footer-snapshot.json').write_bytes(raw)
    unresolved.sort(key=lambda r:(r['input_inchikey'],r['product_solvent_key']))
    with (out/'solver-reported-unresolved.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(unresolved[0]));writer.writeheader();writer.writerows(unresolved)
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='complete_footer_coverage_only',
        system_denominator=227370,contaminant_denominator=5830,reported_statuses=dict(counts),
        unresolved_systems=len(unresolved),affected_contaminants=len({r['input_inchikey'] for r in unresolved}),
        by_solvent=dict(collections.Counter(r['product_solvent_key'] for r in unresolved)),
        snapshot_sha256=hashlib.sha256(raw).hexdigest(),plan_pins=pins,
        unresolved_csv_sha256=hashlib.sha256((out/'solver-reported-unresolved.csv').read_bytes()).hexdigest(),
        scope='Every expected unit/solvent occurs exactly once in terminal footers. Reported solver classifications only; numerical qualification and final release remain pending.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
