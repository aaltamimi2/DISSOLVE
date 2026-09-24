"""Independent A-10 checkpoint checks against the verified original release.

Streams bounded compact archives; never imports the executing scientific worker.
Checks numerical arithmetic and saved LLE evidence, not experimental accuracy.
"""
import collections
import datetime
import gzip
import hashlib
import json
import math
import resource
from pathlib import Path

from audit_phase9_results import sha
from phase10_lle_audit import check as check_lle, near

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')
ROOTS={'calibration-results-v2':'calibration-plan-v2.json','production-results-v1':'production-plan.json'}
VOLUME_PIN='839d30e199fc0f1504506d1a44923d918dcafab8242a3530297f15db641d71fe'


def records(registry):
    for bundle in registry.get('compact_bundles',[]):
        path=Path(bundle['path']);assert path.parent==D/'collected'
        assert sha(path)==bundle['sha256'],path
        with gzip.open(path,'rt') as stream:
            for line in stream:
                row=json.loads(line)
                assert row['path'].split('/')[0] in ROOTS
                yield row['path'],row['value']


def context():
    primary=D.parent/'promotion-v1'
    verification=json.loads((D.parent/'phase9-v1/delivery-verification.json').read_text())
    assert verification['status']=='complete_delivery_verified'
    assert verification['manifest_sha256']==sha(primary/'manifest.json')
    receipt=json.loads((D/'primary-polymer-reference-receipt.json').read_text())
    assert receipt['status']=='complete_original_coefficients_verified'
    assert receipt['primary_release_manifest_sha256']==verification['manifest_sha256']
    assert receipt['reference_sha256']==sha(D/'primary-polymer-reference.json.gz')
    assert receipt['extractor_sha256']==sha(R/'scripts/build_phase10_primary_reference.py')
    with gzip.open(D/'primary-polymer-reference.json.gz','rt') as f:
        reference={r['unit']:r for r in map(json.loads,f)}
    assert len(reference)==5830
    manifest=json.loads((D/'manifest.json').read_text())
    cohort=json.loads((D.parent/'phase83-v1/cohort.json').read_text())['rows']
    assert manifest['cohort_sha256']==sha(D.parent/'phase83-v1/cohort.json')
    base=D.parent/'phase8-v1';assert manifest['primary_manifest_sha256']==sha(base/'manifest.json')
    original=json.loads((base/'manifest.json').read_text())
    assert set(manifest['polymers'])==set(original['polymers']) and len(manifest['polymers'])==10
    solvents={s['name']:s for s in manifest['solvents']}
    assert len(solvents)==39 and not set(solvents)&{s['name'] for s in original['solvents']}
    for name,s in solvents.items():
        assert name==name.lower() and sha(D/s['surface'])==s['surface_sha256']
        assert s['physical_volume'] is None
    vp=D/'qualified-physical-volumes-v4.json';assert sha(vp)==VOLUME_PIN
    volumes=json.loads(vp.read_text());assert volumes['qualified']==39 and not volumes['missing']
    volumes={r['solvent']:r for r in volumes['entries']};assert set(volumes)==set(solvents)
    for name,v in volumes.items():
        s=solvents[name];assert v['input_inchikey']==s['inchikey'] and v['temperature_K']==298.15
        assert v['density_g_cm3']>0 and v['source_url'].startswith('https://')
        near(v['molar_volume_cm3_mol'],s['molecular_weight_g_mol']/v['density_g_cm3'],1e-12)
    phase_pins={s['name']:s['surface_sha256'] for s in solvents.values()}
    phase_pins.update({'CONTROL__'+s['name']:sha(base/s['B']) for s in original['solvents'] if s['name'] in manifest['controls']})
    units={f"cohort-{c['index']:05d}":c for c in cohort};assert set(reference)==set(units)
    chunks={};owned=set()
    for root,name in ROOTS.items():
        plan=json.loads((D/name).read_text());assert plan['manifest_sha256']==sha(D/'manifest.json')
        pins=json.loads((D/plan['worker_pins_file']).read_text())
        for filename,pin in pins.items():assert sha(R/'scripts'/filename)==pin
        for i,us in enumerate(plan['chunks']):
            for u in us:
                c=units[u['id']];ref=reference[u['id']]
                assert u['id'] not in owned;owned.add(u['id'])
                assert u['inchikey']==c['inchikey']==ref['inchikey']
                assert u['surface_sha256']==c['surface_sha256']==ref['surface_sha256']
                assert u['molecular_weight_g_mol']==c['molecular_weight_g_mol']
                assert u['primary_partition']=='../phase9-v1/'+ref['primary_path']
            chunks[f'{root}/{i:04d}']=dict(units=us,positions={u['id']:i for i,u in enumerate(us)},
                signature=dict(plan_sha256=sha(D/name),worker_pins_sha256=sha(D/plan['worker_pins_file']),
                    manifest_sha256=sha(D/'manifest.json'),package_pins_sha256=sha(base/'package-pins.json')))
    assert owned==set(units) and len(chunks)==67
    tails={}
    for ap in sorted(D.glob('tail-*-v1/assignment.json')):
        a=json.loads(ap.read_text());digest=sha(ap)
        expected=chunks[f"production-results-v1/{a['chunk']:04d}"]
        assert a['signature']==expected['signature']
        assert len(a['groups'])==3 and all(a['groups'])
        assigned=[u for group in a['groups'] for u in group]
        assert len(assigned)==len(set(assigned)) and a['retained_unit'] not in assigned
        assert set(assigned)<=set(expected['positions'])
        for name,pin in a['code_pins'].items():assert sha(R/'scripts'/name)==pin,name
        rp=D/f"production-retry-tail{a['chunk']:04d}-submission.json"
        receipt=json.loads(rp.read_text());assert receipt['assignment_sha256']==digest
        assert receipt['purpose']=='extension_recovery'
        tails[digest]=dict(assignment=a,receipt=receipt)
    return dict(reference=reference,manifest=manifest,solvents=solvents,volumes=volumes,
                phase_pins=phase_pins,units=units,chunks=chunks,tails=tails,primary_manifest_sha256=verification['manifest_sha256'])


