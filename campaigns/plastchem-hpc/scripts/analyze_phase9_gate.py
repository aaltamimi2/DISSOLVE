"""Compare A-9 returned, digest-verified units with the full frozen references.

This report never submits or cancels. Incomplete evidence cannot pass the gate.
"""
import csv,datetime,gzip,hashlib,json,math,statistics
from pathlib import Path

D=Path('/mnt/r/plastchem-euler/phase9-v1');OLD=D.parent/'phase83-v1';BASE=D.parent/'phase8-v1'


def load_gz(p):
    with gzip.open(p,'rt') as f:return json.load(f)


def collected_records(root_prefix='gate-results-v1',include_activities=False,chunk_id=None):
    # Older gate snapshots used separate compact files. New collections use one
    # compressed JSONL stream per verified return archive, committed by ledger.
    prefix=root_prefix+'__'+(chunk_id+'__' if chunk_id is not None else '')
    wanted=root_prefix+'/'+(chunk_id+'/' if chunk_id is not None else '')
    for p in (D/'collected').glob(prefix+'*.gz'):
        if not (include_activities or '__partition__' in p.name or '__lle__' in p.name or p.name.endswith('__complete.json.gz')):continue
        # Only the first three separators encode directories. Later double
        # underscores belong to conformer and LLE filenames and must survive.
        yield '/'.join(p.name.removesuffix('.gz').split('__',3)),load_gz(p)
    registry=D/'collection.json'
    if registry.exists():
        for bundle in json.loads(registry.read_text()).get('compact_bundles',[]):
            assert hashlib.sha256(Path(bundle['path']).read_bytes()).hexdigest()==bundle['sha256']
            with gzip.open(bundle['path'],'rt') as f:
                for line in f:
                    row=json.loads(line)
                    if row['path'].startswith(wanted):yield row['path'],row['value']


