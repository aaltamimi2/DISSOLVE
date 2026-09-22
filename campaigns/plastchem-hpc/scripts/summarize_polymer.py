"""Separate conformer accounting; no species or conformer collapsing."""
import json,collections,datetime,csv
from pathlib import Path
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1';groups={};rows=[]
for group in ['body','large']:
 molecules=json.loads((P/group/'manifest.json').read_text())['molecules'];c=dict(converged=0,failed=0,running=0,awaiting_verification=0,retry_pending=0,not_yet_run=0,denominator=len(molecules))
 for m in molecules:
  f=P/'records'/(m['entry_id']+'.json');r=json.loads(f.read_text()) if f.exists() else {};s=r.get('status','not_yet_run');cat=s if s in ['converged','failed','not_yet_run'] else 'awaiting_verification' if s=='converged_identity_pending' else 'running';cat='retry_pending' if (r.get('execution_outcome')=='time_limit' or r.get('failure_mode') in ['slurm_timeout','walltime_censored','scheduler_signal_10']) else cat;c[cat]+=1
  rows.append(dict(entry_id=m['entry_id'],polymer=m['polymer'],species=m['species'],conformer_id=m['conformer_id'],submission_order=m['submission_order'],atoms=m['atoms'],status=cat,cpu_model=r.get('cpu_model'),failure_mode=r.get('failure_mode'),input_inchikey=m['inchikey'],perceived_inchikey=r.get('perceived_inchikey'),identity_match_basis=r.get('identity_match_basis'),surface_sha256=r.get('surface_sha256'),archive_path=r.get('archive_path')))
 groups[group]=c
counts={k:sum(c[k] for c in groups.values()) for k in next(iter(groups.values()))};assert counts['denominator']==284 and sum(v for k,v in counts.items() if k!='denominator')==284
out=Path('/mnt/r/plastchem-euler/polymer-v1');out.mkdir(parents=True,exist_ok=True)
with (out/'results.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
v={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'counts':counts,'groups':groups,'PE':dict(collections.Counter(r['status'] for r in rows if r['polymer']=='pe')),'PE_denominator':31,'failure_modes':dict(collections.Counter(r['failure_mode'] for r in rows if r['status']=='failed'))};tmp=P/'summary.tmp';tmp.write_text(json.dumps(v,indent=2)+'\n');tmp.replace(P/'summary.json');print(json.dumps(v))
