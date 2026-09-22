"""Read-only local characterization; hypothetical policy outcomes never alter accepted results."""
import csv, hashlib, json, math, re, subprocess
from pathlib import Path
from rdkit import Chem, rdBase

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'state/identity-policy'
REPORT=ROOT/'reports/identity-policy'
pilot=json.loads((ROOT/'state/pilot-v1/verified-state.json').read_text())
for line in (ROOT/'state/INPUTS.sha256').read_text().splitlines():
    digest,name=line.split()
    assert hashlib.sha256((ROOT/'inputs'/name).read_bytes()).hexdigest()==digest
pinned={}
for r in csv.DictReader((ROOT/'inputs/plastchem_organics_orca_opencosmo_firstpass.csv').open()):
    pinned.setdefault(r['inchikey'],set()).add(r['smiles'])

def without_stereo(inchi):
    return '/'.join(layer for layer in inchi.split('/') if layer[0] not in 'btms')

rows=[]
for key,r in pilot['records'].items():
    if r.get('failure_mode')!='identity_unresolved_stereochemistry':continue
    smiles=r['input']['smiles']
    assert smiles in pinned[key]
    mol=Chem.MolFromSmiles(smiles)
    assert Chem.MolToInchiKey(mol)==key
    source_inchi=Chem.MolToInchi(mol)
    atom_tags=[a.GetIdx() for a in mol.GetAtoms() if a.GetChiralTag()!=Chem.ChiralType.CHI_UNSPECIFIED]
    bond_tags=[b.GetIdx() for b in mol.GetBonds() if b.GetStereo() not in (Chem.BondStereo.STEREONONE,Chem.BondStereo.STEREOANY)]
    potential=[{'type':str(x.type),'specified':str(x.specified),'centered_on':x.centeredOn} for x in Chem.FindPotentialStereo(mol)]
    xyz=Path('/mnt/r/plastchem-euler/results')/key/'optimized.xyz'
    process=subprocess.run(['/home/aaltamimi2/anaconda3/bin/obabel',str(xyz),'-oinchi'],capture_output=True,text=True,check=True)
    inchi=next(x for x in process.stdout.splitlines() if x.startswith('InChI='))
    observed=Chem.InchiToInchiKey(inchi)
    stored={x['method']:x['full_inchikey'] for x in r['identity_observations'] if x.get('full_inchikey')}
    assert observed==stored['openbabel_reference'] and stored['rdkit_determine_bonds']==observed
    row={'name':r['input']['name'],'input_inchikey':key,'input_smiles':smiles,
         'input_specified_atom_stereo_count':len(atom_tags),'input_specified_bond_stereo_count':len(bond_tags),
         'input_has_stereo_smiles_tokens':bool(re.search(r'[@/\\]',smiles)),
         'input_potential_stereo':potential,'input_inchi':source_inchi,
         'computed_inchikey':observed,'computed_inchi':inchi,
         'computed_stereo_layers':[x for x in inchi.split('/') if x[0] in 'btms'],
         'first_block_match':key.split('-')[0]==observed.split('-')[0],
         'third_block_match':key.split('-')[2]==observed.split('-')[2],
         'all_nonstereo_inchi_layers_identical':without_stereo(source_inchi)==without_stereo(inchi),
         'perception_engines_agree':len(set(stored.values()))==1,
         'optimized_xyz_sha256':hashlib.sha256(xyz.read_bytes()).hexdigest(),
         'openbabel_warnings':process.stderr.strip(),
         'hypothetical_option3_source_constraints_satisfied':not atom_tags and not bond_tags and without_stereo(source_inchi)==without_stereo(inchi)}
    rows.append(row)
assert len(rows)==9
assert all(r['first_block_match'] and r['all_nonstereo_inchi_layers_identical'] for r in rows)
assert all(r['hypothetical_option3_source_constraints_satisfied'] for r in rows)

def projection(k,n,population=5833):
    z=1.959963984540054;p=k/n;denom=1+z*z/n
    center=(p+z*z/(2*n))/denom
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return {'accepted':k,'denominator':n,'fraction':p,'population':population,'projected_count':population*p,
            'wilson_95_rate_interval':[center-half,center+half],
            'scaled_wilson_95_count_interval':[population*(center-half),population*(center+half)]}

executed=[r for r in pilot['records'].values() if r.get('dft_status')=='converged']
assert len(executed)==55
exact=sum(r['status']=='converged' for r in executed)
connectivity=sum(any(x.get('full_inchikey','').split('-')[0]==r['input']['inchikey'].split('-')[0] for x in r['identity_observations']) for r in executed)
assert exact==46 and connectivity==55
out={'policy_selected':None,'cluster_work_performed':False,'rdkit_version':rdBase.rdkitVersion,
     'cases':rows,'summary':{'cases':9,'inputs_with_any_specified_stereo':sum(r['input_specified_atom_stereo_count']>0 or r['input_specified_bond_stereo_count']>0 for r in rows),
         'first_block_matches':sum(r['first_block_match'] for r in rows),
         'stereo_only_full_inchi_differences':sum(r['all_nonstereo_inchi_layers_identical'] for r in rows),
         'both_perception_engines_agree':sum(r['perception_engines_agree'] for r in rows)},
     'projections_identity_conditional_on_execution':{'exact_full_inchikey':projection(46,55),'connectivity_only':projection(55,55),'connectivity_and_computed_stereo_with_source_constraints':projection(55,55)},
     'projections_including_preflight_at_pilot_rate':{'exact_full_inchikey':projection(46,56),'connectivity_only':projection(55,56),'connectivity_and_computed_stereo_with_source_constraints':projection(55,56)},
     'identity_failure_rate_using_selected_denominator':projection(9,56),
     'uncertainty_warning':'Scaled 95% Wilson binomial rate intervals are conditional illustrations, not design-valid confidence intervals or prediction intervals for this deliberately selected diversity pilot. They do not capture selection bias, population chemistry mix, unobserved failure modes or model misspecification. The 1/56 isotope preflight rate must not replace the exact census count of 9/5833 isotope-labelled structures.',
     'cpu_hour_estimate_changes':False}
(STATE/'analysis.json').write_text(json.dumps(out,indent=2)+'\n')
columns=[k for k in rows[0] if k not in ['input_potential_stereo','openbabel_warnings']]
with (REPORT/'nine-cases.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
    for r in rows:writer.writerow({k:json.dumps(r[k]) if isinstance(r[k],list) else r[k] for k in columns})
print(json.dumps({k:v for k,v in out.items() if k!='cases'},indent=2))
