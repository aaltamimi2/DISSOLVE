"""Retrieve cited density candidates for manual phase/temperature qualification.

Public reference retrieval only. No automatic acceptance, extrapolation,
concentration prediction or update to the pinned activity manifest.
"""
import datetime
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

D = Path('/mnt/r/plastchem-euler/phase10-v1')
OUT = D/'volume-sources/pubchem'


def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def extract(record):
    references={r['ReferenceNumber']:r for r in record.get('Reference',[])}
    observations=[]
    def walk(section, headings):
        heading=section.get('TOCHeading','')
        headings=headings+[heading]
        for info in section.get('Information',[]):
            value=info.get('Value',{})
            text='; '.join(r.get('String','') for r in value.get('StringWithMarkup',[]))
            if not text and 'Number' in value:text=str(value['Number'])+' '+value.get('Unit','')
            refs=info.get('ReferenceNumber',[])
            if isinstance(refs,int):refs=[refs]
            observations.append(dict(headings=headings,text=text,value=value,
                reference_numbers=refs,references=[references[n] for n in refs if n in references],
                raw_reference_strings=info.get('Reference',[]),
                source_name=info.get('Name'),
                mentions_25_C=bool(re.search(r'\b25\s*(?:°|deg|degrees)?\s*C\b|\b298\.15\s*K\b',text,re.I)),
                explicitly_estimated=bool(re.search(r'predict|estimat|calculat',text,re.I)),
                qualification='candidate_only_manual_identity_temperature_phase_review_required'))
        for child in section.get('Section',[]):walk(child,headings)
    walk(record,[])
    return observations


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((D/'manifest.json').read_text())
    results=[]
    def fetch(url, path):
        if path.exists():return json.loads(path.read_text())
        time.sleep(.3)  # below the five-requests/second public limit
        request=Request(url,headers={'User-Agent':'PlastChem-reference-audit/1.0 (public research data; serial requests)'})
        try:
            with urlopen(request,timeout=40) as response:
                status=response.status;content=response.read()
        except HTTPError as error:
            status=error.code;content=error.read()
        evidence=dict(url=url,retrieved_utc=utc(),http_status=status,
                      response_sha256=hashlib.sha256(content).hexdigest())
        raw=path.with_suffix('.response');raw.write_bytes(content)
        evidence['raw_path']=str(raw)
        if status==200:evidence['data']=json.loads(content)
        else:evidence['error_excerpt']=content.decode(errors='replace')[:500]
        save(path,evidence)
        return evidence
    for s in manifest['solvents']:
        available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
        assert available>=2.5*1024**2,'Memory guard; completed references retained'
        key=s['inchikey'];row=dict(solvent=s['name'],input_inchikey=key,observations=[])
        try:
            cid=fetch('https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/'+quote(key,safe='')+'/cids/JSON',OUT/(key+'-cid.json'))
            ids=cid.get('data',{}).get('IdentifierList',{}).get('CID',[])
            row['CID_lookup']=cid
            if not ids:row['status']='no_CID_or_lookup_error'
            else:
                assert len(ids)==1,('ambiguous CID lookup',ids)
                number=ids[0];row['CID']=number
                density=fetch(f'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{number}/JSON?heading=Density',OUT/(key+'-density.json'))
                row['density_evidence']=dict(url=density['url'],retrieved_utc=density['retrieved_utc'],
                    response_sha256=density['response_sha256'],raw_path=density['raw_path'],http_status=density['http_status'])
                if density['http_status']==200:
                    record=density['data']['Record'];assert record['RecordNumber']==number
                    row['observations']=extract(record);row['status']='candidates_retrieved'
                else:row['status']='no_density_section_or_retrieval_error'
        except Exception as exc:row.update(status='retrieval_error',error=repr(exc))
        results.append(row)
        save(D/'volume-reference-candidates.json',dict(utc=utc(),status='unqualified_candidates',
            expected_solvents=39,queried=len(results),solvents_with_observations=sum(bool(r['observations']) for r in results),
            rows=results,scope='No value promoted; source phase, temperature, density units and identity require review.'))
        print(json.dumps(dict(utc=utc(),solvent=s['name'],status=row['status'],observations=len(row['observations']))),flush=True)


if __name__=='__main__':main()
