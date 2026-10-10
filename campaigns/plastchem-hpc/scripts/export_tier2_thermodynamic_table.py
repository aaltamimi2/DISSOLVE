"""Export all eligible targets and requested solvent/reference rows, including missingness."""
import argparse,collections,csv,hashlib,json,time
import os,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/tier2-v1/thermodynamics';PANEL=ROOT/'state/thermodynamics-v1';DEST=Path('/mnt/r/plastchem-euler/tier2-v1/thermodynamics')
parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true')
parser.add_argument('--snapshot-root',type=Path);parser.add_argument('--output-dir',type=Path)
args=parser.parse_args();assert not args.frozen, 'Tier2 frozen export requires a separately sealed cohort'
lock=(P/'export.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
record_root=ROOT
if args.snapshot_root:
 assert args.output_dir is not None
 record_root=args.snapshot_root.resolve();assert record_root.is_relative_to(Path('/mnt/r/plastchem-euler'))
 P=record_root/'state/tier2-v1/thermodynamics';PANEL=record_root/'state/thermodynamics-v1'
if args.output_dir:
 DEST=args.output_dir.resolve();assert DEST.is_relative_to(Path('/mnt/r/plastchem-euler'))
 DEST.mkdir(parents=True,exist_ok=True)
eligible=json.loads((record_root/'state/tier2-v1/tier2/manifest.json').read_text())['molecules'];library=json.loads((PANEL/'library-registry.json').read_text());solvents=[s for s in library['solvents'] if s['solvent_key']!='water'];ledger=json.loads((P/'processing-ledger.json').read_text()) if (P/'processing-ledger.json').exists() else {}
bundle=record_root/'captured-records.json'
captured_records=json.loads(bundle.read_text()) if args.snapshot_root and bundle.exists() else None
phase_review=json.loads((PANEL/'solvent-phase-review.json').read_text());assert phase_review['temperature_K']==298.15
phase_notes={r['solvent']:r for r in phase_review['entries']}
fields=['tier','phase_basis','solvent_phase_note','solvent_phase_source','input_inchikey','perceived_inchikey','identity_match_basis','name','orca_status','solvent','reference','status','reason','temperature_K','parameterization','log10_K_mole_fraction','log10_K_concentration','concentration_basis_status','volume_correction_log10','volume_source','observed_dilution_shift_sum_log10','solute_surface_sha256','solvent_surface_sha256','reference_surface_sha256']
fields += ['cpu_model','perceived_keys_by_engine','perception_engines_agreeing_on_perceived_key','result_sha256']
counts=collections.Counter();output=DEST/('partitioning-frozen.csv' if args.frozen else 'partitioning-current.csv');n=0;concentration_rows=0
temporary=output.with_name(output.name+f'.{os.getpid()}.tmp')
with temporary.open('w',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
 for mol in eligible:
  key=mol['inchikey'];record=record_root/'state/tier2-v1/records'/(key+'.json')
  r=json.loads(captured_records.get(key,'{}')) if captured_records is not None else (json.loads(record.read_text()) if record.exists() else {})
  data={}
  if key in ledger:
   raw=Path(ledger[key]['result_path']).read_bytes()
   assert hashlib.sha256(raw).hexdigest()==ledger[key]['result_sha256'],f'Uncommitted or changed thermodynamic record: {key}'
   data=json.loads(raw)
   assert data['input_inchikey']==key and data['solute_surface_sha256']==r.get('surface_sha256'),f'Source binding mismatch: {key}'
   assert data['config']['temperature_K']==298.15 and data['config']['parameterization']=='openCOSMORS24a'
   assert data['perceived_inchikey']==r.get('perceived_inchikey') and data['identity_match_basis']=='connectivity_first_block'

  results={v['solvent']:v for v in data.get('partitions_against_water',[])}
  for solvent in solvents:
   name=solvent['solvent_key'];row={'tier':'CHNO_500_700','input_inchikey':key,'perceived_inchikey':r.get('perceived_inchikey'),'identity_match_basis':r.get('identity_match_basis'),'name':mol['name'],'orca_status':r.get('status','not_yet_run'),'solvent':name,'reference':'water','temperature_K':298.15,'parameterization':'openCOSMORS24a','solute_surface_sha256':r.get('surface_sha256')}
   row.update(cpu_model=r.get('cpu_model',''),perceived_keys_by_engine=json.dumps(r.get('perceived_keys_by_engine',{}),sort_keys=True),perception_engines_agreeing_on_perceived_key=json.dumps(r.get('perception_engines_agreeing_on_perceived_key',[])),result_sha256=ledger.get(key,{}).get('result_sha256',''))
   if r.get('status')!='converged' and (r.get('execution_outcome')=='time_limit' or r.get('failure_mode') in ['slurm_timeout','walltime_censored','scheduler_signal_10']):
    row.update(status='awaiting_time_limit_retry',reason=r.get('failure_mode') or 'time_limit; retry required')
   elif r.get('status')=='failed':row.update(status='orca_or_preparation_failed',reason=r.get('failure_mode'))
   elif r.get('status')!='converged':row.update(status='awaiting_verified_orca')
   elif solvent['status']=='identity_unresolved':row.update(status='solvent_identity_unresolved',reason=solvent.get('reason'))
   elif solvent['status']!='ready':row.update(status='awaiting_verified_solvent',reason=solvent.get('reason',solvent['status']))
   elif name not in results:row.update(status='awaiting_thermodynamic_calculation')
   else:row.update(results[name])
   row['phase_basis']='liquid_reference'
   if name in phase_notes:
    row['solvent_phase_note']=phase_notes[name]['required_result_note'];row['solvent_phase_source']=phase_notes[name]['source_url']
   counts[row['status']]+=1
   if row['status']=='predicted' and row.get('log10_K_concentration') is not None:concentration_rows+=1
   if isinstance(row.get('volume_source'),dict):row['volume_source']=json.dumps(row['volume_source'],sort_keys=True)
   writer.writerow(row);n+=1
assert len(eligible)==270 and len(solvents)==32 and n==270*32
h=hashlib.sha256()
with temporary.open('rb') as f:
 for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
temporary.replace(output)
summary={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'status':'incomplete','tier':'CHNO_500_700','eligible_contaminants':270,'isotope_exclusions':0,'requested_nonself_solvent_pairs_per_contaminant':32,'table_rows':n,'row_status_counts':dict(counts),'phase_review_sha256':hashlib.sha256((PANEL/'solvent-phase-review.json').read_bytes()).hexdigest(),'csv_path':str(output),'csv_bytes':output.stat().st_size,'csv_sha256':h.hexdigest(),'notes':'Missing values remain empty. This export is not evidence of complete thermodynamic coverage.'}
summary['frozen']=bool(args.snapshot_root)
summary['concentration_prediction_rows']=concentration_rows
receipt=DEST/'table-export.json' if args.snapshot_root else P/'table-export.json'
temp_receipt=receipt.with_suffix('.tmp');temp_receipt.write_text(json.dumps(summary,indent=2)+'\n');temp_receipt.replace(receipt)
print(json.dumps(summary,indent=2))
