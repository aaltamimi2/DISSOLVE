from pathlib import Path
import csv,json,hashlib,statistics,math,datetime
B=Path('/mnt/r/plastchem-euler');D=B/'post1096-octanol-2026-09-15';P=B/'pubchem-post960-2026-09-15'
rows=list(csv.DictReader((P/'cumulative-parity.csv').open()));pending=json.loads((P/'experimental-statistics.json').read_text())['pending_prediction'];audit=json.loads((D/'numerical-audit.json').read_text());added=[]
for r in pending:
 k=r['input_inchikey'];p=D/'octanol'/f'{k}.json';a=next(a for a in audit['rows'] if a['inchikey']==k);assert a['status']=='passed' and hashlib.sha256(p.read_bytes()).hexdigest()==a['result_sha256'];v=json.loads(p.read_text());r.update(predicted_logKow=v['prediction']['log10_K_concentration'],prediction_path=str(p),prediction_sha256=a['result_sha256']);r['residual_predicted_minus_measured']=r['predicted_logKow']-float(r['measured_logKow']);added.append(r)
rows+=added;assert len(rows)==len({r['input_inchikey'] for r in rows});fields=list(dict.fromkeys(k for r in rows for k in r))
with (D/'cumulative-parity.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
x=[float(r['measured_logKow']) for r in rows];y=[float(r['predicted_logKow']) for r in rows];e=[b-a for a,b in zip(x,y)];mx=statistics.mean(x);my=statistics.mean(y);slope=sum((a-mx)*(b-my) for a,b in zip(x,y))/sum((a-mx)**2 for a in x)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'n':len(rows),'unique_connectivity':len({r['input_inchikey'].split('-')[0] for r in rows}),'prediction_denominator':1111,'MAE':statistics.mean(map(abs,e)),'RMSE':math.sqrt(statistics.mean(v*v for v in e)),'bias':statistics.mean(e),'slope':slope,'intercept':my-slope*mx,'added':added,'selection':'Previous 57 measured references unchanged; pending cited PubChem point value now paired to audited prediction. No recalibration.'}
(D/'experimental-statistics.json').write_text(json.dumps(s,indent=2)+'\n');(D/'REPORT.md').write_text(f'''# Post-1096 octanol validation batch

15/15 new octanol calculations completed and passed independent numerical/provenance auditing, bringing octanol coverage to 1,111 molecules. Six available panel predictions per molecule remain incomplete relative to the requested 32; no missing value is interpolated.

The previously pending 2-phenyltridecane reference is now paired to its audited prediction. Cumulative measured comparison: n={s['n']} entries ({s['unique_connectivity']} connectivity skeletons); MAE {s['MAE']:.6f}, RMSE {s['RMSE']:.6f}, bias {s['bias']:+.6f}, slope {s['slope']:.6f}, intercept {s['intercept']:.6f}. Earlier reference selections and packages are unchanged. Source citations and numeric residuals are in `cumulative-parity.csv`. Shared-connectivity entries are not independent. Dry-octanol model conditions differ from mutually saturated experimental phases. No recalibration.

Reproduce from `/home/aaltamimi2/plastchem-euler`: `/home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/audit_post1096_octanol.py` then `python3 scripts/summarize_post1096_validation.py`. Cohort and source surface hashes are in `manifest.json`; calculation recipe and octanol reference provenance are in `provenance.json`; numerical audit evidence is in `numerical-audit.json`.

Panel worker restarted as PID 4041433 after the serial handoff. Campaign completion remains pending. Xylene identity and didecyl phthalate source remain owner questions.
''');print(json.dumps({k:v for k,v in s.items() if k!='added'},indent=2));print([(r['name'],r['measured_logKow'],r['predicted_logKow']) for r in added])
