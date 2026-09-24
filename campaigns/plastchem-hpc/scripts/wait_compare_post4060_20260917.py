"""Compare only after the new serial cohort passes its complete numerical audit."""
from pathlib import Path
import time,json,subprocess,datetime
pid=820812
while Path(f'/proc/{pid}').exists():
 assert 'finalize_octanol_post4060_20260917.py' in Path(f'/proc/{pid}/cmdline').read_text()
 time.sleep(30)
p=Path('/mnt/r/plastchem-euler/octanol-post4060-2026-09-17/numerical-audit.json')
a=json.loads(p.read_text());assert a['denominator']==112 and a['passed']==112 and a['failed']==0,a
subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python','scripts/compare_post4060_20260917.py'],check=True)
Path('state/octanol-post4060-compared-20260917.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'audited_and_compared','visual_review_pending':True},indent=2)+'\n')
