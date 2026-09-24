"""Offline held submission, stale pause and lost-confirmation controls."""
import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE=Path(__file__).with_name('submit_phase10_tail_remote.py')


class TailSubmission(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'state');self.addCleanup(self.temp.cleanup)
        self.d=Path(self.temp.name)/'phase10-v1';self.d.mkdir()
        self.root=self.d/'tail-0002-v1';self.root.mkdir()
        (self.d.parent/'phase83-v1').mkdir();(self.d.parent/'phase9-v1').mkdir()
        self.save=lambda p,v:p.write_text(json.dumps(v))
        self.sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        controller=self.d.parent/'phase9-v1/phase9_throttle_remote_v5.py';controller.write_text('fixture')
        self.save(controller.parent/'active-throttle-controller.json',dict(filename=controller.name,sha256=self.sha(controller)))
        self.save(self.d/'tail-code-pins.json',{})
        self.save(self.d/'calibration-clearance.json',dict(status='passed',exact_batch_control_comparisons=80,max_control_difference=0.))
        self.save(self.root/'assignment.json',dict(groups=[['a'],['b'],['c']]))
        self.state=dict(status='paused_helpers_permitted',heartbeat_epoch=time.time(),assignment_sha256=self.sha(self.root/'assignment.json'))
        self.save(self.root/'handoff-state.json',self.state)
        self.calls=[];self.found=False;self.ambiguous=False

    def call(self,args,**kwargs):
        self.calls.append(args)
        if args[0]=='squeue':out='99999_0|name|RUNNING\n' if self.found else ''
        elif args[0]=='sacct':out='99999_0|name|RUNNING|1|node\n' if self.found else ''
        elif args[0]=='sbatch':
            self.assertIn('--hold',args);self.assertIn('--array=0-2%3',args);self.found=True
            out='lost confirmation' if self.ambiguous else '99999\n'
        elif args[0]=='scontrol':out='JobId=99999 JobState=PENDING Reason=JobHeldUser'
        else:raise AssertionError(args)
        return subprocess.CompletedProcess(args,0,out,'')

    def invoke(self):
        ns=dict(__name__='__main__',__file__=str(self.d/SOURCE.name))
        with patch('subprocess.run',self.call),patch('sys.argv',[str(SOURCE),'2']),contextlib.redirect_stdout(io.StringIO()):
            try:exec(compile(SOURCE.read_text(),str(SOURCE),'exec'),ns)
            finally:
                for key in ['lock','launchlock']:
                    if key in ns:ns[key].close()

    def test_held_submit_then_reconcile_no_duplicate(self):
        self.invoke();self.invoke()
        self.assertEqual(sum(c[0]=='sbatch' for c in self.calls),1)
        receipt=json.loads((self.d/'production-retry-tail0002-submission.json').read_text())
        self.assertEqual(receipt['purpose'],'extension_recovery')
        self.assertEqual(receipt['decision'],'existing_no_resubmission')

    def test_v6_controller_is_accepted(self):
        controller=self.d.parent/'phase9-v1/phase9_throttle_remote_v6.py';controller.write_text('reviewed-v6-fixture')
        self.save(controller.parent/'active-throttle-controller.json',dict(filename=controller.name,sha256=self.sha(controller)))
        self.invoke();self.invoke()
        self.assertEqual(sum(c[0]=='sbatch' for c in self.calls),1)

    def test_lost_confirmation_reconciles_without_duplicate(self):
        self.ambiguous=True
        with self.assertRaises(AssertionError):self.invoke()
        self.invoke();self.assertEqual(sum(c[0]=='sbatch' for c in self.calls),1)

    def test_unknown_submission_never_retried(self):
        (self.d/'production-retry-tail0002-unconfirmed.json').write_text('{}')
        with self.assertRaises(AssertionError):self.invoke()
        self.assertFalse(any(c[0]=='sbatch' for c in self.calls))

    def test_stale_pause_prevents_submission(self):
        self.state['heartbeat_epoch']-=200;self.save(self.root/'handoff-state.json',self.state)
        with self.assertRaises(AssertionError):self.invoke()
        self.assertFalse(any(c[0]=='sbatch' for c in self.calls))


if __name__=='__main__':unittest.main()
