import csv, hashlib, json
from pathlib import Path
from collections import Counter
from rdkit import Chem
from rdkit.Chem import Descriptors
root=Path(__file__).resolve().parents[1]
rows={}; report={}
for line in (root/'state/INPUTS.sha256').read_text().splitlines():
    digest,name=line.split(); p=root/'inputs'/name
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest, name
    data=list(csv.DictReader(p.open())); rows[name]=data
    report[name]={'sha256':digest,'rows':len(data),'unique_inchikeys':len({r['inchikey'] for r in data})}
def get(s): return rows['plastchem_organics_orca_opencosmo_'+s+'.csv']
def counter(rs): return Counter(tuple(r[c] for c in ['name','smiles','plastchem_id','cas','inchikey','molecular_weight_g_mol']) for r in rs)
tiers=[get(s) for s in ['firstpass','flagged_heteroatoms','flagged_si_b']]
sup=get('firstpass_audited_8065')
assert counter(sum(tiers,[]))==counter(sup)
assert counter(tiers[0]+tiers[1])==counter(get('firstpass_no_sib'))
sets=[{r['inchikey'] for r in t} for t in tiers]
assert all(not sets[i]&sets[j] for i in range(3) for j in range(i))
assert [(len(t),len(s)) for t,s in zip(tiers,sets)]==[(6064,5833),(1762,1721),(239,238)]
max_delta=0; max_mw=0
for r in sup:
    m=Chem.MolFromSmiles(r['smiles']); assert m is not None
    assert Chem.GetFormalCharge(m)==0 and len(Chem.GetMolFrags(m))==1
    mw=Descriptors.MolWt(m); max_mw=max(max_mw,mw)
    delta=abs(mw-float(r['molecular_weight_g_mol'])); max_delta=max(delta,max_delta); assert delta<1
for r in tiers[0]:
    assert {a.GetSymbol() for a in Chem.MolFromSmiles(r['smiles']).GetAtoms()} <= set(['C','H','N','O'])
anchors={k:sum(r['cas']==v for r in sup) for k,v in {'DEP':'84-66-2','DBP':'84-74-2','BBP':'85-68-7','DEHP':'117-81-7'}.items()}
assert all(anchors.values())
report.update(partition_rows_exact=True,partition_structures_disjoint=True,max_rdkit_mw=max_mw,max_mw_difference=max_delta,anchors_by_cas=anchors)
(root/'state/phase0-input-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
