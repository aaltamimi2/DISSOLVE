"""Replay every validated gate outcome through the identity-preserving wrapper.

This verifies the new failure boundary on real gate outputs. It is NOT a fresh
COSMOspace/LLE solve: the numerical worker and solver are byte-identical, and the
wrapper returns every normal result by identity. Report that distinction.
"""
import datetime
import hashlib
import json
from pathlib import Path

from analyze_phase9_gate import collected_records
import phase9_failure_policy as policy

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase9-v1')


def main():
    clearance=json.loads((D/'production-clearance.json').read_text())
    for name,digest in clearance['file_pins'].items():
        assert policy.digest(D/name)==digest,name
    receipt=json.loads((D/'production-retry-01-submission.json').read_text())
    for name in ['phase9_failure_policy.py','phase9_retry_entry.py']:
        assert policy.digest(R/'scripts'/name)==receipt['recovery_code_pins'][name]
    plan=json.loads((D/'gate-plan.json').read_text())
    source_by_unit={u['id']:u['reference'] for units in plan['chunks'] for u in units}
    counts={};keys=set();digests=hashlib.sha256()
    for path,value in collected_records('gate-results-v1'):
        if '/lle/' not in path:continue
        key=(value['unit'],value['solvent'],value['regime'])
        assert key not in keys;keys.add(key)
        before=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        returned=policy.wrap(lambda:value)()
        assert returned is value
        after=json.dumps(returned,sort_keys=True,separators=(',',':')).encode()
        assert before==after
        for field in ['status','above_15_mol_percent','above_15_wt_percent','grid_checks']:
            assert value.get(field)==returned.get(field)
        source=source_by_unit[value['unit']];counts[source]=counts.get(source,0)+1
        digests.update(path.encode()+b'\0'+hashlib.sha256(before).digest())
    assert len(keys)==2240 and counts['calibration']==1728
    assert sum(n for k,n in counts.items() if k!='calibration')==512
    out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='passed_output_boundary_replay',
             systems=len(keys),counts=counts,all_statuses_and_both_verdicts_identical=True,
             normal_result_bytes_identical=True,failure_policy_sha256=policy.digest(policy.__file__),
             retry_entry_sha256=policy.digest(R/'scripts/phase9_retry_entry.py'),
             gate_comparison_sha256=policy.digest(D/'gate-comparison-final.json'),
             ordered_source_outcomes_sha256=digests.hexdigest(),
             scope='Every already validated gate LLE outcome is replayed through the new exception boundary and returned byte-identically by object identity. Original numerical file pins unchanged. No fresh thermodynamic solve; this does not extend experimental validation.',
             timing_note='Recovery 68675_31 launched under prior authorization before the new full-gate-proof note was observed; this evidence was produced afterward and is not presented as a pre-launch test.')
    (D/'recovery-gate-boundary-replay.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))


if __name__=='__main__':main()
