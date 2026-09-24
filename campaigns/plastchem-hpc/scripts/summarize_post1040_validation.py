import csv,json,math,statistics,datetime
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/post1040-octanol-2026-09-15')
def read(p):return list(csv.DictReader(p.open()))
old=read(D.parent/'post960-octanol-2026-09-15/cumulative-parity.csv');new=read(D/'opera-experimental-reference-candidates.csv');pred={r['input_inchikey']:r for r in read(D/'octanol.csv')}
for r in new:
 r['predicted_logKow']=float(pred[r['input_inchikey']]['logKow']);r['residual_predicted_minus_measured']=r['predicted_logKow']-float(r['measured_logKow'])
rows=old+new;assert len({r['input_inchikey'] for r in rows})==len(rows)
fields=list(dict.fromkeys(k for r in rows for k in r))
with (D/'cumulative-parity.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
x=[float(r['measured_logKow']) for r in rows];y=[float(r['predicted_logKow']) for r in rows];e=[b-a for a,b in zip(x,y)];mx=statistics.mean(x);my=statistics.mean(y);slope=sum((a-mx)*(b-my) for a,b in zip(x,y))/sum((a-mx)**2 for a in x)
stats={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'n':len(rows),'unique_connectivity':len({r['input_inchikey'].split('-')[0] for r in rows}),'prediction_denominator':1096,'new_matches':len(new),'MAE':statistics.mean(map(abs,e)),'RMSE':math.sqrt(statistics.mean(v*v for v in e)),'bias':statistics.mean(e),'slope':slope,'intercept':my-slope*mx,'new_rows':new,'selection':'Previously selected 53 retained; two new qualified OPERA observed values. No recalibration.'}
(D/'experimental-statistics.json').write_text(json.dumps(stats,indent=2)+'\n')
p=D/'REPORT.md';text=p.read_text().replace('Experimental reference matching for this cohort remains pending.','The pinned OPERA experimental dataset matches 2/56 molecules by connectivity and CAS. PubChem and CompTox expansion for this cohort remains pending.')
text+='\n## Expanded experimental comparison\n\nCumulative n={n} entries ({unique_connectivity} distinct connectivity skeletons) among {prediction_denominator} octanol predictions: MAE {MAE:.4f}, RMSE {RMSE:.4f}, bias {bias:+.4f}; predicted-on-measured slope {slope:.4f}, intercept {intercept:.4f}. No recalibration. Earlier n=21, n=45 and n=53 packages are unchanged. Values and per-row citations are in `cumulative-parity.csv`; source qualification and retrieval provenance are in `opera-experimental-reference-candidates.csv`. Dataset-level experimental provenance is supplied by OPERA; it has no per-row experimental boolean.\n'.format(**stats)
for r in new:text+=f"\n- {r['name']}: measured {r['measured_logKow']}, predicted {r['predicted_logKow']:.6f}; {r['raw_reference_string']}.\n"
p.write_text(text);print(json.dumps({k:v for k,v in stats.items() if k!='new_rows'},indent=2));print([(r['name'],r['measured_logKow'],r['predicted_logKow']) for r in new])
