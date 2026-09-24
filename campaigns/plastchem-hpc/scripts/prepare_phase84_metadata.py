"""Read-only product mapping evidence for the future release; no promotion."""
import ast,csv,datetime,hashlib,json,gzip
from pathlib import Path
import duckdb
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1')
P=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert not (D/'release-metadata-pins.json').exists(),'Pinned metadata snapshot already exists; never mix later campaign/product changes into it'
 out=D/'release-metadata';out.mkdir(exist_ok=True)
 code=P/'thermodynamics.py';tree=ast.parse(code.read_text());identities=None
 for node in tree.body:
  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='POLYMER_IDENTITIES' for t in node.targets):identities=ast.literal_eval(node.value)
  if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id=='POLYMER_IDENTITIES':identities=ast.literal_eval(node.value)
 assert identities is not None
 db=P/'data/thermodynamics.duckdb';con=duckdb.connect(str(db),read_only=True);con.execute('SET threads=1');con.execute("SET memory_limit='256MB'")
 grid=dict(con.execute('SELECT polymer,count(*) FROM solubility_grid GROUP BY polymer').fetchall());con.close()
 mapping={'evoh':['EVOH'],'nylon6':['NYLON6'],'nylon66':['NYLON66'],'pc':['PC'],'pe':['LDPE','HDPE'],'pet':['PET'],'pp':['PP'],'ps':['PS'],'pvc':['PVC'],'pvdf':['PVDF']}
 manifest=json.loads((D.parent/'phase8-v1/manifest.json').read_text());assert set(mapping)==set(manifest['polymers'])
 rows=[]
 for polymer,keys in mapping.items():
  for key in keys:
   assert key in identities,key
   rows.append({'campaign_polymer':polymer,'product_polymer_key':key,'shared_model_multiple_materials':len(keys)>1,
    'conformer_count':len(manifest['polymers'][polymer]),'product_identity_recognized':True,'legacy_solubility_grid_rows':grid.get(key,0),
    'legacy_solubility_available':bool(grid.get(key,0)),
    'mapping_note':'One electronic conformer ensemble maps to LDPE and HDPE; no material-specific crystallinity or fusion parameters are inferred.' if polymer=='pe' else ('Partitioning available; legacy S(T) absent, so a full dissolution-based screen lacks this input.' if key not in grid else 'Exact product identity; polymer S(T) remains legacy.')})
 with (out/'polymer-product-map.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 evidence={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'read_only':True,'product_code':str(code),'product_code_sha256':sha(code),'product_database':str(db),'product_database_sha256':sha(db),'legacy_grid_counts':grid,'phase81_manifest_sha256':sha(D.parent/'phase8-v1/manifest.json')}
 (out/'product-mapping-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
 cohort=json.loads((D/'cohort.json').read_text());contaminants=[];seen=set()
 def add(i,tier,status,**extra):
  key=i['inchikey'];assert key not in seen,key;seen.add(key)
  contaminants.append({'input_inchikey':key,'name':i['name'],'smiles':i['smiles'],'cas':i.get('cas',''),'plastchem_id':i.get('plastchem_id',''),
   'molecular_weight_g_mol':i.get('molecular_weight_g_mol',''),'atom_count':i.get('atoms',i.get('n_atoms_with_H','')),'group':'CHNO','tier':tier,
   'campaign_status_at_snapshot':status,'in_phase83_snapshot':status=='converged',**extra})
 for r in cohort['rows']:
  add(r['input'],r['tier'],'converged',perceived_inchikey=r['perceived_inchikey'],identity_match_basis=r['identity_match_basis'],
   perception_engines_agreeing_on_perceived_key=json.dumps(r['perception_engines_agreeing_on_perceived_key']),surface_sha256=r['surface_sha256'],
   source_record_sha256=r['record_sha256'],phase83_index=r['index'])
 for r in cohort['not_accepted_records']:
  folder='campaign-v1' if r['tier']=='main' else 'tier2-v1';p=R/'state'/folder/'records'/(r['input']['inchikey']+'.json')
  assert sha(p)==r['record_sha256'],'Failure record changed since snapshot; retrieve pinned version rather than mix snapshots'
  source=json.loads(p.read_text());add(r['input'],r['tier'],r['status'],failure_mode=source.get('failure_mode',''),failure_detail=source.get('error',''),
   perceived_inchikey=source.get('perceived_inchikey',''),source_record_sha256=r['record_sha256'])
 tier_input=R/'inputs/plastchem_organics_orca_opencosmo_tier2_chno_mw500_700.csv'
 for i in csv.DictReader(tier_input.open()):
  if i['inchikey'] not in seen:add(i,'tier2','not_yet_run',exclusion_reason='Outside the accepted Phase8.3 cohort at snapshot',source_input_sha256=sha(tier_input))
 exclusions=list(csv.DictReader((R/'reports/campaign-v1/exclusions.csv').open()));assert len(exclusions)==9
 originals={r['inchikey']:r for r in csv.DictReader((R/'inputs/plastchem_organics_orca_opencosmo_firstpass_no_sib.csv').open())}
 for x in exclusions:add(originals[x['input_inchikey']],'main','excluded_isotope',exclusion_reason=x['reason'])
 assert len(contaminants)==6103
 with gzip.open(out/'contaminants.csv.gz','wt',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for row in contaminants for k in row)));w.writeheader();w.writerows(contaminants)
 evidence['cohort_sha256']=sha(D/'cohort.json');evidence['tier2_input_sha256']=sha(tier_input);evidence['contaminant_rows']=len(contaminants)
 (out/'product-mapping-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
 pins={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort_sha256':sha(D/'cohort.json'),
  'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()}}
 (D/'release-metadata-pins.json').write_text(json.dumps(pins,indent=2)+'\n')
 print(json.dumps({'mapping_rows':len(rows),'missing_legacy_grid':[r['product_polymer_key'] for r in rows if not r['legacy_solubility_available']]},indent=2))
if __name__=='__main__':main()
