"""Independent arithmetic and provenance audit of A-9 collected production data.

No execution worker, COSMO engine or solver is imported. Partition ensembles are
reconstructed from the archived exact-zero activities. LLE checks verify stored
qualification evidence, units and verdict arithmetic, not experimental accuracy
or a second solution of COSMOspace. Partial coverage is never called complete.
"""
import collections, datetime, gzip, hashlib, json, math, resource, sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-v1')
BASE = D.parent/'phase8-v1'
ROOTS = {'chunk-probe-results-v1': 'chunk-probe-plan.json', 'production-results-v1': 'production-plan.json'}


def check_activity_failure(value):
    # Independent of the execution handler: a failed activity grid is not a
    # completed two-grid LLE solve and must carry no inferred numerical result.
    assert value['status']=='activity_nonconvergence'
    assert value['failure_mode']=='cosmospace_binary_grid_nonconvergence'
    assert value['exception_type']=='ValueError'
    assert value['exception_message']=='COSMOspace did not converge for binary grid'
    assert 'ValueError: '+value['exception_message'] in value['traceback']
    assert value['failure_policy_sha256']==sha(R/'scripts/phase9_failure_policy.py')
    assert value['grid_checks']==[] and value['tie_lines']==[]
    assert not value.get('activities')
    for field in ['solute_mole_fraction_solubility','solute_wt_percent_solubility',
                  'above_15_mol_percent','above_15_wt_percent']:
        assert value[field] is None,field
    policy=value['recovery_policy']
    if policy['entry_sha256']==sha(R/'scripts/phase9_retry_entry.py'):
        pass
    else:
        assert policy['entry_sha256']==sha(R/'scripts/phase9_tail_helper.py')
        assignment_path=D/'tail-0002-v1/assignment.json'
        assert policy['assignment_sha256']==sha(assignment_path)
        assignment=json.loads(assignment_path.read_text())
        assert value['unit']!=assignment['retained_unit']
        assert sum(value['unit'] in group for group in assignment['groups'])==1
    assert policy['failure_policy_sha256']==value['failure_policy_sha256']


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()


def logsum(values):
    top=max(values)
    return top+math.log(math.fsum(math.exp(v-top) for v in values))


def records(registry):
    # Older snapshots used one gzip per record; later ones are sorted streams.
    # Phase activities precede dependent partition rows in both formats.
    for p in sorted((D/'collected').glob('*.json.gz')):
        if not p.name.startswith(tuple(root+'__' for root in ROOTS)):continue
        with gzip.open(p,'rt') as f:yield '/'.join(p.name.removesuffix('.gz').split('__',3)),json.load(f)
    for bundle in registry.get('compact_bundles',[]):
        p=Path(bundle['path']);assert sha(p)==bundle['sha256'],p
        with gzip.open(p,'rt') as f:
            for line in f:
                row=json.loads(line)
                if row['path'].split('/')[0] in ROOTS:yield row['path'],row['value']


