"""Observation-level PubChem logKow qualification; retain raw citations and choose one measured reference per molecule."""
import csv,datetime as dt,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/pubchem-post4579-2026-09-17');D=B/'pubchem-measured-validation';D.mkdir(exist_ok=True)
retrieval=json.loads((B/'retrieval.json').read_text());observations=[];rejected=[]
def infos(obj):
 if isinstance(obj,dict):
  if 'Information' in obj:yield from obj['Information']
  for k,v in obj.items():
   if k!='Information':yield from infos(v)
 elif isinstance(obj,list):
  for v in obj:yield from infos(v)
for item in retrieval['rows']:
 key=item['inchikey'];path=B/'reference-sources'/f'pubchem-expansion-{key}-logP.json'
 if not path.exists():continue
 doc=json.loads(path.read_text());references={r['ReferenceNumber']:r for r in doc['Record'].get('Reference',[])};receipt=json.loads(path.with_suffix('.retrieval.json').read_text());assert hashlib.sha256(path.read_bytes()).hexdigest()==receipt['sha256']
 for info in infos(doc):
  source=references.get(info.get('ReferenceNumber'),{});name=source.get('SourceName','');value=info.get('Value',{});texts=[x['String'] for x in value.get('StringWithMarkup',[])];texts+=list(map(str,value.get('Number',[])))
  citations=info.get('Reference',[])+[json.dumps(x,sort_keys=True) for x in info.get('ExtendedReference',[])]+[json.dumps(x,sort_keys=True) for x in source.get('ExtendedReference',[])]
  for text in texts:
   row={'input_inchikey':key,'name':item['name'],'cas':item.get('cas',''),'cid':item['cid'],'solvent':'octanol','reference':'water','source_name':name,'raw_value_text':text,'raw_reference_string':' | '.join(citations),'source_url':item['source_url'],'source_record_url':source.get('URL',''),'retrieved_utc':receipt['retrieved_utc'],'source_sha256':receipt['sha256'],'source_class':'','observed_operator':'','measured_logKow':'','qualification':'','selection_priority':99,'conditions_note':'Source temperature and wet/dry conditions not inferred from the numeric value; pure-solvent model differs from mutually saturated experimental phases.'}
   if re.search(r'estimat|comput|predict|calculat|XLogP',text,re.I):row['qualification']='excluded_explicitly_modelled'
   elif re.search(r'GEMS|KOWWIN|CLOGP|EPI.?Suite|estimat|predict|calculat',' | '.join(citations),re.I):row['qualification']='excluded_model_or_unverified_computation_citation'
   elif not citations:row['qualification']='excluded_no_observation_citation'
   elif name=='Hazardous Substances Data Bank (HSDB)':row.update(qualification='qualified',source_class='HSDB_cited_experimental_or_literature_logKow',selection_priority=0 if any('Ellington' in x or 'Andersson' in x for x in citations) else 1)
   elif name=='SangsterLogP':row.update(qualification='qualified',source_class='Sangster_curated_experimental_logP',selection_priority=2,conditions_note=row['conditions_note']+' Sangster source is curated and may include logD-to-logP adjustments/consensus processing; retained as a distinct reference class.')
   else:row['qualification']='excluded_measurement_provenance_unverified'
   if any('a908863f' in citation.lower() for citation in citations):
    row['conditions_note']+=' Primary paper reports stearyl-acrylate partition dependence on octanol concentration in water and discusses micelle effects (https://doi.org/10.1039/A908863F). Not an exact infinite-dilution comparison.'
   if any('2280184' in citation or 'Roda A' in citation for citation in citations):
    row['conditions_note']+=' Primary bile-acid study varies pH and ionic strength and distinguishes protonated/ionized species (https://pubmed.ncbi.nlm.nih.gov/2280184/); the species assignment of this HSDB number has not been independently recovered.'
   match=re.fullmatch(r'\s*(?:(?:log\s*(?:K\s*ow|P|Kow))\s*[=:]?\s*)?([<>]=?)?\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s*\([^)]*\))?\s*',text,re.I)
   if match:row.update(observed_operator=match.group(1) or '=',measured_logKow=float(match.group(2)))
   else:row['qualification']='excluded_numeric_parse_ambiguous'
   (observations if row['qualification']=='qualified' else rejected).append(row)
unique=[]
for row in sorted(observations,key=lambda r:r['selection_priority']):
 dois={x.lower().rstrip('.') for x in re.findall(r'10\.\d{4,9}/[A-Za-z0-9._;()/:+-]+',row['raw_reference_string'])}
 duplicate=False
 for prior in unique:
  prior_dois={x.lower().rstrip('.') for x in re.findall(r'10\.\d{4,9}/[A-Za-z0-9._;()/:+-]+',prior['raw_reference_string'])}
  if row['input_inchikey']==prior['input_inchikey'] and row['measured_logKow']==prior['measured_logKow'] and row['observed_operator']==prior['observed_operator'] and dois & prior_dois:
   duplicate=True;break
 if duplicate:
  row['qualification']='duplicate_same_compound_value_and_primary_DOI';rejected.append(row)
 else:unique.append(row)
observations=unique
fields=list((observations or rejected)[0])
for filename,rows in [('experimental-reference-candidates.csv',observations),('excluded-reference-observations.csv',rejected)]:
 with (D/filename).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
best={}
for row in sorted(observations,key=lambda r:(r['selection_priority'],r['source_name'],r['raw_reference_string'])):
 if row['observed_operator']=='=':best.setdefault(row['input_inchikey'],row)
with (D/'best-measured-logKow.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(best.values())
cohort=json.loads((B/'remaining-pubchem-cohort.json').read_text())['cohort']
with (D/'per-molecule-best-measured.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=[*fields,'reference_status']);w.writeheader()
 for member in cohort:
  key=member['inchikey']
  if key in best:w.writerow({**best[key],'reference_status':'qualified_point_value'})
  else:
   record=json.loads((ROOT/'state/campaign-v1/records'/f'{key}.json').read_text())
   status='no_qualified_point_value' if any(r['inchikey']==key for r in retrieval['rows']) else 'retrieval_pending'
   w.writerow({'input_inchikey':key,'name':record['input']['name'],'reference_status':status})
summary={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'cohort_denominator':len(cohort),'retrieval_molecules_completed':len(retrieval['rows']),'retrieval_complete':len(retrieval['rows'])==len(cohort),'qualified_observations':len(observations),'qualified_molecules':len({r['input_inchikey'] for r in observations}),'best_point_value_molecules':len(best),'censored_observations':sum(r['observed_operator']!='=' for r in observations),'excluded_observations':len(rejected),'best_value_policy':'HSDB citations to direct measurements first, other cited HSDB/Hansch literature values next, cited Sangster experimental compilation last. No predicted value; one point value per molecule for primary error statistics.','output_root':str(D)}
(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
