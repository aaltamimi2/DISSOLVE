"""Independent A-5 input gate; no mutations to pinned inputs or cluster work."""
import csv,json,hashlib,subprocess,collections,datetime,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'inputs/polymers/polymer_conformers_v1.csv'
assert hashlib.sha256(p.read_bytes()).hexdigest()=='72e4fca5694615722cb6134a42d2126333dfe3a8fc56c9f969e41edbc911a3ae'
rows=list(csv.DictReader(p.open()));assert len(rows)==284
verified=[]
for r in rows:
 q=ROOT/r['xyz_path'];raw=q.read_bytes();assert hashlib.sha256(raw).hexdigest()==r['xyz_sha256']
 lines=raw.decode().splitlines();n=int(lines[0]);atoms=[x.split() for x in lines[2:] if x.strip()];assert n==int(r['n_atoms']) and len(atoms)==n
 counts=collections.Counter(a[0] for a in atoms);formula_counts={e:int(c or 1) for e,c in re.findall(r'([A-Z][a-z]?)(\d*)',r['formula'])};assert counts==formula_counts,(q,counts,formula_counts)
 result=subprocess.run(['obabel','-ixyz',str(q),'-oinchikey'],capture_output=True,text=True,check=True);key=result.stdout.strip().splitlines()[0];assert key.split('-')[0]==r['inchikey_connectivity'],(q,key,r['inchikey_connectivity'])
 verified.append(dict(r,perceived_full_inchikey=key,derived_element_counts=dict(counts)))
assert [int(r['submission_order']) for r in rows]==list(range(1,285))
assert [int(r['n_atoms']) for r in rows]==sorted(int(r['n_atoms']) for r in rows)
assert all(r['polymer']=='pe' and int(r['n_atoms'])==38 for r in rows[:31])
for species in {r['species'] for r in rows}:
 part=[r for r in rows if r['species']==species];assert len({r['inchikey_connectivity'] for r in part})==1 and len({r['formula'] for r in part})==1
v={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manifest_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(rows),'polymers':len({r['polymer'] for r in rows}),'species':len({r['species'] for r in rows}),'verified_xyz_digests_atom_counts_connectivity_formula':len(verified),'records':verified,'submitted':False}
out=ROOT/'state/polymer-v1';out.mkdir(exist_ok=True);(out/'input-verification.json').write_text(json.dumps(v,indent=2)+'\n')
print(json.dumps({k:x for k,x in v.items() if k!='records'}))
