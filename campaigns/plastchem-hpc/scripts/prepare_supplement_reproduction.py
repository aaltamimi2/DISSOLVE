"""Construct a four-anchor control plan with original batch membership/order."""
import argparse
import hashlib
import json
from pathlib import Path

R=Path(__file__).resolve().parents[1]
B=Path('/mnt/r/plastchem-euler')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inputs',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args()
    spec=json.loads(args.inputs.read_text())
    assert spec['status']=='prepared_requires_reproduction_and_measured_cost_gate'
    out=args.output.resolve()
    assert out.is_relative_to(B/'supplement-v1') and not out.exists()
    keys=[spec['anchors'][name] for name in ('DEP','DBP','BBP','DEHP')]
    source=B/'phase10-v1/calibration-plan-v2.json'
    original=json.loads(source.read_text())
    by_sha={u['surface_sha256']:u['surface'] for u in spec['frozen'].values()}
    controls={}
    for chunk in original['chunks']:
        for unit in chunk:
            if unit['inchikey'] not in keys:
                continue
            members=[]
            for m in unit['control_original_batch']:
                path=by_sha[m['surface_sha256']]
                assert sha(path)==m['surface_sha256']
                members.append(dict(id=m['id'],surface=path,surface_sha256=m['surface_sha256']))
            controls[unit['inchikey']]=dict(id=unit['id'],members=members)
    assert set(controls)==set(keys)
    names=['supplement_partition_worker.py','phase9_profiles.py','phase9_worker_cpu.py','phase9_grid.py']
    pins={str(R/'scripts'/n):sha(R/'scripts'/n) for n in names}
    pins[str(B/'phase8-v1/phase8_lle.py')]=sha(B/'phase8-v1/phase8_lle.py')
    plan=dict(purpose='reproduction_only_not_production',inputs=str(args.inputs.resolve()),inputs_sha256=sha(args.inputs),
        keys=keys,polymers=['pe','pvc','polyethersulfone','polyurethane'],solvents=sorted(spec['solvents']),
        solvent_mode='frozen_cache',direct_solvent_controls=['water','hexane'],subbatch=4,
        original_control_batches=controls,original_batch_polymer_controls=['pe','pvc'],
        original_batch_source=str(source),original_batch_source_sha256=sha(source),
        output=str(out/'results'),code_pins=pins,preparation_script_sha256=sha(__file__))
    out.mkdir(parents=True)
    (out/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    (out/'prepare_supplement_reproduction.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(dict(plan=str(out/'plan.json'),sha256=sha(out/'plan.json'),original_batch_sizes=[len(c['members']) for c in controls.values()])))

if __name__=='__main__':main()
