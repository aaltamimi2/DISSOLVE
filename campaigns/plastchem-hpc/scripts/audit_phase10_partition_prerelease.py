"""Audit all collected partition rows before full LLE collection is available.

Uses the unchanged independent auditor, labels partial LLE coverage explicitly,
and does not export or certify a complete release.
"""
import datetime
import json
from pathlib import Path

from audit_phase10_results import main as audit, sha

D=Path('/mnt/r/plastchem-euler/phase10-v1')


def main():
    available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines()
                       if x.startswith('MemAvailable:')))*1024
    assert available>=2.5*1024**3
    out=D/'partition-prerelease-audit';out.mkdir(exist_ok=False)
    registry=json.loads((D/'collection.json').read_text())
    counts={group:sum('/'+group+'/' in p and not p.endswith('.sha256.json') for p in registry['files'])
            for group in ['partition','lle']}
    assert counts['partition']==5830
    result=audit(require_complete=False,registry=registry)
    assert result['partition_molecules']==5830 and result['counts']['partition_rows_checked']==4547400
    assert result['counts']['primary_exact_zero_controls']==11660
    result['prerelease_scope']='All stored activity/mole-fraction partition rows and existing-convention concentration rows checked; normalized physical-volume export and full LLE/release verification still pending.'
    result['wrapper_sha256']=sha(Path(__file__))
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
