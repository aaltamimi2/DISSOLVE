"""Offline archive controls: prerequisites, immutable resume and corrupt payloads."""
import contextlib
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_phase10 as collector


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'state')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'logs').mkdir()
        self.solvents=['solvent-'+str(i) for i in range(39)]
        (self.root/'manifest.json').write_text(json.dumps(dict(solvents=[dict(name=s) for s in self.solvents])))
        self.chunk=self.root/'calibration-results-v2/0000'

    def seal(self, relative, value):
        p=self.chunk/relative;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value))
        digest=hashlib.sha256(p.read_bytes()).hexdigest()
        p.with_suffix('.json.sha256.json').write_text(json.dumps(dict(sha256=digest,signature={})))
        return p

    def run_remote(self, known=None, cursor=None, scan_readers=4):
        source=collector.remote_code(known or {},cursor,scan_readers=scan_readers)
        needle="D=pathlib.Path.home()/'plastchem-euler/phase10-v1'"
        assert source.count(needle)==1
        source=source.replace(needle,'D=pathlib.Path('+repr(str(self.root))+')')
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):exec(compile(source,'test-remote','exec'),{})
        return json.loads(stream.getvalue())

    def test_prerequisites_then_partition_and_immutable_resume(self):
        partition=self.seal('partition/cohort-00000.json',[dict(unit='cohort-00000')])
        result=self.run_remote()
        self.assertEqual(result['new_files'],0)
        self.seal('polymer-reuse/cohort-00000.json',{})
        for name in self.solvents+['CONTROL__water','CONTROL__hexane']:
            self.seal('activities/'+name+'.json',dict(values=[1.]))
        result=self.run_remote()
        with tarfile.open(result['archive']) as stream:
            pins=json.load(stream.extractfile('return-pins.json'))
            self.assertIn(str(partition.relative_to(self.root)),pins)
            for name,digest in pins.items():
                self.assertEqual(hashlib.sha256(stream.extractfile(name).read()).hexdigest(),digest)
        known={name:dict(sha256=digest) for name,digest in pins.items()}
        self.assertEqual(self.run_remote(known)['new_files'],0)
        stamp=partition.with_suffix('.json.sha256.json')
        stamp.write_text(stamp.read_text()+' ')
        with self.assertRaises(AssertionError):self.run_remote(known)

    def test_payload_corruption_prevents_archive_completion(self):
        p=self.seal('lle/cohort-00000__solvent-0__RT.json',dict(status='single_liquid_phase'))
        p.write_text('{}')
        with self.assertRaises(AssertionError):self.run_remote()

    def test_failed_first_calibration_is_excluded(self):
        p=self.root/'calibration-results-v1/0000/lle/failed.json'
        p.parent.mkdir(parents=True);p.write_text('{}')
        self.assertEqual(self.run_remote()['new_files'],0)

    def test_parallel_scan_preserves_every_payload_and_cursor_resume(self):
        for i in range(37):
            self.seal(f'lle/cohort-{i:05d}__solvent-0__RT.json',dict(index=i,status='single_liquid_phase'))
        def contents(result):
            with tarfile.open(result['archive']) as archive:
                return {m.name:archive.extractfile(m).read() for m in archive}
        serial=self.run_remote(scan_readers=1)
        parallel=self.run_remote(scan_readers=4)
        self.assertEqual(contents(serial),contents(parallel))
        self.assertEqual(serial['scanned_seals'],37)
        pins=json.loads(contents(serial)['return-pins.json'])
        known={name:dict(sha256=digest) for name,digest in pins.items()}
        self.seal('lle/cohort-00037__solvent-0__RT.json',dict(index=37))
        cursor='calibration-results-v2/0000/lle/cohort-00018__solvent-0__RT.json.sha256.json'
        a=self.run_remote(known,cursor,scan_readers=1)
        b=self.run_remote(known,cursor,scan_readers=4)
        self.assertEqual(contents(a),contents(b))
        self.assertEqual(a['new_files'],2)

    def test_persisted_backoff_is_a_deferral(self):
        (self.root/'state').mkdir()
        for message in ['Backoff active: retry after 123, in 47s','Wait at least 120s before another connection attempt']:
            with patch.object(collector,'R',self.root),patch.object(collector,'require_primary'),patch.object(collector,'_run',side_effect=RuntimeError(message)):
                with self.assertRaises(collector.PrimaryBusy):collector.transport('ssh',['fixture'])

    def test_unrelated_transport_error_is_not_hidden(self):
        (self.root/'state').mkdir()
        with patch.object(collector,'R',self.root),patch.object(collector,'require_primary'),patch.object(collector,'_run',side_effect=RuntimeError('unexpected error')):
            with self.assertRaises(RuntimeError) as caught:collector.transport('ssh',['fixture'])
            self.assertNotIsInstance(caught.exception,collector.PrimaryBusy)


if __name__=='__main__':unittest.main()
