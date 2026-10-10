"""Compare supplemental controls with frozen releases and independent averaging."""
import argparse
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path

import duckdb

B=Path('/mnt/r/plastchem-euler')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--partial-polymers',nargs='+',help='Explicitly audit only completed polymer units of an interrupted run')
    args=ap.parse_args()
    out=args.output.resolve()
    assert out.is_relative_to(B/'supplement-v1') and not out.exists()
    plan=json.loads(args.plan.read_text());spec=json.loads(Path(plan['inputs']).read_text())
    root=Path(plan['output'])
    if args.partial_polymers:
        assert set(args.partial_polymers)<set(plan['polymers'])
        plan['polymers']=args.partial_polymers
        one=json.loads(next((root/'activities').glob('*.json.sha256.json')).read_text())
        payload=json.loads(next(p for p in (root/'activities').glob('*.json') if not p.name.endswith('.sha256.json')).read_text())
        complete=dict(status='partition_complete_lle_not_computed',signature={k:one['signature'][k] for k in ('plan_sha256','inputs_sha256','code_pins')},
            execution=payload['execution'],rows=len(plan['keys'])*len(plan['polymers'])*len(plan['solvents'])*2,peak_rss_kib=None,wall_seconds=None)
    else:
        complete=json.loads((root/'complete.json').read_text())
    assert complete['status']=='partition_complete_lle_not_computed'
    assert complete['signature']['plan_sha256']==sha(args.plan)
    assert sha(plan['inputs'])==plan['inputs_sha256']
    expected_phases={'control-solvent-'+s for s in plan['direct_solvent_controls']}
    expected_phases|={'polymer-'+r['entry_id'] for p in plan['polymers'] for r in spec['polymers'][p]}
    actual_phases={p.name.removesuffix('.json.sha256.json') for p in (root/'activities').glob('*.json.sha256.json')}
    assert expected_phases<=actual_phases if args.partial_polymers else expected_phases==actual_phases
    source_pins={}
    for p in root.rglob('*.json.sha256.json'):
        seal=json.loads(p.read_text());payload=p.with_name(p.name.removesuffix('.sha256.json'))
        assert sha(payload)==seal['sha256']
        assert all(seal['signature'].get(k)==v for k,v in complete['signature'].items())
        source_pins[str(payload)]=seal['sha256']
    rows=[]
    expected_parts={root/'partition'/p/(k+'.json') for p in plan['polymers'] for k in plan['keys']}
    actual_parts={p for polymer in plan['polymers'] for p in (root/'partition'/polymer).glob('*.json') if not p.name.endswith('.sha256.json')}
    assert actual_parts==expected_parts
    max_gamma_error=0.;max_volume_error=0.
    for polymer in plan['polymers']:
        conformers=spec['polymers'][polymer]
        energy=[c['B_energy_hartree'] for c in conformers];emin=min(energy)
        for index,key in enumerate(plan['keys']):
            values=[json.loads((root/'activities'/('polymer-'+c['entry_id']+'.json')).read_text())['values'][index] for c in conformers]
            pair=json.loads((root/'partition'/polymer/(key+'.json')).read_text());assert len(pair)==len(plan['solvents'])*2
            for r in pair:
                assert r['input_inchikey']==key and r['polymer']==polymer
                normalized=r['convention']=='normalized'
                factor=2625.4996394799/(.00831446261815324*298.15) if normalized else 627.5094740631/(.0019872041*298.15)
                weights=[math.exp(-(e-emin)*factor) for e in energy];z=math.fsum(weights)
                # Independent direct weighted sum, rather than production logsumexp.
                avg=math.fsum(w*math.exp(-v) for w,v in zip(weights,values))
                expected_gamma=-math.log(avg/z if normalized else avg)
                expected_volume=math.fsum(w*c['B_cavity_cm3_mol'] for w,c in zip(weights,conformers))/z
                max_gamma_error=max(max_gamma_error,abs(expected_gamma-r['ln_gamma_polymer']))
                max_volume_error=max(max_volume_error,abs(expected_volume-r['polymer_volume_cm3_mol']))
                assert abs(expected_gamma-r['ln_gamma_polymer'])<1e-11
                assert abs(expected_volume-r['polymer_volume_cm3_mol'])<1e-10
                x=(r['ln_gamma_polymer']-r['ln_gamma_solvent'])/math.log(10)
                assert abs(x-r['logP_x'])<1e-12
                assert abs(x+math.log10(r['polymer_volume_cm3_mol']/r['solvent_volume_cm3_mol'])-r['logP_concentration'])<1e-12
                rows.append(r)
    def key(r):return(r['input_inchikey'],r['polymer'],r['product_solvent_key'],r['convention'])
    assert len(rows)==len({key(r) for r in rows})==complete['rows']
    con=duckdb.connect();con.execute("SET threads=1");con.execute("SET memory_limit='128MB'")
    reference={}
    for release in ('promotion-v1','promotion-ext39-v1'):
        path=B/release/'partition.parquet';source_pins[str(path)]=sha(path)
        records=con.execute('SELECT input_inchikey,campaign_polymer,product_solvent_key,convention,logP_x,logP_concentration FROM read_parquet(?) WHERE input_inchikey IN (SELECT unnest(?)) AND campaign_polymer IN (SELECT unnest(?))',[str(path),plan['keys'],plan['original_batch_polymer_controls']]).fetchall()
        for r in records:
            k=tuple(r[:4]);assert k not in reference;reference[k]=r[4:]
    comparisons=[]
    for r in rows:
        if r['polymer'] not in plan['original_batch_polymer_controls']:continue
        ref=reference[key(r)];comparisons.append(dict(input_inchikey=r['input_inchikey'],polymer=r['polymer'],solvent=r['product_solvent_key'],convention=r['convention'],new_logP_x=r['logP_x'],released_logP_x=ref[0],difference_logP_x=r['logP_x']-ref[0],new_logP_concentration=r['logP_concentration'],released_logP_concentration=ref[1],difference_logP_concentration=r['logP_concentration']-ref[1]))
    assert len(comparisons)==len(reference)==len(plan['keys'])*len(plan['original_batch_polymer_controls'])*len(plan['solvents'])*2
    maximum=max(max(abs(r['difference_logP_x']),abs(r['difference_logP_concentration'])) for r in comparisons)
    controls={s:json.loads((root/'controls'/(s+'.json')).read_text())['max_difference'] for s in plan['direct_solvent_controls']}
    passed=maximum<=1e-9 and max(controls.values())<=1e-9
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status=('passed_partial_local_reproduction' if args.partial_polymers else 'passed_local_reproduction') if passed else 'failed_local_reproduction',
        partial=bool(args.partial_polymers),audited_polymers=plan['polymers'],full_worker_completed=(root/'complete.json').exists(),
        rows=len(rows),frozen_reference_comparisons=len(comparisons),max_partition_difference=maximum,
        direct_activity_control_errors=controls,max_independent_ensemble_gamma_error=max_gamma_error,
        max_independent_ensemble_volume_error=max_volume_error,temperature_K=298.15,solute_mole_fraction=0,
        cpu_model=complete['execution']['cpu_model'],peak_rss_kib=complete['peak_rss_kib'],wall_seconds=complete['wall_seconds'],
        plan_sha256=sha(args.plan),source_sha256=source_pins,
        limitations='Small local arithmetic/implementation reproduction only. No experimental validation, route-A comparison, LLE calculation, Milan calibration or full supplementary production is claimed.')
    out.mkdir(parents=True)
    for filename,rs in [('frozen-reference-comparisons.csv',comparisons),('partition-control-rows.csv',rows)]:
        with (out/filename).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (out/'manifest.json').write_text(json.dumps(dict(status=summary['status'],files={p.name:sha(p) for p in out.iterdir() if p.is_file()}),indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='source_sha256'}))
    assert passed, 'Partition reproduction exceeds 1e-9; retain report and stop'

if __name__=='__main__':main()
