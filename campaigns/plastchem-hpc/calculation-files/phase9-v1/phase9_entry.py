"""Resume-safe entry point around the frozen A-9 scientific worker.

Never overwrite a completed chunk's original timing or execution provenance.
Partial chunks resume through the worker's per-phase digest checks. No formulas
or numerical operations are changed by this wrapper.
"""
import importlib,json,sys
from pathlib import Path


def main(planpath,index):
    planpath=Path(planpath);plan=json.loads(planpath.read_text());units=plan['chunks'][index]
    module=plan.get('worker_module','phase9_worker')
    assert module in ['phase9_worker','phase9_worker_cpu']
    worker=importlib.import_module(module)
    folder=worker.D/plan['output']/f'{index:04d}';complete=folder/'complete.json'
    if not complete.exists():
        return worker.main(planpath,index)
    expected=dict(plan_sha256=worker.sha(planpath),worker_sha256=worker.sha(worker.__file__),
                  profiles_sha256=worker.sha(worker.D/'phase9_profiles.py'),
                  grid_sha256=worker.sha(worker.D/'phase9_grid.py'),
                  solver_sha256=worker.sha(worker.BASE/'phase8_lle.py'),
                  package_pins_sha256=worker.sha(worker.BASE/'package-pins.json'))
    stamp=json.loads(complete.read_text());assert stamp['signature']==expected
    assert stamp['unit_ids']==[u['id'] for u in units]
    assert len(stamp['lle_statuses'])==64*len(units)
    manifest=json.loads((worker.BASE/'manifest.json').read_text())
    expected_counts=dict(activities=len(manifest['solvents'])+sum(len(rs) for rs in manifest['polymers'].values()),partition=len(units),lle=64*len(units))
    for group,count in expected_counts.items():
        seals=list((folder/group).glob('*.json.sha256.json'));assert len(seals)==count,(group,len(seals),count)
        for seal in seals:
            data=json.loads(seal.read_text());assert all(data['signature'].get(k)==v for k,v in expected.items())
            payload=seal.with_name(seal.name[:-len('.sha256.json')]);assert worker.sha(payload)==data['sha256']
    print(json.dumps(dict(status='completed_chunk_verified_and_skipped',chunk=index,complete_sha256=worker.sha(complete),original_execution=stamp['execution'],entry_sha256=worker.sha(__file__))),flush=True)


if __name__=='__main__':main(sys.argv[1],int(sys.argv[2]))
