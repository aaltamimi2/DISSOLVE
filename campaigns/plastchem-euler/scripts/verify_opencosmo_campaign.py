"""Local compatibility verification, segregated from homogeneous-Milan production results."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,hashlib,importlib.util,json,math,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/opencosmo-verification-v1';OUT=ROOT/'reports/opencosmo-verification-v1'
spec=importlib.util.spec_from_file_location('reference_cosmo_logp',P/'reference_cosmo_logp.py');ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a
import numpy as np
SOLVENTS=['water','methanol','dichloromethane','hexane','cyclohexanol']
PAIRS=[('dichloromethane','water'),('cyclohexanol','water'),('hexane','water'),('dichloromethane','methanol')]
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def predict(item):
    start=time.time();result=dict(item)
    try:
        assert digest(item['surface'])==item['surface_sha256']
        lng={};dilute={}
        for solvent in SOLVENTS:
            f=P/'solvents'/(solvent+'.orcacosmo');engine=COSMORS(openCOSMORS24a());engine.add_molecule([item['surface']]);engine.add_molecule([str(f)])
            for x in [1e-5,1e-6]:engine.add_job(x=np.array([x,1-x]),T=298.15,refst='pure_component')
            values=engine.calculate()['tot']['lng']
            lng[solvent]=float(values[0][0]);dilute[solvent]=float(values[1][0]);assert math.isfinite(lng[solvent]) and math.isfinite(dilute[solvent])
        result.update(status='predicted',ln_gamma=lng,ln_gamma_1e6=dilute,pairs=[])
        for a,b in PAIRS:
            kw={'volume_a':ref.MOLAR_VOLUMES_CM3[a],'volume_b':ref.MOLAR_VOLUMES_CM3[b]}
            value=ref.delta_log_d(lng[a],lng[b],**kw);repeat=ref.delta_log_d(dilute[a],dilute[b],**kw)
            row={'solvent':a,'reference':b,'log10_partition_concentration_basis':value,'log10_partition_mole_fraction_basis':ref.delta_log_d(lng[a],lng[b]),'dilution_shift_1e5_to_1e6':repeat-value}
            if item.get('anchor'):
                target=dict(((x,y),v) for x,y,v in getattr(ref,item['anchor']+'_ANCHOR_PAIRS' if item['anchor']!='DEP' else 'ANCHOR_PAIRS'))[(a,b)]
                row.update(existing_table_pair_difference=target,residual_to_existing_table=value-target)
            result['pairs'].append(row)
    except Exception as exc:result.update(status='failed',error=str(exc),traceback=traceback.format_exc())
    result['wall_seconds']=time.time()-start
    (P/'records'/f"{item['label']}.json").write_text(json.dumps(result,indent=2)+'\n');return result
if __name__=='__main__':
    for d in ['solvents','records']:(P/d).mkdir(exist_ok=True)
    provenance={}
    for solvent in SOLVENTS:
        source=Path('/home/aaltamimi2/cosmo-artifacts/stage1')/(ref.ORCA_SOLVENT_FILE_STEMS[solvent]+'_cosmo.solute.orcacosmo');target=P/'solvents'/(solvent+'.orcacosmo');target.write_bytes(source.read_bytes());provenance[solvent]={'source':str(source),'pinned_copy':str(target),'sha256':digest(target),'cpu_origin':'historical workstation; not homogeneous Milan campaign library'}
    all_records=[json.loads(p.read_text()) for p in (ROOT/'state/campaign-v1/records').glob('*.json')];ready=[r for r in all_records if r['status']=='converged'];chosen=[];items=[]
    for anchor in ['DEP','DBP','BBP','DEHP']:
        key=getattr(ref,anchor+'_INCHIKEY').split('-')[0];r=next(r for r in ready if r['inchikey'].split('-')[0]==key);chosen.append((r,anchor))
        f=Path('/home/aaltamimi2/cosmo-artifacts/stage1')/(anchor.lower()+'_cosmo.solute.orcacosmo');pinned=P/(anchor.lower()+'-workstation.orcacosmo');pinned.write_bytes(f.read_bytes());items.append({'label':anchor+'-workstation-reference','kind':'workstation_replay','anchor':anchor,'surface':str(pinned),'surface_sha256':digest(pinned),'source_surface':str(f)})
    for group in ['main_le80','tail_gt80']:
        candidates=sorted([r for r in ready if r['input']['group']==group and not r.get('reused_from')],key=lambda r:r['input']['atoms'])
        for i in sorted(set([0,len(candidates)//3,2*len(candidates)//3,len(candidates)-1])):
            if candidates:chosen.append((candidates[i],None))
    for r,anchor in chosen:
        item={'label':r['inchikey'],'kind':'campaign_surface','anchor':anchor,'name':r['input']['name'],'input_inchikey':r['inchikey'],'perceived_inchikey':r['perceived_inchikey'],'identity_match_basis':r['identity_match_basis'],'group':r['input']['group'],'atoms':r['input']['atoms'],'surface':str(Path(r['archive_path'])/'surface.orcacosmo'),'surface_sha256':r['surface_sha256'],'cpu_model':r['cpu_model'],'orca_version':r['orca_version'],'orca_git':r['orca_git']};items.append(item)
    manifest={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'campaign_converged_at_snapshot':len(ready),'campaign_eligible':5824,'selected_campaign_count':len(chosen),'workstation_replay_count':4,'solvents':provenance,'table_solvent_panel':sorted(ref.TABLE_SOLVENT_KEYS),'temperature_K':298.15,'solute_mole_fractions':[1e-5,1e-6],'parameterization':'openCOSMORS24a','interpretation':'compatibility diagnostic using historical workstation solvent surfaces; not homogeneous Milan production predictions','items':items,'reference_code_sha256':digest(P/'reference_cosmo_logp.py')}
    (P/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
        for r in pool.map(predict,items):print(json.dumps({'label':r['label'],'status':r['status'],'wall_seconds':r['wall_seconds'],'error':r.get('error')}),flush=True)