def cpu(value):
    model=value['execution']['cpu_model']
    assert 'EPYC 7763' in model or 'EPYC 9' in model
    assert value['execution']['job_id'] and value['execution']['node']


def check_tail_evidence(value,ctx):
    if 'tail_helper' not in value:return
    e=value['tail_helper'];record=ctx['tails'][e['assignment_sha256']]
    a=record['assignment'];group=e['group'];assert type(group) is int and 0<=group<3
    assert value['unit'] in a['groups'][group] and value['unit']!=a['retained_unit']
    assert e['helper_sha256']==a['code_pins']['phase10_tail_helper.py']
    assert value['execution']['array_job_id']==record['receipt']['job_id']
    assert value['execution']['array_task_id']==str(group)
    assert all(value['signature'][k]==v for k,v in a['signature'].items())


def check_partition(rows,u,reference,activities,index,ctx):
    assert len(rows)==780
    coefficients={(r['polymer'],r['convention']):r for r in reference['coefficients']}
    expected={(p,c,s) for p,c in coefficients for s in ctx['solvents']};seen=set()
    for r in rows:
        key=(r['polymer'],r['convention'],r['solvent']);assert key not in seen;seen.add(key)
        assert r['unit']==u['id'] and r['inchikey']==u['inchikey']
        assert r['status']=='activity_predicted' and r['temperature_K']==298.15
        assert r['solute_surface_sha256']==u['surface_sha256']
        assert r['solvent_surface_sha256']==ctx['phase_pins'][r['solvent']]
        assert r['primary_partition_sha256']==reference['primary_sha256']
        coefficient=coefficients[key[:2]]
        for field in ['ln_gamma_polymer','polymer_volume_cm3_mol']:
            near(r[field],coefficient[field],1e-12)
        gs=activities[r['solvent']]['values'][index];near(r['ln_gamma_solvent'],gs,1e-12)
        x=(coefficient['ln_gamma_polymer']-gs)/math.log(10);near(r['logP_x'],x,1e-12)
        if r['convention']=='existing':
            vs=ctx['solvents'][r['solvent']]['cavity_volume_cm3_mol']
            assert r['concentration_status']=='predicted'
            near(r['solvent_volume_cm3_mol'],vs,1e-12)
            near(r['logP_concentration'],x+math.log10(coefficient['polymer_volume_cm3_mol']/vs),1e-12)
        else:
            assert r['concentration_status']=='missing_documented_molar_volume'
            assert r['logP_concentration'] is None and r['solvent_volume_cm3_mol'] is None
    assert seen==expected


