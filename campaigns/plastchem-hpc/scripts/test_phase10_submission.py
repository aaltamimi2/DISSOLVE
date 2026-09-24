"""Production cost-gate, duplicate prevention and lost-confirmation controls."""
import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).with_name('submit_phase10_production_remote.py')


class SubmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.d = self.home/'plastchem-euler/phase10-v1'
        self.d.mkdir(parents=True)
        (self.d.parent/'phase83-v1').mkdir()
        (self.d.parent/'phase9-v1').mkdir()
        self.calls = []
        self.found = False
        self.ambiguous = False
        self.sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        self.save = lambda p, v: p.write_text(json.dumps(v))
        chunks = [[dict(id=f'cohort-{i:05d}') for i in range(k*4, (k+1)*4)] for k in range(8)]
        chunks.append([dict(id=f'cohort-{i:05d}') for i in range(32,40)])
        cal = dict(chunks=chunks, output='calibration-results-v2')
        self.save(self.d/'calibration-plan-v2.json', cal)
        cp = self.sha(self.d/'calibration-plan-v2.json')
        for i, chunk in enumerate(chunks):
            p = self.d/cal['output']/f'{i:04d}';p.mkdir(parents=True)
            self.save(p/'complete.json',dict(signature=dict(plan_sha256=cp),
                unit_ids=[u['id'] for u in chunk],lle_statuses={str(j):'single_liquid_phase' for j in range(39*len(chunk))}))
            for solvent in ['water','hexane']:
                self.save(p/f'CONTROL__{solvent}-comparison.json',dict(passed=True,max_abs_ln_gamma=0.,n=len(chunk)))
        units = [dict(id=f'cohort-{i:05d}') for i in range(40,5830)]
        plan = dict(calibration_plan_sha256=cp,chunks=[units[i:i+100] for i in range(0,len(units),100)],constraint='(milan|genoa)&cpu')
        self.save(self.d/'production-plan.json',plan)
        self.gate = dict(status='passed',projected_CPU_h=100.,limit_CPU_h=510,
            calibration_molecules=40,LLE_systems=1560,exact_batch_control_comparisons=80,max_control_difference=0.,
            file_pins={n:self.sha(self.d/n) for n in ['production-plan.json','calibration-plan-v2.json']})
        self.save(self.d/'calibration-clearance.json',self.gate)
        controller = self.d.parent/'phase9-v1/phase9_throttle_remote_v5.py';controller.write_text('fixture')
        self.save(controller.parent/'active-throttle-controller.json',dict(filename=controller.name,sha256=self.sha(controller)))

    def tearDown(self): self.temp.cleanup()

    def run_command(self,args,**kwargs):
        self.calls.append(args)
        if args[0]=='squeue': out='99999_0|RUNNING|node\n' if self.found else ''
        elif args[0]=='sacct': out='99999_0|contam-phase10-production-v1|RUNNING|1|node\n' if self.found else ''
        elif args[0]=='sbatch':
            self.assertIn('--hold',args);self.assertIn('--array=0-57%27',args)
            self.found=True;out='lost confirmation' if self.ambiguous else '99999\n'
        else: raise AssertionError(args)
        return subprocess.CompletedProcess(args,0,out,'')

    def invoke(self):
        with patch.object(Path,'home',return_value=self.home),patch('subprocess.run',self.run_command),contextlib.redirect_stdout(io.StringIO()):
            namespace=dict(__name__='__main__')
            try:
                exec(compile(SOURCE.read_text(),str(SOURCE),'exec'),namespace)
            finally:
                if 'lock' in namespace:namespace['lock'].close()

    def test_new_submission_held_and_reconciled(self):
        self.invoke()
        r=json.loads((self.d/'production-submission.json').read_text())
        self.assertEqual(r['job_id'],'99999')
        self.assertEqual([c[0] for c in self.calls],['squeue','sacct','sbatch'])

    def test_existing_job_is_not_submitted_again(self):
        self.found=True;self.invoke()
        self.assertFalse(any(c[0]=='sbatch' for c in self.calls))

    def test_over_budget_blocks_before_scheduler_mutation(self):
        self.gate['projected_CPU_h']=511
        self.save(self.d/'calibration-clearance.json',self.gate)
        with self.assertRaises(AssertionError):self.invoke()
        self.assertFalse(self.calls)

    def test_unknown_unconfirmed_attempt_is_not_resubmitted(self):
        (self.d/'production-submission-unconfirmed.json').write_text('{}')
        with self.assertRaises(AssertionError):self.invoke()
        self.assertFalse(any(c[0]=='sbatch' for c in self.calls))

    def test_lost_confirmation_reconciles_without_duplicate(self):
        self.ambiguous=True
        with self.assertRaises(AssertionError):self.invoke()
        self.invoke()
        self.assertEqual(sum(c[0]=='sbatch' for c in self.calls),1)
        self.assertEqual(json.loads((self.d/'production-submission.json').read_text())['job_id'],'99999')


if __name__=='__main__':unittest.main()
