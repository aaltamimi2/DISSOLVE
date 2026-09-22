import json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/solvent-library-v1';m=json.loads((P/'solvent_library/manifest.json').read_text());counts=dict(converged=0,failed=0,running=0,awaiting_verification=0,not_yet_run=0,denominator=6)
for mol in m['molecules']:
 f=P/'records'/(mol['inchikey']+'.json');r=json.loads(f.read_text()) if f.exists() else {};s=r.get('status','not_yet_run');cat=s if s in ['converged','failed','not_yet_run'] else 'awaiting_verification' if s=='converged_identity_pending' else 'running';counts[cat]+=1
out={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'counts':counts,'groups':{'solvent_library':counts},'scope':'supporting solvent library, not campaign contaminant denominator','array_job_id':json.loads((P/'solvent_library/submission-receipt.json').read_text())['array_job_id']};tmp=P/'summary.tmp';tmp.write_text(json.dumps(out,indent=2)+'\n');tmp.replace(P/'summary.json')
print(json.dumps(out))
