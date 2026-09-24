"""Close experimental-reference retrieval coverage for the frozen 27 tier-2 rows.

No prediction, source substitution or qualification of PubChem observations.
OPERA matching uses only the pinned observed field and identifiable citations.
All downloaded response bytes (including failures) have retrieval receipts.
"""
import argparse
import collections
import csv
import datetime
import hashlib
import json
import math
import re
import time
from pathlib import Path

B=Path('/mnt/r/plastchem-euler')
D=B/'phase10-v1/tier2-reference-review'
SOURCE=B/'measured-expansion-2026-09-15/sources'
SDF_PIN='de50a1eb020ae42f7f89f8cb7dd73987048e80f30ef32eeaf376451cb832e489'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(p,v):
    temp=p.with_suffix('.tmp');temp.write_text(json.dumps(v,indent=2)+'\n');temp.replace(p)


def opera(cohort):
    p=SOURCE/'LogP_QR.sdf';assert sha(p)==SDF_PIN
    receipt=json.loads((SOURCE/'OPERA_Data.retrieval.json').read_text())
    matches=[];conflicts=[];qualified=0;rejected=collections.Counter();blocks=collections.defaultdict(list);cas=collections.defaultdict(list)
    for index,block in enumerate(p.read_text().replace('\r\n','\n').split('$$$$'),1):
        if not block.strip():continue
        r=dict(re.findall(r'>\s*<([^>]+)>[^\n]*\n(.*?)(?=\n\s*\n)',block,re.S))
        reason=None
        if r.get('Tr_1_Tst_0') not in ['0','1']:reason='not_training_or_test'
        elif not r.get('Kow Reference') or r['Kow Reference'].strip()=='?':reason='missing_observation_reference'
        elif re.search(r'estimat|comput|predict|calculat|XLogP|CLOGP|KOWWIN|OPERA',r['Kow Reference'],re.I):reason='model_or_estimate_reference'
        try:value=float(r.get('LogP',''));assert math.isfinite(value)
        except (ValueError,AssertionError):reason='not_finite_observed_LogP'
        if reason:rejected[reason]+=1;continue
        r.update(record_number=index,observed_LogP=value);qualified+=1
        blocks[r['InChI Key_QSARr'].split('-')[0]].append(r)
        cas[r.get('CAS','')].append(r)
    for c in cohort:
        key=c['inchikey'];input_cas=c['input'].get('cas','')
        for r in cas.get(input_cas,[]) if input_cas else []:
            if r['InChI Key_QSARr'].split('-')[0]!=key.split('-')[0]:
                conflicts.append(dict(input_inchikey=key,input_CAS=input_cas,source_inchikey=r['InChI Key_QSARr'],record_number=r['record_number'],accepted=False))
        for r in blocks.get(key.split('-')[0],[]):
            matches.append(dict(input_inchikey=key,name=c['input']['name'],cas=input_cas,
                observed_logKow=r['observed_LogP'],source_inchikey=r['InChI Key_QSARr'],source_CAS=r.get('CAS',''),
                CAS_agrees=input_cas==r.get('CAS'),record_number=r['record_number'],raw_reference_string=r['Kow Reference'],
                training_test_flag=r['Tr_1_Tst_0'],source_sha256=SDF_PIN,source_url=receipt['url'],
                source_record_url='https://github.com/kmansouri/OPERA/blob/'+receipt['commit']+'/OPERA_Data.zip',
                retrieved_utc=receipt['retrieved_utc'],qualification='observed_training_test_field_with_citation',
                provenance_limitation='Curated experimental dataset provenance; no per-row experimental boolean or inferred measurement conditions.'))
    result=dict(utc=utc(),cohort=27,qualified_source_records=qualified,rejected_source_records=dict(rejected),
        matched_molecules=len({r['input_inchikey'] for r in matches}),observations=matches,CAS_conflicts=conflicts,
        CAS_only_accepted=0,source_sha256=SDF_PIN,source_receipt=receipt)
    save(D/'opera-match.json',result);return result


