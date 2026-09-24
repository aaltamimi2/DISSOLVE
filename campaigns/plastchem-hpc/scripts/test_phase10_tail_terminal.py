"""Reject corrupt, unfinished or misattributed terminal tail evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from collect_phase10_tail_terminal import sha,verify_capture

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'state')
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.assignment=dict(groups=[['unit0'],['unit1'],['unit2']],helper_systems=[9,9,9])
        self.write('assignment.json',self.assignment)
        self.receipt=dict(job_id='123',assignment_sha256=sha(self.root/'assignment.json'))
        self.original=dict(pid=7,start_ticks='42',uid=1000,command='worker',cwd='/owned',state='S')
        self.initial=dict(original_process=self.original)
        self.state=dict(status='original_resumed',outcome='all_helper_units_verified',
            original_process=self.original,resumed_process=self.original,
            assignment_sha256=self.receipt['assignment_sha256'],
            helper_accounting=''.join(f'123_{i}|COMPLETED|0:0\n' for i in range(3)))
        self.write('handoff-state.json',self.state)
        for i in range(3):
            self.write(f'helper-{i}-complete.json',dict(assignment_sha256=self.receipt['assignment_sha256'],
                units=self.assignment['groups'][i],files={f'file{j}':'digest' for j in range(39)},
                counts=dict(computed=9,reused=30)))
    def write(self,name,value):(self.root/name).write_text(json.dumps(value))
    def pins(self):return {p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in self.root.glob('*.json')}
    def verify(self,pins=None):return verify_capture(self.root,self.assignment,self.receipt,pins or self.pins(),self.initial)
    def test_valid_terminal_controls(self):self.assertEqual(self.verify()['status'],'original_resumed')
    def test_altered_capture_is_rejected(self):
        pins=self.pins();p=self.root/'helper-0-complete.json';p.write_text(p.read_text()+' ')
        with self.assertRaises(AssertionError):self.verify(pins)
    def test_pause_or_live_helper_is_not_terminal(self):
        self.state['status']='paused_helpers_permitted';self.write('handoff-state.json',self.state)
        with self.assertRaises(AssertionError):self.verify()
        self.state['status']='original_resumed';self.state['helper_accounting']='123_0|RUNNING|0:0\n'
        self.write('handoff-state.json',self.state)
        with self.assertRaises(AssertionError):self.verify()
    def test_wrong_group_is_rejected_even_with_valid_file_hash(self):
        p=self.root/'helper-0-complete.json';value=json.loads(p.read_text());value['units']=['unit1'];self.write(p.name,value)
        with self.assertRaises(AssertionError):self.verify()

if __name__=='__main__':unittest.main()
