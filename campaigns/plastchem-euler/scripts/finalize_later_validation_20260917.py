from pathlib import Path
import json,time,subprocess,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1];pid=773694
while Path(f'/proc/{pid}').exists():
 assert 'finalize_octanol_followup_20260917.py' in Path(f'/proc/{pid}/cmdline').read_text();time.sleep(30)
assert json.loads((ROOT/'state/octanol-followup-finalized-20260917.json').read_text())['status']=='audited_and_compared'
subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python',str(ROOT/'scripts/compare_later_followup_20260917.py')],check=True)
D=Path('/mnt/r/plastchem-euler/octanol-followup-2026-09-17/later-validation')
for name in ['compare_later_followup_20260917.py',Path(__file__).name]:(D/name).write_bytes((ROOT/'scripts'/name).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
(ROOT/'state/later-validation-finalized-20260917.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'compared','figures_require_visual_review':True},indent=2)+'\n')