def fetch_pubchem(cohort):
    import requests
    folder=D/'pubchem';folder.mkdir(exist_ok=True)
    session=requests.Session();last=0.;rows=[]
    def get(url,name):
        nonlocal last
        payload=folder/(name+'.json');receipt=folder/(name+'.retrieval.json')
        if receipt.exists():
            info=json.loads(receipt.read_text());assert info['url']==url and sha(payload)==info['sha256']
            if info['http_status'] not in [200,404]:raise RuntimeError('Retained transient HTTP failure requires review: '+str(receipt))
            return info,json.loads(payload.read_text()) if info['http_status']==200 else None
        assert not payload.exists(),'Response without receipt must be reconciled'
        time.sleep(max(0,.4-(time.monotonic()-last)))
        response=session.get(url,timeout=45);last=time.monotonic();payload.write_bytes(response.content)
        info=dict(url=url,retrieved_utc=utc(),http_status=response.status_code,sha256=sha(payload),bytes=len(response.content))
        save(receipt,info)
        if response.status_code not in [200,404]:raise RuntimeError('HTTP failure; stop without a burst of retries: '+str(response.status_code))
        return info,response.json() if response.status_code==200 else None
    for c in cohort:
        key=c['inchikey'];row=dict(input_inchikey=key,name=c['input']['name'],cas=c['input'].get('cas',''))
        identity_url='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/'+key+'/property/InChIKey/JSON'
        info,doc=get(identity_url,key+'-identity');row['identity_receipt']=info
        if doc is None:row['status']='identity_not_found'
        else:
            props=doc.get('PropertyTable',{}).get('Properties',[])
            assert len(props)==1 and props[0]['InChIKey'].split('-')[0]==key.split('-')[0],('Ambiguous or mismatched identity',key)
            cid=props[0]['CID'];row.update(cid=cid,source_inchikey=props[0]['InChIKey'])
            info,doc=get(f'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=LogP',key+'-logP')
            row.update(LogP_receipt=info,status='LogP_section_retrieved_unqualified' if doc else 'no_LogP_section')
        rows.append(row);save(D/'pubchem-retrieval.json',dict(utc=utc(),cohort=27,processed=len(rows),rows=rows))
        print(json.dumps(dict(processed=len(rows),input_inchikey=key,status=row['status'])),flush=True)
    return dict(counts=dict(collections.Counter(r['status'] for r in rows)),processed=len(rows))


def fetch_comptox(cohort):
    import requests
    import uuid
    folder=D/'comptox';folder.mkdir(exist_ok=True)
    base='https://comptox.epa.gov/dashboard-api/batchsearch/export/'
    session=requests.Session();results=[]
    for mode in ['INCHIKEY','CASRN']:
        values=[c['inchikey'] if mode=='INCHIKEY' else c['input'].get('cas','') for c in cohort]
        values=sorted({v for v in values if v})
        request=dict(identifierTypes=[mode],massError=0,downloadItems=['CASRN','INCHIKEY','CHEMICAL_PROPERTIES_DETAILS'],
            searchItems='\n'.join(values),inputType='IDENTIFIER',downloadType='EXCEL')
        request_path=folder/(mode+'-request.json');response_path=folder/(mode+'-response.txt')
        if request_path.exists():assert json.loads(request_path.read_text())==request
        else:save(request_path,request)
        receipt_path=folder/(mode+'-export-receipt.json')
        if not response_path.exists():
            response=session.post(base,json=request,timeout=60);response_path.write_bytes(response.content)
            save(receipt_path,dict(url=base,http_status=response.status_code,retrieved_utc=utc(),sha256=sha(response_path)))
            response.raise_for_status()
        receipt=json.loads(receipt_path.read_text());assert 200<=receipt['http_status']<300 and receipt['sha256']==sha(response_path)
        job=response_path.read_text().strip().strip('"');uuid.UUID(job)
        target=folder/(mode+'-properties.xlsx');pin=target.with_suffix('.retrieval.json')
        if target.exists():
            existing=json.loads(pin.read_text());assert existing['sha256']==sha(target)
            results.append(existing);continue
        status_log=folder/(mode+'-status.jsonl')
        for attempt in range(60):
            url=base+'status/'+job;response=session.get(url,timeout=40)
            with status_log.open('a') as f:f.write(json.dumps(dict(url=url,http_status=response.status_code,body=response.text,retrieved_utc=utc()))+'\n')
            response.raise_for_status()
            if response.text.strip().lower()=='true':break
            print(json.dumps(dict(source='CompTox',identifier_type=mode,export_id=job,status='pending')),flush=True)
            time.sleep(30)
        else:raise RuntimeError('CompTox export pending; resume the saved export id, never resubmit blindly')
        url=base+'content/'+job;response=session.get(url,timeout=60);response.raise_for_status()
        assert response.content.startswith(b'PK'),'Expected an XLSX export'
        target.write_bytes(response.content)
        receipt=dict(url=url,http_status=response.status_code,retrieved_utc=utc(),sha256=sha(target),bytes=len(response.content),export_id=job)
        save(pin,receipt);results.append(receipt)
        print(json.dumps(dict(source='CompTox',identifier_type=mode,export_id=job,status='downloaded_unqualified',bytes=len(response.content))),flush=True)
    return results