def main(require_complete=True,registry=None,callback=None):
    ctx=context()
    if registry is None:registry=json.loads((D/'collection.json').read_text())
    activities=collections.defaultdict(dict);reuse=set();partition=set();seen=set();complete={}
    lle=collections.defaultdict(dict);statuses=collections.Counter();counts=collections.Counter()
    maximum_control_error=0.
    for path,value in records(registry):
        assert path not in seen and path in registry['files'],path;seen.add(path)
        parts=path.split('/');chunk='/'.join(parts[:2]);spec=ctx['chunks'][chunk]
        group=parts[2]
        if len(parts)==4:
            assert path+'.sha256.json' in registry['files'],'Missing source seal'
        if group=='polymer-reuse':
            unit=Path(parts[3]).stem;assert unit in spec['positions'] and unit not in reuse
            ref=ctx['reference'][unit]
            assert value['coefficients']==ref['coefficients']
            assert value['control_activities']==ref['control_activities']
            assert value['source_sha256']==ref['primary_sha256']==value['source_seal']['sha256']
            assert value['source'].endswith('/phase9-v1/'+ref['primary_path'])
            u=spec['units'][spec['positions'][unit]]
            assert value['original_plan_sha256']==u['primary_plan_sha256']
            assert value['source_seal']['signature']['plan_sha256']==u['primary_plan_sha256']
            assert value['source_seal']['signature']['unit']==unit
            reuse.add(unit)
        elif group=='activities':
            name=Path(parts[3]).stem;assert name==value['solvent'] and name not in activities[chunk]
            assert value['phase_sha256']==ctx['phase_pins'][name]
            assert value['solute_mole_fraction']==0 and value['temperature_K']==298.15
            assert value['reference_state']=='pure_component' and len(value['values'])==len(spec['units'])
            assert all(math.isfinite(v) for v in value['values']);cpu(value)
            if name.startswith('CONTROL__'):
                for u,v in zip(spec['units'],value['values']):
                    want=ctx['reference'][u['id']]['control_activities'][name.removeprefix('CONTROL__')]
                    near(v,want,1e-9);maximum_control_error=max(maximum_control_error,abs(v-want))
                    counts['primary_exact_zero_controls']+=1
            activities[chunk][name]=value
        elif group=='partition':
            # Archive members are lexically ordered: a partition can precede
            # its polymer-reuse record in the same verified bundle. Its source
            # coefficients are independently available from the primary map;
            # require the reuse record separately at final coverage, not by
            # incidental serialization order.
            unit=Path(parts[3]).stem;assert unit not in partition
            assert set(activities[chunk])==set(ctx['phase_pins'])
            i=spec['positions'][unit];u=spec['units'][i]
            check_partition(value,u,ctx['reference'][unit],activities[chunk],i,ctx)
            partition.add(unit);counts['partition_rows_checked']+=len(value)
        elif group=='lle':
            unit=value['unit'];i=spec['positions'][unit];u=spec['units'][i];s=value['solvent']
            assert parts[3]==unit+'__'+s+'__RT.json'
            assert value['inchikey']==u['inchikey'] and value['regime']=='RT' and value['temperature_K']==298.15
            expected=dict(spec['signature'],unit=unit,solute_sha256=u['surface_sha256'],
                          solvent_sha256=ctx['phase_pins'][s],temperature_K=298.15)
            assert value['signature']==expected;cpu(value);check_tail_evidence(value,ctx)
            assert s not in lle[unit];lle[unit][s]=value['status'];statuses[value['status']]+=1
            check_lle(value,u['molecular_weight_g_mol'],ctx['solvents'][s]['molecular_weight_g_mol'],sha(R/'scripts/phase9_failure_policy.py'))
            counts['LLE_systems_checked']+=1
        elif group=='complete.json':
            assert chunk not in complete and value['signature']==spec['signature']
            assert value['unit_ids']==[u['id'] for u in spec['units']]
            assert set(value['lle_statuses'])=={u['id']+'__'+s+'__RT' for u in spec['units'] for s in ctx['solvents']}
            cpu(value);complete[chunk]=value
        elif group.startswith('CONTROL__') and group.endswith('-comparison.json'):
            assert value['passed'] and value['n']==len(spec['units']) and value['max_abs_ln_gamma']<=1e-9
        else:raise AssertionError(('Unknown record',path))
        if callback:callback(path,value,ctx,activities.get(chunk,{}))
        # Activity arrays are small (41 x at most 100); keep them for streamed
        # callbacks until all partition members of this chunk have appeared.
        if group=='partition' and all(u['id'] in partition for u in spec['units']):
            del activities[chunk]
    fully={u for u in partition if set(lle[u])==set(ctx['solvents'])}
    qualified={u for u in fully if all(s in ['single_liquid_phase','two_liquid_phases'] for s in lle[u].values())}
    for chunk,value in complete.items():
        for u in ctx['chunks'][chunk]['units']:
            for solvent,status in lle[u['id']].items():
                assert value['lle_statuses'][u['id']+'__'+solvent+'__RT']==status
    done=len(fully)==5830 and len(complete)==67 and reuse==set(ctx['units'])
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='complete' if done else 'passed_for_collected_subset',
        denominator=5830,partition_molecules=len(partition),fully_evaluated_molecules=len(fully),fully_qualified_molecules=len(qualified),
        complete_chunks=len(complete),counts=dict(counts),LLE_statuses=dict(statuses),maximum_control_error=maximum_control_error,
        primary_release_manifest_sha256=ctx['primary_manifest_sha256'],volume_references_sha256=VOLUME_PIN,
        collection_snapshot_sha256=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest(),
        auditor_sha256=sha(Path(__file__)),lle_checker_sha256=sha(R/'scripts/phase10_lle_audit.py'),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        scope='Independent original-release coefficient reuse, exact-zero controls, all activity/partition sign and volume arithmetic, identity/plan/CPU linkage, unique coverage and saved LLE qualification/threshold evidence. Does not rerun COSMOspace or establish experimental accuracy.')
    if require_complete:assert done,('Incomplete extension',result)
    (D/'results-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':print(json.dumps(main(),indent=2))
