"""Build isolated 48-hour geometry restart, only for confirmed time-limit exits."""
import json,hashlib,copy
from pathlib import Path
from bounded_geometry_identity import verify
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1';S=P/'nitro-retry1';S.mkdir(exist_ok=True);(S/'body').mkdir(exist_ok=True)
inv=json.loads((P/'nitro-salvage-inventory.json').read_text());original=json.loads((P/'body/manifest.json').read_text());selected=[]
for row in inv['rows']:
 if row['disposition']!='time_limit_retry_candidate':continue
 assert not row.get('gbw_requires_further_inspection'),row['entry_id']
 m=copy.deepcopy(next(m for m in original['molecules'] if m['entry_id']==row['entry_id']));p=S/'prepared'/m['entry_id'];p.mkdir(parents=True,exist_ok=True);(p/'input.xyz').write_text(row['xyz']);check=verify(p/'input.xyz',m['inchikey'],'');assert check['identity_verified'],check
 m['array_index']=len(selected);m['restart_provenance']={k:v for k,v in row.items() if k!='xyz'};m['restart_provenance']['geometry_identity']=check;selected.append(m)
 (p/'preparation.json').write_text(json.dumps({'status':'prepared','xyz_sha256':hashlib.sha256((p/'input.xyz').read_bytes()).hexdigest(),'restart_provenance':m['restart_provenance']},indent=2)+'\n')
assert selected,'No confirmed time-limit candidates'
original.update(molecules=selected,name='contam-polymer24a-nitro-retry1',walltime='48:00:00',outlier_note='Heavily nitrated nitrocellulose is not described by atom-count runtime fit; owner-selected 48h.')
(S/'body/manifest.json').write_text(json.dumps(original,indent=2)+'\n')
s=(R/'scripts/polymer_runner.py').read_text().replace("ROOT=Path.home()/'plastchem-euler/polymer-v1'","ROOT=Path.home()/'plastchem-euler/polymer-v1/nitro-retry1'");(S/'polymer_runner.py').write_text(s)
s=(P/'body.sbatch').read_text().replace('contam-polymer24a-body-v1','contam-polymer24a-nitro-retry1').replace('72:00:00','48:00:00').replace('/polymer-v1/polymer_runner.py','/polymer-v1/nitro-retry1/polymer_runner.py');(S/'body.sbatch').write_text(s)
print(json.dumps({'candidates':len(selected),'entries':[m['entry_id'] for m in selected],'restart_sources':[m['restart_provenance']['restart_source'] for m in selected]}))