def qualify_comptox(cohort):
    import openpyxl
    folder=D/'comptox';bykey={r['inchikey']:r for r in cohort};cas=collections.defaultdict(list)
    for r in cohort:
        if r['input'].get('cas'):cas[r['input']['cas']].append(r['inchikey'])
    fields=next(csv.reader((B/'combined-validation-references-2026-09-17-pubchem-final571/experimental-reference-candidates.csv').open()))
    observations=[];excluded=[];counts={}
    for mode in ['INCHIKEY','CASRN']:
        path=folder/(mode+'-properties.xlsx');receipt=json.loads(path.with_suffix('.retrieval.json').read_text());assert receipt['sha256']==sha(path)
        workbook=openpyxl.load_workbook(path,read_only=True,data_only=True)
        identities=list(workbook['Main Data'].values);properties=list(workbook['Chemical Properties'].values)
        assert identities[0]==('INPUT','FOUND_BY','DTXSID','PREFERRED_NAME','CASRN','INCHIKEY')
        assert properties[0]==('DTXSID','DTXCID','TYPE','NAME','VALUE','UNITS','SOURCE','DESCRIPTION')
        mapped=collections.defaultdict(set)
        for identifier,found,dtx,name,source_cas,key in identities[1:]:
            keys=[identifier] if mode=='INCHIKEY' else cas.get(identifier,[])
            for k in keys:
                assert k in bykey
                if dtx and key and key.split('-')[0]==k.split('-')[0]:mapped[dtx].add(k)
                else:excluded.append(dict(input_inchikey=k,identifier_type=mode,source_inchikey=key,reason='unmatched_or_connectivity_mismatch'))
        counter=collections.Counter((r[2],r[3]) for r in properties[1:])
        counts[mode]=dict(mapped_structures=len(set().union(*mapped.values())) if mapped else 0,
            source_sha256=receipt['sha256'],explicit_experimental_LogKow_rows=counter['experimental','LogKow: Octanol-Water'],
            predicted_LogKow_rows_excluded=counter['predicted','LogKow: Octanol-Water'],
            all_properties=[dict(type=k[0],property=k[1],count=n) for k,n in sorted(counter.items())])
        for number,r in enumerate(properties[1:],2):
            dtx,cid,kind,name,value,units,source,description=r
            if kind!='experimental' or name!='LogKow: Octanol-Water':continue
            try:numeric=float(value);assert math.isfinite(numeric) and units=='Log10 unitless' and source and description
            except (ValueError,TypeError,AssertionError):
                excluded.append(dict(identifier_type=mode,row=number,reason='ambiguous_value_units_or_citation'));continue
            for key in mapped.get(dtx,[]):
                row={k:'' for k in fields};c=bykey[key]['input']
                row.update(input_inchikey=key,name=c['name'],cas=c.get('cas',''),solvent='octanol',reference='water',source_name=source,
                    raw_value_text=value,raw_reference_string=f'{source} | {description} | {path.name}: Chemical Properties row {number}; TYPE=experimental; DTXSID={dtx}',
                    source_url=f'https://comptox.epa.gov/dashboard/chemical/properties/{dtx}',source_record_url=receipt['url'],
                    retrieved_utc=receipt['retrieved_utc'],source_sha256=receipt['sha256'],source_class='CompTox_explicit_experimental_LogKow',
                    observed_operator='=',measured_logKow=numeric,qualification='qualified_explicit_experimental_type_with_source',selection_priority=4,
                    conditions_note='Explicit experimental flag and first-block connectivity match; measurement conditions not inferred; compilation sources may overlap.')
                observations.append(row)
        workbook.close()
    with (D/'comptox-experimental-reference-candidates.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(observations)
    result=dict(utc=utc(),cohort=27,qualified_observations=len(observations),qualified_molecules=len({r['input_inchikey'] for r in observations}),exports=counts,excluded=excluded)
    save(D/'comptox-qualification.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--fetch-pubchem',action='store_true');p.add_argument('--fetch-comptox',action='store_true');p.add_argument('--qualify-comptox',action='store_true');args=p.parse_args()
    D.mkdir(exist_ok=True)
    path=B/'phase83-v1/cohort.json';all_rows=json.loads(path.read_text())['rows'];cohort=[r for r in all_rows if r['tier']=='tier2']
    assert len(cohort)==27 and len({r['inchikey'] for r in cohort})==27
    save(D/'cohort.json',dict(cohort=27,source_cohort_sha256=sha(path),rows=cohort))
    result=opera(cohort);print(json.dumps(dict(OPERA_matched_molecules=result['matched_molecules'],observations=len(result['observations']))),flush=True)
    if args.fetch_pubchem:print(json.dumps(fetch_pubchem(cohort)),flush=True)
    if args.fetch_comptox:print(json.dumps(fetch_comptox(cohort)),flush=True)
    if args.qualify_comptox:
        result=qualify_comptox(cohort)
        print(json.dumps(dict(CompTox_qualified_molecules=result['qualified_molecules'],qualified_observations=result['qualified_observations'])),flush=True)


if __name__=='__main__':main()
