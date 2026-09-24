"""Negative readiness controls; tests never call the builder or scheduler."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import watch_phase9_release as watcher


class ReleaseGuards(unittest.TestCase):
    def test_both_counts_required(self):
        full = dict(watcher.CHECKS)
        self.assertTrue(watcher.ready_counts(full, full))
        for field in watcher.CHECKS:
            with self.subTest(field=field):
                partial = dict(full, **{field: full[field] - 1})
                self.assertFalse(watcher.ready_counts(partial, full))
                self.assertFalse(watcher.ready_counts(full, partial))
        self.assertFalse(watcher.ready_counts(full, {}))

    def test_all_chunk_footers_required(self):
        plans = {'production-results-v1': {'chunks': [[], []]},
                 'chunk-probe-results-v1': {'chunks': [[]]}}
        expected = {'production-results-v1/0000/complete.json',
                    'production-results-v1/0001/complete.json',
                    'chunk-probe-results-v1/0000/complete.json'}
        self.assertEqual(watcher.expected_footers(plans), expected)
        watcher.require_footers({'files': {key: {} for key in expected}}, plans)
        for key in expected:
            with self.subTest(missing=key), self.assertRaises(AssertionError):
                watcher.require_footers({'files': {k: {} for k in expected - {key}}}, plans)

    def test_pending_running_and_completing_all_block(self):
        ids = {'100', '101', '102'}
        for job in ids:
            for state in ['PENDING', 'RUNNING', 'COMPLETING']:
                with self.subTest(job=job, state=state):
                    self.assertFalse(watcher.no_active_activity_jobs(
                        {'queue': f'{job}_7|{state}|fixture'}, ids))
        self.assertTrue(watcher.no_active_activity_jobs({'queue': '1100_7|RUNNING|unrelated'}, ids))
        self.assertTrue(watcher.no_active_activity_jobs({'queue': ''}, ids))

    def test_changed_charter_or_checker_stops(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(watcher, 'R', Path(directory)):
            path = Path(directory) / 'CHARTER.txt'
            path.write_text('frozen instruction\n')
            pins = {'CHARTER.txt': watcher.sha(path)}
            watcher.require_pins(pins)
            path.write_text('replacement instruction\n')
            with self.assertRaises(AssertionError): watcher.require_pins(pins)


if __name__ == '__main__': unittest.main()