def main():
    plan=json.loads((D/'gate-plan.json').read_text());cohort=json.loads((OLD/'cohort.json').read_text());units=sum(plan['chunks'],[])
    expected_partition={};expected_lle={}
    for u in units:
        if u['reference']=='calibration':
            ref=load_gz(OLD/'collected'/f"{u['reference_index']:05d}.json.gz")
            parts=ref['partition'];lles=ref['lle'].values()
        else:
            parts=list(csv.DictReader((BASE/'phase82/partition'/u['reference_name']/'predictions.csv').open()))
            all_lle=json.loads((BASE/'phase82/lle-results.json').read_text())
            lles=[r for r in all_lle.values() if r['solute']==u['reference_name']]
        for r in parts:expected_partition[u['id'],r['polymer'],r['solvent'],r['convention']]=r
        for r in lles:expected_lle[u['id'],r['solvent'],r['regime']]=r
    assert len(expected_partition)==17792,len(expected_partition)
    assert len(expected_lle)==2240,len(expected_lle)
    actual_partition={};actual_lle={};complete=[]
    for name,value in collected_records():
        if '/partition/' in name:
            for r in value:
                key=(r['unit'],r['polymer'],r['solvent'],r['convention'])
                assert key not in actual_partition;actual_partition[key]=r
        elif '/lle/' in name:
            r=value;key=(r['unit'],r['solvent'],r['regime']);assert key not in actual_lle;actual_lle[key]=r
        elif name.endswith('/complete.json'):complete.append(value)
    authority_path=D/'finite-dilution-clarification.json'
    authority=json.loads(authority_path.read_text()) if authority_path.exists() else None
    permitted={tuple(k) for k in authority['keys']} if authority else set()
    if authority:assert len(permitted)==59
    errors=[];partition_differences=[];lle_comparisons=[];documented=[]
    for key,old in expected_partition.items():
        if key not in actual_partition:continue
        new=actual_partition[key]
        assert new['solute_surface_sha256']==old['solute_surface_sha256']
        assert new['solvent_surface_sha256']==old['solvent_surface_sha256']
        if old['status']!='predicted' or new['status']!='predicted':errors.append(dict(key=key,kind='partition_status'));continue
        dx=float(new['logP_x'])-float(old['logP_x']);dc=float(new['logP_concentration'])-float(old['logP_concentration'])
        r=dict(unit=key[0],polymer=key[1],solvent=key[2],convention=key[3],old_logP_x=float(old['logP_x']),new_logP_x=float(new['logP_x']),old_logP_concentration=float(old['logP_concentration']),new_logP_concentration=float(new['logP_concentration']),difference_logP_x=dx,difference_logP_concentration=dc,comparison_status='numerical_reproduction_pass')
        if max(abs(dx),abs(dc))>.005:
            if key in permitted and max(abs(dx),abs(dc))<=.012:
                r['comparison_status']='documented_finite_dilution_reference_difference'
                documented.append(r)
            else:
                r['comparison_status']='reproduction_failure'
                errors.append(dict(key=key,kind='partition_tolerance',differences=[dx,dc]))
        partition_differences.append(r)
    for key,old in expected_lle.items():
        if key not in actual_lle:continue
        new=actual_lle[key]
        for field in ['temperature_K']:
            assert float(new[field])==float(old[field]),(key,field)
        r=dict(unit=key[0],solvent=key[1],regime=key[2],old_status=old['status'],new_status=new['status'])
        match=new['status']==old['status']
        for field in ['above_15_mol_percent','above_15_wt_percent']:
            r['old_'+field]=old.get(field);r['new_'+field]=new.get(field)
            match=match and old.get(field)==new.get(field)
        if old['status'] in ['single_liquid_phase','two_liquid_phases'] and new['status'] in ['single_liquid_phase','two_liquid_phases']:
            r['difference_mol_percentage_points']=100*(new['solute_mole_fraction_solubility']-old['solute_mole_fraction_solubility'])
            r['difference_wt_percentage_points']=new['solute_wt_percent_solubility']-old['solute_wt_percent_solubility']
            if max(abs(r['difference_mol_percentage_points']),abs(r['difference_wt_percentage_points']))>.01:
                errors.append(dict(key=key,kind='LLE_raw_difference_above_grid_tolerance'))
        r['verdicts_match']=match;lle_comparisons.append(r)
        if not match:errors.append(dict(key=key,kind='LLE_status_or_verdict_mismatch',old=old['status'],new=new['status']))
    def write_csv(p,rows):
        with p.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)) or ['status']);w.writeheader();w.writerows(rows)
    write_csv(D/'gate-partition-differences.csv',partition_differences);write_csv(D/'gate-lle-comparisons.csv',lle_comparisons)
    write_csv(D/'documented-finite-dilution-differences.csv',documented)
    all_present=len(partition_differences)==len(expected_partition) and len(lle_comparisons)==len(expected_lle) and len(complete)==len(plan['chunks'])
    out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='failed' if errors else ('reproduction_passed_cost_pending' if all_present else 'incomplete'),
             partition_compared=len(partition_differences),partition_denominator=len(expected_partition),lle_compared=len(lle_comparisons),lle_denominator=len(expected_lle),
             complete_chunks=len(complete),chunk_denominator=len(plan['chunks']),
             partition_numerical_passes=sum(r['comparison_status']=='numerical_reproduction_pass' for r in partition_differences),documented_finite_dilution_reference_differences=len(documented),partition_clarification_authority={k:v for k,v in authority.items() if k!='keys'} if authority else None,
             maximum_logP_error=max([max(abs(r['difference_logP_x']),abs(r['difference_logP_concentration'])) for r in partition_differences],default=None),errors=errors,
             completed_peak_rss_kib=max([c['peak_rss_kib'] for c in complete],default=None))
    if all_present:
        # LLE varies strongly by contaminant: use the original nine strata.
        # Non-LLE overhead is reported separately and conservatively scaled
        # at the validation chunk size until a full-sized chunk is measured.
        unit_lle={u['id']:sum(actual_lle[u['id'],s['name'],regime]['wall_seconds'] for s in json.loads((BASE/'manifest.json').read_text())['solvents'] for regime in ['RT','high']) for u in units}
        strata=[];projected_lle_seconds=0
        for name,s in cohort['strata'].items():
            costs=[unit_lle[f'cal-{i:05d}'] for i in s['selected_indices']]
            projected_lle_seconds+=s['population']*statistics.mean(costs)
            strata.append(dict(stratum=name,population=s['population'],sample_seconds=costs,mean_seconds=statistics.mean(costs)))
        summed_wall=sum(c['wall_seconds'] for c in complete);summed_lle=sum(unit_lle.values())
        overhead=max(0,summed_wall-summed_lle)/len(units)
        conservative_hours=(projected_lle_seconds+overhead*5830)/3600
        out['cost_screen']=dict(strata=strata,projected_lle_cpu_hours=projected_lle_seconds/3600,validation_chunk_overhead_seconds_per_solute=overhead,
                               conservative_cpu_hours_at_validation_chunk_size=conservative_hours,planning_cpu_hours_with_25_percent_allowance=1.25*conservative_hours,
                               limit_cpu_hours=500,full_sized_chunk_measurement_pending=True,
                               note='Engineering extrapolation, not a random-sample confidence interval. No final production clearance from this preliminary cost screen alone.')
    (D/'gate-comparison.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))


if __name__=='__main__':main()
