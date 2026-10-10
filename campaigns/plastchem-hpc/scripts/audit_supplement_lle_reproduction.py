"""Compare small supplemental LLE controls with the sealed A-10 panel."""
import argparse
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
    ap.add_argument('--plan',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args();plan=json.loads(args.plan.read_text());root=Path(plan['output'])
    complete=json.loads((root/'complete.json').read_text())
    assert complete['signature']['plan_sha256']==sha(args.plan)
    assert complete['status']=='lle_systems_complete_including_unresolved'
    con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='128MB'")
    source=B/'promotion-v1/binary-lle.parquet';con.from_parquet(str(source)).create_view('ref')
    comparisons=[];pins={str(source):sha(source),str(args.plan):sha(args.plan)}
    for system in plan['systems']:
        key=system['inchikey'];solvent=system['solvent'];regime=system['regime']
        path=root/'systems'/(key+'__'+solvent+'__'+regime+'.json')
        seal=json.loads(path.with_suffix('.json.sha256.json').read_text());assert sha(path)==seal['sha256']
        assert all(seal['signature'].get(k)==v for k,v in complete['signature'].items())
        pins[str(path)]=seal['sha256'];new=json.loads(path.read_text())
        query=con.execute('SELECT * FROM ref WHERE input_inchikey=? AND product_solvent_key=? AND temperature_regime=?',[key,solvent,regime])
        records=query.fetchall();assert len(records)==1;old=dict(zip([d[0] for d in query.description],records[0]))
        assert new['temperature_K']==old['temperature_K']
        for field in ('solute_surface_sha256','solvent_surface_sha256'):assert new[field]==old[field]
        verdict_fields=['above_15_mol_percent','above_15_wt_percent']
        row=dict(system=system,status_match=new['status']==old['status'],new_status=new['status'],released_status=old['status'],
            validation_match=new['value_validated']==old['value_validated'],
            verdicts_match=all(new.get(f)==old[f] for f in verdict_fields))
        differences={}
        if new['value_validated']:
            for field in ('solute_mole_fraction_solubility','solute_wt_percent_solubility'):
                assert math.isfinite(new[field]) and math.isfinite(old[field])
                differences[field]=abs(new[field]-old[field])
            previous_ties=json.loads(old['tie_lines_json']);new_ties=new['tie_lines'];assert len(previous_ties)==len(new_ties)
            for index,(a,b) in enumerate(zip(new_ties,previous_ties)):
                for field in ('x_solvent_rich','x_solute_rich'):
                    differences[f'tie_{index}_{field}']=abs(a[field]-b[field])
        else:
            assert all(new.get(f) is None and old[f] is None for f in verdict_fields)
        row['differences']=differences
        row['passed']=row['status_match'] and row['validation_match'] and row['verdicts_match'] and all(v<=1e-9 for v in differences.values())
        comparisons.append(row)
    assert len(comparisons)==complete['systems']
    passed=all(r['passed'] for r in comparisons)
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='passed_local_lle_reproduction' if passed else 'failed_local_lle_reproduction',
        systems=len(comparisons),passed=sum(r['passed'] for r in comparisons),comparisons=comparisons,
        cpu_model=complete['execution']['cpu_model'],peak_rss_kib=complete['peak_rss_kib'],wall_seconds=complete['wall_seconds'],
        source_sha256=pins,scope='Eight system control check with unchanged A-9 solver; not full calibration-set reproduction or experimental validation.')
    out=args.output.resolve();assert out.is_relative_to(B/'supplement-v1') and not out.exists();out.mkdir(parents=True)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (out/'manifest.json').write_text(json.dumps(dict(status=summary['status'],files={p.name:sha(p) for p in out.iterdir() if p.is_file()}),indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('source_sha256','comparisons')}));assert passed

if __name__=='__main__':main()
