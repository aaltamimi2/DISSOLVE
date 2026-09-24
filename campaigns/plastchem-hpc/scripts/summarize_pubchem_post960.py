from pathlib import Path
import csv,json,math,statistics,hashlib,datetime
B=Path('/mnt/r/plastchem-euler');D=B/'pubchem-post960-2026-09-15'
def read(p):return list(csv.DictReader(p.open()))
rows=read(B/'post1040-octanol-2026-09-15/cumulative-parity.csv');old={r['input_inchikey'] for r in rows};added=[];pending=[]
for r in read(D/'pubchem-measured-validation/best-measured-logKow.csv'):
 k=r['input_inchikey']
 if k in old:continue
 paths=[B/x/'octanol'/f'{k}.json' for x in ['post960-octanol-2026-09-15','post1040-octanol-2026-09-15']];paths=[p for p in paths if p.exists()]
 if not paths:pending.append(r);continue
 assert len(paths)==1;p=paths[0];v=json.loads(p.read_text());audit=json.loads((p.parent.parent/'numerical-audit.json').read_text());a=next(a for a in audit['rows'] if a['inchikey']==k);assert a['status']=='passed' and a['result_sha256']==hashlib.sha256(p.read_bytes()).hexdigest()
 r.update(predicted_logKow=v['prediction']['log10_K_concentration'],prediction_path=str(p),prediction_sha256=a['result_sha256']);r['residual_predicted_minus_measured']=r['predicted_logKow']-float(r['measured_logKow']);added.append(r)
rows+=added
fields=list(dict.fromkeys(k for r in rows for k in r))
with (D/'cumulative-parity.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
x=[float(r['measured_logKow']) for r in rows];y=[float(r['predicted_logKow']) for r in rows];e=[b-a for a,b in zip(x,y)];mx=statistics.mean(x);my=statistics.mean(y);slope=sum((a-mx)*(b-my) for a,b in zip(x,y))/sum((a-mx)**2 for a in x)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'n':len(rows),'unique_connectivity':len({r['input_inchikey'].split('-')[0] for r in rows}),'prediction_denominator':1096,'MAE':statistics.mean(map(abs,e)),'RMSE':math.sqrt(statistics.mean(v*v for v in e)),'bias':statistics.mean(e),'slope':slope,'intercept':my-slope*mx,'added':added,'pending_prediction':pending,'selection':'Previous 55 references unchanged; new cited PubChem measured point values only. No recalibration.'}
(D/'experimental-statistics.json').write_text(json.dumps(s,indent=2)+'\n')
(D/'REPORT.md').write_text(f'''# Additional PubChem validation

All 143 pinned newly processed molecules were queried: 130 had no LogP section, 13 had one. Qualification retained 14 cited observations across 10 molecules and excluded 10 observations. Seven already occur in the previous 55-entry comparison; their selected references remain unchanged, with alternate observations preserved in the candidate CSV.

Two new entries have hash-verified, numerically audited octanol predictions: heptadecane and cetyl alcohol. The cited 2-phenyltridecane value remains pending its octanol prediction; it is excluded from statistics, without substitution.

Cumulative n={s['n']} entries ({s['unique_connectivity']} connectivity skeletons) among 1,096 octanol predictions: MAE {s['MAE']:.6f}, RMSE {s['RMSE']:.6f}, bias {s['bias']:+.6f}, slope {s['slope']:.6f}, intercept {s['intercept']:.6f}. No recalibration. Source citations, URLs, retrieval times and per-row residuals are in `cumulative-parity.csv`. Shared-connectivity entries are not independent; dry-octanol calculations are not identical experimental conditions. Earlier packages remain unchanged.

Reproduce from `/home/aaltamimi2/plastchem-euler`: `python3 scripts/qualify_post960_pubchem.py` then `python3 scripts/summarize_pubchem_post960.py`. Raw responses and digest receipts are in `reference-sources/`; rejected observations are explicitly recorded in `pubchem-measured-validation/`.

Campaign and full solvent-panel completion remain pending. Xylene identity and didecyl phthalate source remain owner questions.
''')
print(json.dumps({k:v for k,v in s.items() if k not in ['added','pending_prediction']},indent=2))
