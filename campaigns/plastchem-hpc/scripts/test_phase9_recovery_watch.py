"""A recovery sweep must never duplicate a registered or still-live task."""
import contextlib,io,json,runpy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

class RecoverySweep(unittest.TestCase):
    def test_exact_failure_and_other_states(self):
        source=Path(__file__).with_name('reconcile_phase9_failures_remote.py').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'logs').mkdir()
            (d/'recovery-gate-fresh-passed.json').write_text(json.dumps(dict(status='passed',systems=2240,mismatch_count=0)))
            (d/'production-submission.json').write_text(json.dumps(dict(job_id='100')))
            (d/'production-retry-01-submission.json').write_text(json.dumps(dict(job_id='101',indices=[31])))
            for i in [31,32,34]:
                (d/f'logs/production-100_{i}.err').write_text('ValueError: COSMOspace did not converge for binary grid\n')
            operations=[]
            def run(args,**kw):
                operations.append(args)
                if args[0]=='squeue':text='100_34|RUNNING|original\n'
                elif args[0]=='sacct':text='100_31|FAILED|100|1:0\n100_32|FAILED|100|1:0\n100_33|TIMEOUT|100|0:9\n100_34|FAILED|100|1:0\n101_31|RUNNING|100|0:0\n'
                elif args[1].endswith('submit_phase9_recovery_v2_remote.py'):
                    self.assertEqual(args[-2:],['42','32']);text=json.dumps(dict(job_id='102',indices=[32]))
                elif args[1].endswith('phase9_throttle_remote_v4.py'):text=json.dumps(dict(status='verified'))
                else:raise AssertionError(args)
                return subprocess.CompletedProcess(args,0,text,'')
            output=io.StringIO()
            with patch('subprocess.run',run),contextlib.redirect_stdout(output):exec(compile(source,str(d/'reconcile_phase9_failures_remote.py'),'exec'),dict(__file__=str(d/'reconcile_phase9_failures_remote.py'),__name__='__main__'))
            result=json.loads(output.getvalue())
            self.assertEqual(len(result['submitted']),1)
            self.assertEqual(result['already_registered'],[31])
            self.assertEqual(result['unhandled'][0]['task'],'100_33')
            self.assertEqual(sum('submit_phase9_recovery_v2_remote.py' in ' '.join(x) for x in operations),1)

if __name__=='__main__':unittest.main()