def main(require_complete=False, registry=None):
    manifest=json.loads((BASE/'manifest.json').read_text())
    validation=json.loads((BASE/'validation-inputs.json').read_text())
    cohort=json.loads((D.parent/'phase83-v1/cohort.json').read_text())
    # Exporters pass the exact immutable registry they will consume. New return
    # archives may arrive while that snapshot is audited; they belong to a later
    # snapshot and must neither invalidate nor silently expand this one.
    if registry is None:registry=json.loads((D/'collection.json').read_text())
    assert len(cohort['rows'])==5830
    plans={root:json.loads((D/name).read_text()) for root,name in ROOTS.items() if (D/name).exists()}
    common_pins={key:sha(path) for key,path in {
        'worker_sha256':R/'scripts/phase9_worker.py', 'profiles_sha256':R/'scripts/phase9_profiles.py',
        'grid_sha256':R/'scripts/phase9_grid.py','solver_sha256':BASE/'phase8_lle.py',
        'package_pins_sha256':BASE/'package-pins.json'}.items()}
    chunks={}
    for root,plan in plans.items():
        if plan.get('worker_module')=='phase9_worker_cpu':
            proof=json.loads((D/'genoa-comparison.json').read_text())
            assert proof['status']=='passed' and proof['worker_cpu_sha256']==sha(R/'scripts/phase9_worker_cpu.py')
        assert plan['cohort_sha256']==sha(D.parent/'phase83-v1/cohort.json')
        assert plan['manifest_sha256']==sha(BASE/'manifest.json')
        assert plan['validation_sha256']==sha(BASE/'validation-inputs.json')
        for i,units in enumerate(plan['chunks']):
            key=f'{root}/{i:04d}'
            chunks[key]=dict(units=units,positions={u['id']:j for j,u in enumerate(units)},
                             signature=dict(common_pins,worker_sha256=sha(R/'scripts'/(plan.get('worker_module','phase9_worker')+'.py')),plan_sha256=sha(D/ROOTS[root])),
                             allow_genoa=plan.get('worker_module')=='phase9_worker_cpu')
            for u in units:
                c=cohort['rows'][u['reference_index']]
                assert u['id']==f"cohort-{c['index']:05d}"
                assert u['inchikey']==c['inchikey'] and u['surface_sha256']==c['surface_sha256']
    solvents={s['name']:s for s in manifest['solvents']}
    phase_pins={'solvent-'+s['name']:sha(BASE/s['B']) for s in solvents.values()}
    phase_pins.update({'polymer-'+r['entry_id']:sha(BASE/r['B']) for rs in manifest['polymers'].values() for r in rs})
    temperatures={(r['solvent'],r['regime']):r['temperature_K'] for r in validation['lle_units']}
    assert len(temperatures)==64
    positions={key:i for i,key in enumerate(sorted(temperatures))}
    partition_keys={(p,s,c) for p in manifest['polymers'] for s in solvents for c in ['normalized','existing']}
    activities=collections.defaultdict(dict);partition_done=set();lle_masks=collections.defaultdict(int)
    completed={};seen_paths=set();counts=collections.Counter();errors=collections.defaultdict(float);statuses=collections.Counter()
    def signature(actual,expected):
        assert all(actual.get(k)==v for k,v in expected.items()),(actual,expected)
    def numeric(field,actual,expected,tolerance=1e-9):
        assert math.isfinite(float(actual)) and math.isfinite(float(expected))
        delta=abs(float(actual)-float(expected));errors[field]=max(errors[field],delta)
        assert delta<tolerance,(field,actual,expected,delta)
    for path,value in records(registry):
        assert path not in seen_paths,path;seen_paths.add(path)
        parts=path.split('/');chunk='/'.join(parts[:2]);spec=chunks[chunk]
        assert path in registry['files'],path
        if len(parts)>3 and parts[2]=='activities':
            phase=value['phase'];assert phase not in activities[chunk]
            assert value['phase_sha256']==phase_pins[phase]
            assert value['solute_x']==0 and value['reference_state']=='pure_component'
            assert len(value['values'])==len(spec['units']) and all(math.isfinite(x) for x in value['values'])
            assert ('EPYC 7763' in value['execution']['cpu_model'] or (spec['allow_genoa'] and 'EPYC 9' in value['execution']['cpu_model']))
            activities[chunk][phase]=value['values'];counts['phase_activity_checkpoints']+=1
        elif len(parts)>3 and parts[2]=='partition':
            assert len(value)==640
            unit=value[0]['unit'];assert unit not in partition_done
            idx=spec['positions'][unit];u=spec['units'][idx]
            raw=activities[chunk];assert set(raw)==set(phase_pins),(chunk,len(raw))
            gamma={};volumes={}
            for polymer,rs in manifest['polymers'].items():
                minimum=min(r['B_energy_hartree'] for r in rs)
                lng=[raw['polymer-'+r['entry_id']][idx] for r in rs]
                for convention,factor in [('normalized',2625.4996394799/(.00831446261815324*298.15)),('existing',627.5094740631/(.0019872041*298.15))]:
                    exponents=[-(r['B_energy_hartree']-minimum)*factor for r in rs];z=logsum(exponents)
                    weights=[math.exp(e-z) for e in exponents];numeric('weight_sum',sum(weights),1,1e-12)
                    gamma[polymer,convention]=-logsum([e-g for e,g in zip(exponents,lng)])+(z if convention=='normalized' else 0)
                    volumes[polymer,convention]=math.fsum(w*r['B_cavity_cm3_mol'] for w,r in zip(weights,rs))
            keys=set()
            for row in value:
                assert row['unit']==unit and row['inchikey']==u['inchikey'] and row['status']=='predicted'
                assert row['solute_surface_sha256']==u['surface_sha256']
                assert row['solvent_surface_sha256']==phase_pins['solvent-'+row['solvent']]
                assert row['temperature_K']==298.15
                k=(row['polymer'],row['solvent'],row['convention']);assert k not in keys;keys.add(k)
                pc=(row['polymer'],row['convention']);gp=gamma[pc];gs=raw['solvent-'+row['solvent']][idx];vp=volumes[pc]
                vs=solvents[row['solvent']]['B_volume' if row['convention']=='normalized' else 'B_legacy_volume']
                lp=(gp-gs)/math.log(10);lc=lp+math.log10(vp/vs)
                for field,expected in [('ln_gamma_polymer',gp),('ln_gamma_solvent',gs),('polymer_volume_cm3_mol',vp),('solvent_volume_cm3_mol',vs),('logP_x',lp),('logP_concentration',lc)]:numeric(field,row[field],expected)
            assert keys==partition_keys
            partition_done.add(unit);counts['partition_rows_reconstructed']+=640
            # Keep bounded memory even when many chunks are collected together.
            if all(u['id'] in partition_done for u in spec['units']):del activities[chunk]
        elif len(parts)>3 and parts[2]=='lle':
            unit=value['unit'];u=spec['units'][spec['positions'][unit]];key=(value['solvent'],value['regime'])
            assert value['inchikey']==u['inchikey'] and value['temperature_K']==temperatures[key]
            expected=dict(spec['signature'],unit=unit,solute_sha256=u['surface_sha256'],solvent_sha256=phase_pins['solvent-'+value['solvent']],temperature_K=temperatures[key])
            signature(value['signature'],expected);assert ('EPYC 7763' in value['execution']['cpu_model'] or (spec['allow_genoa'] and 'EPYC 9' in value['execution']['cpu_model']))
            bit=1<<positions[key];assert not lle_masks[unit]&bit;lle_masks[unit]|=bit
            status=value['status'];statuses[status]+=1
            grids=value['grid_checks']
            if status=='activity_nonconvergence':check_activity_failure(value)
            else:
                assert status in ['single_liquid_phase','two_liquid_phases','grid_or_tie_line_unresolved','grid_not_converged']
                assert [r['grid_intervals'] for r in grids]==[1000,2000]
            valid=status in ['single_liquid_phase','two_liquid_phases']
            if valid:
                assert all(r['status']==status for r in grids)
                mw=u['molecular_weight_g_mol'];ms=validation['solvent_identities'][value['solvent']]['molecular_weight_g_mol']
                for r in [*grids,value]:
                    x=r['solute_mole_fraction_solubility'];assert 0<=x<=1
                    numeric('LLE_weight_conversion',r['solute_wt_percent_solubility'],100*x*mw/(x*mw+(1-x)*ms))
                    ties=r['tie_lines']
                    if status=='single_liquid_phase':assert not ties and x==1
                    else:
                        assert ties and x==ties[0]['x_solvent_rich']
                        for t in ties:
                            assert 0<t['x_solvent_rich']<t['x_solute_rich']<1
                            assert t['chemical_potential_residual']<=1e-7 and t['minimum_tangent_distance_RT']>=-1e-7
                dx=100*abs(grids[0]['solute_mole_fraction_solubility']-grids[1]['solute_mole_fraction_solubility'])
                dw=abs(grids[0]['solute_wt_percent_solubility']-grids[1]['solute_wt_percent_solubility'])
                assert max(dx,dw)<=.01
                numeric('LLE_grid_mol_delta',value['grid_change_mol_percentage_points'],dx)
                numeric('LLE_grid_wt_delta',value['grid_change_wt_percentage_points'],dw)
                for basis,v in [('mol',100*value['solute_mole_fraction_solubility']),('wt',value['solute_wt_percent_solubility'])]:
                    expected=None if abs(v-15)<=.01 else v>15
                    assert value['above_15_'+basis+'_percent'] is expected
            else:
                assert value.get('above_15_mol_percent') is None and value.get('above_15_wt_percent') is None
            counts['LLE_systems_checked']+=1
        elif path.endswith('/complete.json'):
            signature(value['signature'],spec['signature']);assert chunk not in completed
            assert value['unit_ids']==[u['id'] for u in spec['units']]
            assert ('EPYC 7763' in value['execution']['cpu_model'] or (spec['allow_genoa'] and 'EPYC 9' in value['execution']['cpu_model']))
            assert len(value['lle_statuses'])==64*len(spec['units'])
            completed[chunk]=value
    fully={u for u in partition_done if lle_masks[u]==(1<<64)-1}
    status='complete' if len(fully)==5830 and len(completed)==len(chunks) else 'passed_for_collected_subset'
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status=status,denominator=5830,
                partition_molecules=len(partition_done),fully_evaluated_molecules=len(fully),complete_chunks=len(completed),
                counts=dict(counts),LLE_statuses=dict(statuses),maximum_arithmetic_errors=dict(errors),
                auditor_sha256=sha(Path(__file__)),cohort_sha256=sha(D.parent/'phase83-v1/cohort.json'),
                collection_snapshot_sha256=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest(),
                peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                scope='Independent reconstruction of both x=0 ensemble conventions and volume/sign arithmetic from archived activities; surface/code/plan/CPU linkage, unique keys, LLE qualification evidence and both threshold verdicts. Does not re-solve COSMOspace or establish experimental accuracy.')
    tmp=D/'results-audit.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(D/'results-audit.json')
    print(json.dumps(result,indent=2))
    if require_complete:assert status=='complete','All 5830 completed units required for release'
    return result


if __name__=='__main__':main('--require-complete' in sys.argv)
