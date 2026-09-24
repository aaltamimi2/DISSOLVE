"""A-9 measured 100-unit chunk cost; independent of the correctness decision."""
import collections,datetime,json,statistics
from pathlib import Path
from analyze_phase9_gate import collected_records

D=Path('/mnt/r/plastchem-euler/phase9-v1')


def main():
    plan=json.loads((D/'chunk-probe-plan.json').read_text());units=plan['chunks'][0]
    cohort=json.loads((D.parent/'phase83-v1/cohort.json').read_text())
    lle={};complete=None;partition=0;calibration_partition={}
    selected={f'cohort-{i:05d}' for s in cohort['strata'].values() for i in s['selected_indices']}
    for path,value in collected_records('chunk-probe-results-v1'):
        if '/lle/' in path:
            key=(value['unit'],value['solvent'],value['regime']);assert key not in lle;lle[key]=value
        elif path.endswith('/complete.json'):
            assert complete is None;complete=value
        elif '/partition/' in path:
            partition+=1
            for r in value:
                if r['unit'] in selected:
                    key=(r['unit'],r['polymer'],r['solvent'],r['convention'])
                    assert key not in calibration_partition
                    calibration_partition[key]=r
    out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='incomplete',lle_systems=len(lle),lle_denominator=64*len(units),partition_units=partition,chunk_denominator=len(units))
    assert len(lle)<=64*len(units) and partition<=len(units), 'Collected coverage exceeds the chunk'
    out['completion_record_collected']=complete is not None
    if complete is not None:
        # The collector may see a completion footer after its directory walk
        # passed the final LLE files. Wait for those payloads; never cost a prefix.
        assert complete['unit_ids']==[u['id'] for u in units]
        assert len(complete['lle_statuses'])==64*len(units)
        out['remaining_LLE_transfers']=64*len(units)-len(lle)
        out['remaining_partition_transfers']=len(units)-partition
    if complete is not None and len(lle)==64*len(units) and partition==len(units):
        assert complete['unit_ids']==[u['id'] for u in units]
        # Reconfirm the production-sized ten-solute solve against the completed
        # smaller gate chunks. Both are x=0; no historical finite-x exception.
        checked_partition=0;checked_lle=0;maximum_difference=0;comparison_errors=[]
        for path,value in collected_records('gate-results-v1'):
            if '/partition/' in path:
                for old in value:
                    if not old['unit'].startswith('cal-'):continue
                    unit='cohort-'+old['unit'][4:]
                    new=calibration_partition[unit,old['polymer'],old['solvent'],old['convention']]
                    delta=max(abs(new[k]-old[k]) for k in ['logP_x','logP_concentration'])
                    maximum_difference=max(maximum_difference,delta);checked_partition+=1
                    if delta>.005:comparison_errors.append(dict(unit=unit,kind='chunk_partition_tolerance',difference=delta))
            elif '/lle/' in path and value['unit'].startswith('cal-'):
                unit='cohort-'+value['unit'][4:];new=lle[unit,value['solvent'],value['regime']]
                checked_lle+=1
                if any(new.get(k)!=value.get(k) for k in ['status','above_15_mol_percent','above_15_wt_percent']):
                    comparison_errors.append(dict(unit=unit,solvent=value['solvent'],regime=value['regime'],kind='chunk_LLE_verdict'))
        assert checked_partition==17280 and checked_lle==1728
        out['production_sized_calibration_comparison']=dict(partition_rows=checked_partition,lle_systems=checked_lle,maximum_logP_difference=maximum_difference,errors=comparison_errors,status='passed' if not comparison_errors else 'failed')
        unit_seconds={u['id']:sum(r['wall_seconds'] for key,r in lle.items() if key[0]==u['id']) for u in units}
        strata=[];projected_lle_seconds=0;low=0;high=0
        for name,s in cohort['strata'].items():
            values=[unit_seconds[f'cohort-{idx:05d}'] for idx in s['selected_indices']]
            projected_lle_seconds+=s['population']*statistics.mean(values)
            low+=s['population']*min(values);high+=s['population']*max(values)
            strata.append(dict(stratum=name,population=s['population'],calibration_seconds=values))
        # Preserve all measured chunk overhead, including checkpoint writes.
        overhead=max(0,complete['wall_seconds']-sum(unit_seconds.values()))/len(units)
        seconds=projected_lle_seconds+overhead*5830
        accounting=json.loads((D/'latest-gate-status.json').read_text()).get('accounting','')
        allocated=0
        for line in accounting.splitlines():
            parts=line.split('|');job=parts[0]
            if '.' in job:continue
            # Normal jobs and explicit array task rows; exclude array summaries.
            if not job.isdigit() and not ('_' in job and job.split('_')[-1].isdigit()):continue
            if len(parts)>=4:allocated+=int(parts[2] or 0)*int(parts[3] or 0)
        out.update(status='complete_cost_measurement_not_production_clearance',measured_chunk_wall_seconds=complete['wall_seconds'],peak_rss_kib=complete['peak_rss_kib'],parse_seconds=complete['parse_seconds'],partition_seconds=complete['partition_seconds'],lle_seconds=complete['lle_seconds'],non_LLE_overhead_seconds_per_solute=overhead,
                   population_weighted_cpu_hours=seconds/3600,planning_cpu_hours_with_25_percent_allowance=seconds/3600*1.25,
                   engineering_range_cpu_hours=[(low+overhead*5830)/3600,(high+overhead*5830)/3600],
                   engineering_range_note='Within-stratum extrema from three deliberate calibration molecules; not a confidence interval.',
                   diagnostic_allocated_cpu_hours_to_snapshot=allocated/3600,
                   planning_cpu_hours_including_diagnostics=seconds/3600*1.25+allocated/3600,
                   limit_cpu_hours=500,cost_gate_passes=seconds/3600*1.25+allocated/3600<=500,
                   statuses=dict(collections.Counter(r['status'] for r in lle.values())),strata=strata)
    (D/'chunk-cost.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))


if __name__=='__main__':main()
