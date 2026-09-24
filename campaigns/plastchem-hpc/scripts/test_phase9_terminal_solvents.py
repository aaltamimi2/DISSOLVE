"""Terminal cache must reject missing/corrupt records and stale array identity."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
import collect_phase9_solvents as collector


class TerminalRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = collector.D
        collector.D = self.root = Path(self.tmp.name)
        self.keys = ['synthetic-%03d' % i for i in range(69)]
        self.known = {}
        for key in self.keys:
            path = self.root / 'results' / key / 'verified-result.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(status='converged', slurm_state='COMPLETED')))
            self.known[key] = dict(status='converged', result_sha256=collector.sha(path))
        self.put('solvent_library/manifest.json', dict(molecules=[dict(inchikey=k) for k in self.keys]))
        self.put('submission.json', dict(job_id='123'))
        self.put('latest-snapshot.json', dict(job_id='123'))
        self.summary = dict(status='terminal', denominator=69, terminal_verified=69,
                            running=0, pending=0, states=dict(converged=69))
        self.put('summary.json', self.summary)

    def tearDown(self):
        collector.D = self.old
        self.tmp.cleanup()

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps(value))

    def test_complete(self):
        self.assertEqual(collector.terminal_snapshot(self.known), self.summary)

    def test_altered_record(self):
        self.put('results/' + self.keys[0] + '/verified-result.json', dict(status='failed'))
        with self.assertRaises(AssertionError): collector.terminal_snapshot(self.known)

    def test_missing_record(self):
        (self.root / 'results' / self.keys[0] / 'verified-result.json').unlink()
        with self.assertRaises(FileNotFoundError): collector.terminal_snapshot(self.known)

    def test_wrong_roster(self):
        known = copy.deepcopy(self.known)
        known['unexpected'] = known.pop(self.keys[0])
        with self.assertRaises(AssertionError): collector.terminal_snapshot(known)

    def test_new_array_requires_scheduler(self):
        self.put('submission.json', dict(job_id='456'))
        self.assertIsNone(collector.terminal_snapshot(self.known))

    def test_incomplete_requires_scheduler(self):
        self.put('summary.json', dict(self.summary, status='in_progress'))
        self.assertIsNone(collector.terminal_snapshot(self.known))

    def test_running_record_rejected(self):
        path = 'results/' + self.keys[0] + '/verified-result.json'
        self.put(path, dict(status='converged', slurm_state='RUNNING'))
        self.known[self.keys[0]]['result_sha256'] = collector.sha(self.root / path)
        with self.assertRaises(AssertionError): collector.terminal_snapshot(self.known)


if __name__ == '__main__': unittest.main()
