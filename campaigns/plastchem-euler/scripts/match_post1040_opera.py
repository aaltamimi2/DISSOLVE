import csv,datetime as dt,hashlib,json,re,collections,math
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/post1040-octanol-2026-09-15');S=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15/sources');cohort=json.loads((D/'cohort.json').read_text());receipt=json.loads((S/'OPERA_Data.retrieval.json').read_text());p=S/'LogP_QR.sdf';raw=p.read_bytes();digest=hashlib.sha256(raw).hexdigest();assert digest=='de50a1eb020ae42f7f89f8cb7dd73987048e80f30ef32eeaf376451cb832e489';rows=[]
for i,b in enumerate(raw.decode().replace('\r\n','\n').split('$$$$')):
 if not b.strip():continue
 r=dict(re.findall(r'>\s*<([^>]+)>[^\n]*\n(.*?)(?=\n\s*\n)',b,re.S));r['sdf_record_number']=i+1;rows.append(r)
def write(p,rows,fields=None):
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
fields=sorted({k for r in rows for k in r})
# Reuse pinned source files read-only; do not rewrite earlier extracts.
accepted=[];rejected=[];byblock=collections.defaultdict(list);bycas=collections.defaultdict(list)
for r in rows:
 reason=None
 if r.get('Tr_1_Tst_0') not in ['0','1']:reason='not_flagged_training_or_test'
 elif not r.get('Kow Reference') or r['Kow Reference'].strip()=='?':reason='no_identifiable_observation_reference'
 elif re.search(r'estimat|comput|predict|calculat|XLogP|KOWWIN|OPERA',r['Kow Reference'],re.I):reason='model_or_estimate_reference'
 try:v=float(r.get('LogP',''));assert math.isfinite(v)
 except Exception:reason='non_numeric_observed_LogP'
 if reason:rejected.append({'source_record':r['sdf_record_number'],'reason':reason});continue
 accepted.append(r);byblock[r['InChI Key_QSARr'].split('-')[0]].append(r);bycas[r.get('CAS','')].append(r)
base=list(csv.DictReader(Path('/mnt/r/plastchem-euler/progress-2026-09-14/pubchem-measured-validation/experimental-reference-candidates.csv').open()))[0];candidates=[];casconflicts=[];matched=set();casconfirmed=set()
for m in cohort['records']:
 k=m['inchikey'];cas=m['input'].get('cas','');hits=byblock.get(k.split('-')[0],[])
 for r in bycas.get(cas,[]) if cas else []:
  if r['InChI Key_QSARr'].split('-')[0]!=k.split('-')[0]:casconflicts.append({'input_inchikey':k,'cas':cas,'source_inchikey':r['InChI Key_QSARr'],'source_record':r['sdf_record_number'],'disposition':'secondary_CAS_match_conflicts_with_connectivity_not_accepted'})
 for r in hits:
  matched.add(k)
  if cas and r.get('CAS')==cas:casconfirmed.add(k)
  out={f:'' for f in base};out.update(input_inchikey=k,name=m['input']['name'],cas=cas,solvent='octanol',reference='water',source_name='OPERA curated experimental PHYSPROP training/test data',raw_value_text=r['LogP'],raw_reference_string=r['Kow Reference']+' | Mansouri et al. (2018), doi:10.1186/s13321-018-0263-1; OPERA LogP_QR.sdf record '+str(r['sdf_record_number']),source_url=receipt['url'],source_record_url='https://github.com/kmansouri/OPERA/blob/'+receipt['commit']+'/OPERA_Data.zip',retrieved_utc=receipt['retrieved_utc'],source_sha256=digest,source_class='OPERA_curated_observed_LogP_PHYSPROP',observed_operator='=',measured_logKow=float(r['LogP']),qualification='qualified_observed_training_test_field_with_citation',selection_priority=3,conditions_note='Dataset-level experimental provenance; observed LogP field, not a model prediction. Training/test flag '+r['Tr_1_Tst_0']+'. Connectivity first-block match; CAS '+('agrees' if cas==r.get('CAS') else 'not confirmed')+'. Source key '+r['InChI Key_QSARr']+'. Per-row experimental boolean is not supplied. Original measurement method/phase/temperature are not inferred.')
  candidates.append(out)
write(D/'opera-experimental-reference-candidates.csv',candidates,list(base));write(D/'opera-CAS-conflicts.csv',casconflicts,['input_inchikey','cas','source_inchikey','source_record','disposition'])
summary={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'cohort_denominator':cohort['denominator'],'source_records':len(rows),'training':sum(r.get('Tr_1_Tst_0')=='1' for r in rows),'test':sum(r.get('Tr_1_Tst_0')=='0' for r in rows),'qualified_source_records':len(accepted),'rejected_source_records':len(rejected),'matched_molecules':len(matched),'matched_observations':len(candidates),'first_block_matched_molecules':len(matched),'CAS_confirmed_molecules':len(casconfirmed),'CAS_only_accepted':0,'CAS_conflicts':casconflicts,'source_receipt':receipt,'sdf_sha256':digest,'extracted_csv_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in S.glob('*observed.csv')},'experimental_provenance':'Observed LogP field in the published curated experimental training/test dataset; no per-row experimental boolean exists; no OPERA-predicted field read.'}
(D/'opera-match-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items() if k not in ['CAS_conflicts','source_receipt']}))
