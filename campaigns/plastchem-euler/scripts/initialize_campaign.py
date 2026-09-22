"""Pin the A-3 campaign, explicit exclusions, and reuse of completed reference-recipe pilot."""
import csv,hashlib,json
from pathlib import Path
from rdkit import Chem,rdBase
from identity_campaign import decide,MODE

ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1'
def write(name,obj):
    path=P/name;path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():assert json.loads(path.read_text())==obj,name
    else:path.write_text(json.dumps(obj,indent=2)+'\n')
for line in (ROOT/'state/INPUTS.sha256').read_text().splitlines():
    sha,name=line.split();assert hashlib.sha256((ROOT/'inputs'/name).read_bytes()).hexdigest()==sha
charter=(ROOT/'CHARTER.txt').read_bytes();sha=hashlib.sha256(charter).hexdigest()
assert b'AMENDMENT A-3' in charter
(ROOT/'state/charter-history'/f'{sha}.txt').write_bytes(charter)
(ROOT/'state/CHARTER.last-read.sha256').write_text(sha+'\n')
policy={'mode':MODE,'authority':'Owner decision A-3 D-IDENT','charter_sha256':sha,'require_stereo_layer_match':False,'isotope_policy':'exclude; no parent mapping','campaign_concurrency_limit':32}
write('policy.json',policy)
census=json.loads((ROOT/'state/pilot-v1/census.json').read_text())
pilot=json.loads((ROOT/'state/pilot-v1/verified-state.json').read_text())
eligible=[];excluded=[];reused=[];pending=[]
for molecule in sorted(census,key=lambda x:x['inchikey']):
    m=dict(molecule);key=m['inchikey'];mol=Chem.MolFromSmiles(m['smiles'])
    if any(a.GetIsotope() for a in mol.GetAtoms()):
        excluded.append({'input_inchikey':key,'name':m['name'],'status':'excluded','reason':'Owner A-3 D-ISO: deuterated isotope-labelled entry excluded from campaign','isotope_composition':[{'element':a.GetSymbol(),'mass_number':a.GetIsotope()} for a in mol.GetAtoms() if a.GetIsotope()]})
        continue
    m.pop('array_index',None);m.pop('selection_role',None);m.pop('stratum',None)
    m['group']='tail_gt80' if m['atoms']>80 else 'main_le80'
    m['input_stereo_specified']=any(a.GetChiralTag()!=Chem.ChiralType.CHI_UNSPECIFIED for a in mol.GetAtoms()) or any(b.GetStereo() not in (Chem.BondStereo.STEREONONE,Chem.BondStereo.STEREOANY) for b in mol.GetBonds())
    eligible.append(m)
    old=pilot['records'].get(key)
    if old and old.get('dft_status')=='converged':
        result=dict(old);result.update(decide(key,old['identity_observations']))
        assert result['identity_verified'] and result['cpu_model']=='AMD EPYC 7763 64-Core Processor'
        result.update(status='converged',scope='phase2_campaign',input=m,reused_from='phase1_pilot',identity_authority=policy)
        result.pop('failure_mode',None);result.pop('error',None)
        for name,digest in [('surface.orcacosmo',old['surface_sha256']),*[(stage+'.inp',info['input_sha256']) for stage,info in old['stages'].items()]]:
            assert hashlib.sha256((Path(old.get('archive_path',f'/mnt/r/plastchem-euler/results/{key}'))/name).read_bytes()).hexdigest()==digest
        write(f'records/{key}.json',result);reused.append(key)
    else:pending.append(m)
assert len(census)==5833 and len(excluded)==9 and len(eligible)==5824 and len(reused)==55
assert sum(m['atoms']>80 for m in eligible)==89
write('exclusions.json',{'pinned_unique':5833,'excluded_count':9,'eligible_unique':5824,'authority':'Owner A-3 D-ISO','records':excluded})
for group,cap,wall in [('main_le80',28,'24:00:00'),('tail_gt80',4,'48:00:00')]:
    molecules=sorted((dict(m) for m in pending if m['group']==group),key=lambda m:(-m['atoms'],m['inchikey']))
    for i,m in enumerate(molecules):m['array_index']=i
    write(group+'/manifest.json',{'campaign':'contam-p2-milan-v1','group':group,'name':'contam-p2-'+group+'-v1','concurrency':cap,'walltime':wall,'rdkit_version':rdBase.rdkitVersion,'policy':policy,'recipe':{'n_embed':300,'seed':12345,'prune_rms':0.5,'max_mmff_iters':2000,'dft_conformers':1},'molecules':molecules})
write('eligible.json',eligible)
write('plan.json',{'pinned_unique':5833,'excluded_unique':9,'eligible_unique':5824,'main_eligible':5735,'tail_eligible':89,'pilot_reused':55,'new_targets_pending_preparation':len(pending),'main_new_targets':sum(m['group']=='main_le80' for m in pending),'tail_new_targets':89,'concurrency_total':32,'main_cap':28,'tail_cap':4,'main_walltime':'24:00:00','tail_walltime':'48:00:00','automatic_retries':False,'isotope_mapping':False,'prior_diagnostic_ethanol_not_reused_reason':'A-1 was a diagnostic, without the campaign 300-candidate MMFF conformer-ranking preparation; its evidence remains archived.'})
print((P/'plan.json').read_text())
