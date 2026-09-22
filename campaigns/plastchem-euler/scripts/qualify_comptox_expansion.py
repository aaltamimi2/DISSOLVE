"""Only explicit experimental LogKow rows from public batch exports; verified connectivity joins."""
import csv,json,hashlib,math,datetime
from pathlib import Path
import openpyxl
D=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15');S=D/'sources';cohort={r['inchikey']:r for r in json.loads((D/'cohort.json').read_text())['records']};fields=next(csv.reader((D/'opera-experimental-reference-candidates.csv').open()));out=[];excluded=[];counts={}
for filename in ['comptox-properties.xlsx','comptox-CAS-properties.xlsx']:
 p=S/filename
 if not p.exists():continue
 receipt=json.loads(p.with_suffix('.retrieval.json').read_text());assert hashlib.sha256(p.read_bytes()).hexdigest()==receipt['sha256'];w=openpyxl.load_workbook(p,read_only=True,data_only=True);mapping={};casmap=json.loads((S/'comptox-secondary-CAS-map.json').read_text())
 for r in list(w['Main Data'].values)[1:]:
  inp,found,dtx,name,cas,key=r
  if not dtx:continue
  keys=casmap.get(inp,[]) if 'CAS' in filename else [inp]
  for k in keys:
   if k in cohort and key and key.split('-')[0]==k.split('-')[0]:mapping.setdefault(dtx,set()).add(k)
   else:excluded.append({'file':filename,'reason':'connectivity_mismatch_or_missing','row':repr(r),'input_key':k})
 props=list(w['Chemical Properties'].values)[1:];exp=[r for r in props if r[2]=='experimental' and r[3]=='LogKow: Octanol-Water'];counts[filename]={'mapped_structures':len(set().union(*mapping.values())) if mapping else 0,'all_property_rows':len(props),'explicit_experimental_LogKow_rows':len(exp),'predicted_property_rows_excluded':sum(r[2]=='predicted' for r in props)}
 for idx,r in enumerate(props,2):
  dtx,cid,typ,name,val,units,source,desc=r
  if typ!='experimental' or name!='LogKow: Octanol-Water':continue
  try:number=float(val);assert math.isfinite(number) and units=='Log10 unitless' and source and desc
  except (ValueError,TypeError,AssertionError):excluded.append({'file':filename,'reason':'ambiguous_numeric_units_or_missing_citation','row':repr(r)});continue
  for k in mapping.get(dtx,[]):
   inp=cohort[k]['input'];out.append({'input_inchikey':k,'name':inp['name'],'cas':inp.get('cas',''),'cid':'','solvent':'octanol','reference':'water','source_name':source,'raw_value_text':val,'raw_reference_string':f'{source} | {desc} | {filename}: Chemical Properties row {idx}; TYPE=experimental; DTXSID={dtx}','source_url':f'https://comptox.epa.gov/dashboard/chemical/properties/{dtx}','source_record_url':receipt['url'],'retrieved_utc':receipt['retrieved_utc'],'source_sha256':receipt['sha256'],'source_class':'CompTox_explicit_experimental_LogKow','observed_operator':'=','measured_logKow':number,'qualification':'qualified_explicit_experimental_type_with_source','selection_priority':4,'conditions_note':'Dashboard experimental flag retained; OPERA2.8-labelled experimental SDF data are observed records, not its separately labelled predictions. Compilation sources may overlap OPERA/PHYSPROP and PubChem; alternative observations are not independent experiments. Original experimental conditions are not inferred. First-block connectivity required even for secondary CAS matches.'})
with (D/'comptox-experimental-reference-candidates.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exports':counts,'qualified_observations':len(out),'matched_molecules':len({r['input_inchikey'] for r in out}),'excluded':excluded};(D/'comptox-match-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
