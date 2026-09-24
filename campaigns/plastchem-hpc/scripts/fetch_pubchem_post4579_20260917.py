"""Retrieve cited LogP sections for relevant completed molecules; no automatic qualification."""
import argparse,datetime as dt,hashlib,json,re,time
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/pubchem-post4579-2026-09-17');D=B/'reference-sources';S=B/'retrieval.json'
parser=argparse.ArgumentParser();parser.add_argument('--all-cohort',action='store_true');args=parser.parse_args()
if args.all_cohort:S=B/'retrieval.json'
cohort=json.loads((B/'remaining-pubchem-cohort.json').read_text())['cohort'];rows=[];session=requests.Session()
def fetch(url,path):
 if path.exists():return json.loads(path.read_text())
 receipt=path.with_suffix('.retrieval.json')
 if receipt.exists():
  previous=json.loads(receipt.read_text())
  if previous['http_status']==404:return {'retrieval_failure':previous}
 response=session.get(url,timeout=40);stamp=dt.datetime.now(dt.timezone.utc).isoformat();time.sleep(.4)
 info={'url':url,'retrieved_utc':stamp,'http_status':response.status_code,'sha256':hashlib.sha256(response.content).hexdigest()}
 path.with_suffix('.retrieval.json').write_text(json.dumps(info,indent=2)+'\n')
 if response.status_code!=200:return {'retrieval_failure':info}
 path.write_bytes(response.content);return response.json()
def strings(obj):
 if isinstance(obj,dict):
  if 'String' in obj:yield obj['String']
  for v in obj.values():yield from strings(v)
 elif isinstance(obj,list):
  for v in obj:yield from strings(v)
for member in cohort:
 key=member['inchikey'];record=json.loads((ROOT/'state/campaign-v1/records'/f'{key}.json').read_text());m=record['input']
 if not args.all_cohort and not re.search(r'phthal|bisphen|benzophen|phenol|benzotriazol',m['name'],re.I):continue
 row={'inchikey':key,'name':m['name'],'cas':m.get('cas'),'status':'unqualified_pending_review'}
 try:
  identity=fetch('https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/'+key+'/property/InChIKey/JSON',D/f'pubchem-expansion-{key}-identity.json')
  props=identity.get('PropertyTable',{}).get('Properties',[]);assert len(props)==1 and props[0]['InChIKey']==key,'Identity lookup missing or ambiguous'
  cid=props[0]['CID'];url=f'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=LogP';row.update(cid=cid,source_url=url)
  doc=fetch(url,D/f'pubchem-expansion-{key}-logP.json');row['property_text']=list(strings(doc));row['references']=doc.get('Record',{}).get('Reference',[])
  if 'retrieval_failure' in doc:row['status']='no_retrieved_LogP_section';row['retrieval_failure']=doc['retrieval_failure']
 except Exception as e:row.update(status='retrieval_or_identity_failure',error=str(e))
 rows.append(row);S.write_text(json.dumps({'updated_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'cohort':len(cohort),'rows':rows,'interpretation':'Raw candidate retrieval only. Modelled/estimated values must not enter experimental-reference CSV.'},indent=2)+'\n')
 print(json.dumps({'processed':len(rows),'name':m['name'],'status':row['status'],'texts':row.get('property_text',[])}),flush=True)
