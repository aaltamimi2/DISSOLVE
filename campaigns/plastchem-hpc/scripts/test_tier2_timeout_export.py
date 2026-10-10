"""Frozen-data regression: timeout/retry outcomes are never chemistry failures."""
import argparse,csv,hashlib,json,shutil,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(freeze,out):
 out.mkdir()
 with (out/'export.log').open('w') as log:
  subprocess.run([sys.executable,str(R/'scripts/export_tier2_thermodynamic_table.py'),'--snapshot-root',str(freeze),'--output-dir',str(out)],check=True,stdout=log,stderr=subprocess.STDOUT)
 return list(csv.DictReader((out/'partitioning-current.csv').open()))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--snapshot',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
 out=a.output.resolve();assert out.is_relative_to(Path('/mnt/r/plastchem-euler')) and not out.exists();out.mkdir(parents=True)
 baseline=run(a.snapshot/'freeze',out/'unchanged')
 assert sha(out/'unchanged/partitioning-current.csv')==sha(a.snapshot/'partitioning-current.csv')
 source=a.snapshot/'freeze';f=out/'synthetic-freeze';f.mkdir()
 for rel in ['state/tier2-v1/tier2/manifest.json','state/tier2-v1/thermodynamics/processing-ledger.json','state/thermodynamics-v1/library-registry.json','state/thermodynamics-v1/solvent-phase-review.json']:
  dest=f/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,dest)
 bundle=read(source/'captured-records.json');ledger=read(source/'state/tier2-v1/thermodynamics/processing-ledger.json')
 failures=[k for k,v in bundle.items() if read_value(v).get('status')=='failed' and k not in ledger];assert len(failures)>=4
 modes=['slurm_timeout','walltime_censored','scheduler_signal_10','other_termination']
 chosen=failures[:4]
 for key,mode in zip(chosen,modes):
  r=read_value(bundle[key]);r.update(failure_mode=mode)
  if mode=='other_termination':r['execution_outcome']='time_limit'
  else:r.pop('execution_outcome',None)
  bundle[key]=json.dumps(r)
 success=next(k for k in ledger if read_value(bundle[k])['status']=='converged')
 r=read_value(bundle[success]);r.update(execution_outcome='time_limit',failure_mode='slurm_timeout');bundle[success]=json.dumps(r)
 (f/'captured-records.json').write_text(json.dumps(bundle))
 rows=run(f,out/'timeout-cases');assert len(rows)==len(baseline)==8640
 changed=0
 for old,new in zip(baseline,rows):
  assert old['input_inchikey']==new['input_inchikey'] and old['solvent']==new['solvent']
  if new['input_inchikey'] in chosen:
   assert new['status']=='awaiting_time_limit_retry'
   assert new['log10_K_mole_fraction']==new['log10_K_concentration']==''
   assert {k:v for k,v in new.items() if k not in ['status','reason']}=={k:v for k,v in old.items() if k not in ['status','reason']}
   changed+=1
  else:assert old==new
 assert changed==128
 proof=dict(status='passed',unchanged_rows=8640,unchanged_csv_sha256=sha(out/'unchanged/partitioning-current.csv'),timeout_modes=modes,synthetic_retry_rows=changed,accepted_converged_row_not_overridden=True,all_other_rows_identical=True,snapshot=str(a.snapshot),scope='Synthetic cases only; no live scientific records or outputs modified')
 (out/'proof.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
def read_value(v):return json.loads(v)
if __name__=='__main__':main()
