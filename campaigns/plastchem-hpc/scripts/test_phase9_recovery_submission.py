"""Exercise reconciliation/hold boundaries without contacting the scheduler."""
import contextlib
import io
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from phase9_failure_policy import digest

SOURCE = Path(__file__).with_name('submit_phase9_recovery_remote.py')


def main():
    outcomes = []
    with tempfile.TemporaryDirectory(dir='/mnt/r/plastchem-euler/phase9-v1', prefix='recovery-submit-test-') as temp:
        home = Path(temp); root = home/'plastchem-euler/phase9-v1'
        root.mkdir(parents=True); (root.parent/'phase83-v1').mkdir()
        (root/'logs').mkdir()
        controller = root/'phase9_throttle_remote_v3.py'; controller.write_text('fixture')
        values = {
            'active-throttle-controller.json': dict(filename=controller.name, sha256=digest(controller)),
            'recovery-code-pins.json': {controller.name:digest(controller)},
            'production-clearance.json': {'file_pins':{}},
            'production-plan.json': dict(chunks=[[] for _ in range(32)], output='production-results-v1', constraint='(milan|genoa)&cpu'),
            'production-submission.json': {'job_id':'68503'},
        }
        for name, value in values.items(): (root/name).write_text(json.dumps(value))
        error = root/'logs/production-68503_31.err'
        error.write_text('ValueError: COSMOspace did not converge for binary grid\n')
        calls = []; state = dict(found=False, original_running=False)

        def run(args, **kwargs):
            calls.append(args)
            text = ''
            if args[0] == 'sbatch': text = '99999\n'
            elif args[:3] == ['scontrol','show','job']:
                text = 'JobId=99999 JobState=PENDING Reason=JobHeldUser ArrayTaskThrottle=1\n'
            elif args[0] == 'squeue':
                if '--name=contam-phase9-retry-01' in args:
                    if state['found']: text = '99999_31|contam-phase9-retry-01|PENDING\n'
                elif state['original_running']: text = '68503_31|RUNNING\n'
            elif args[0] == 'sacct' and '--name=contam-phase9-retry-01' not in args:
                text = '68503_31|FAILED|1616|1:0\n'
            return subprocess.CompletedProcess(args, 0, text, '')

        def execute():
            output = io.StringIO()
            with patch.object(Path, 'home', return_value=home), \
                 patch('sys.argv', [str(SOURCE),'01','31']), \
                 patch('subprocess.run', run), contextlib.redirect_stdout(output):
                namespace = {}
                try:
                    exec(compile(SOURCE.read_text(),str(SOURCE),'exec'), namespace)
                finally:
                    if 'lock' in namespace: namespace['lock'].close()
            return json.loads(output.getvalue())

        state['original_running'] = True
        try: execute()
        except AssertionError: pass
        else: raise AssertionError('Original task was still active')
        assert not any(c[0]=='sbatch' for c in calls)
        outcomes.append('Active original task rejected before submission')
        state['original_running'] = False
        saved = error.read_text(); error.write_text('unrecognized failure')
        try: execute()
        except AssertionError: pass
        else: raise AssertionError('Unknown failure accepted')
        assert not any(c[0]=='sbatch' for c in calls)
        outcomes.append('Unrecognized failure rejected before submission')
        error.write_text(saved)
        receipt = execute(); submits = [c for c in calls if c[0]=='sbatch']
        assert len(submits)==1 and '--hold' in submits[0] and '--array=31%1' in submits[0]
        assert receipt['job_id']=='99999'
        assert not any(c[:2]==['scontrol','release'] for c in calls)
        outcomes.append('Reconciled failure submitted exactly once, held, with original chunk index')
        state['found'] = True
        assert execute()['decision']=='existing_no_resubmission'
        assert len([c for c in calls if c[0]=='sbatch'])==1
        outcomes.append('Existing retry reconciled without duplicate submission')
        state['found'] = False
        try: execute()
        except AssertionError: pass
        else: raise AssertionError('Missing accounting caused blind resubmission')
        assert len([c for c in calls if c[0]=='sbatch'])==1
        outcomes.append('Receipt/accounting disagreement rejected without blind retry')
    print(json.dumps(dict(passed=len(outcomes), outcomes=outcomes),indent=2))


if __name__=='__main__': main()
