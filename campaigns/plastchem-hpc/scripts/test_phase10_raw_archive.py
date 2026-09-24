"""Extension raw-archive ownership, digest and resumability controls."""
import contextlib
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit_phase10_raw_lle as audit


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.d=root/'phase10-v1';self.d.mkdir();self.out=self.d/'raw-lle-audit-v1'
        p=root/'phase9-v1';p.mkdir();(p/'raw-lle-audit-v2').mkdir();release=root/'promotion-v1';release.mkdir()
        (release/'manifest.json').write_text('{}')
        self.save=lambda p,x:p.write_text(json.dumps(x))
        self.save(p/'delivery-verification.json',dict(status='complete_delivery_verified',manifest_sha256=audit.sha(release/'manifest.json')))
        self.save(p/'raw-lle-audit-v2/summary.json',dict(status='complete'))
        self.save(self.d/'manifest.json',dict(solvents=[dict(name='s'+str(i)) for i in range(39)]))
        self.registry=dict(files={},archives=[])

    def archive(self,index,entries):
        path=self.d/(str(index)+'.tar.gz');encoded={k:json.dumps(v).encode() for k,v in entries.items()}
        pins={k:hashlib.sha256(v).hexdigest() for k,v in encoded.items()}
        with tarfile.open(path,'w:gz') as t:
            for k,v in [('return-pins.json',json.dumps(pins).encode()),*encoded.items()]:
                m=tarfile.TarInfo(k);m.size=len(v);t.addfile(m,io.BytesIO(v))
        self.registry['archives'].append(dict(path=str(path),sha256=audit.sha(path)))
        self.registry['files'].update({k:dict(sha256=v) for k,v in pins.items()})
        self.save(self.d/'collection.json',self.registry)

    def invoke(self):
        with patch.object(audit,'D',self.d),patch.object(audit,'OUT',self.out),contextlib.redirect_stdout(io.StringIO()):audit.main()
        return json.loads((self.out/'summary.json').read_text())

    def test_new_roots_reuse_and_old_attempt_excluded(self):
        row=dict(status='activity_nonconvergence',unit='cohort-00000',solvent='s0',regime='RT')
        self.archive(0,{'calibration-results-v2/0000/lle/a.json':row,
                        'calibration-results-v1/0000/lle/old.json':row})
        first=self.invoke();self.assertEqual(first['systems'],1);self.assertEqual(first['denominator'],227370)
        self.assertEqual(first['status'],'passed_for_collected_subset')
        second=self.invoke();self.assertEqual(second['archive_audit_receipts'],first['archive_audit_receipts'])
        self.archive(1,{'production-results-v1/0000/lle/b.json':dict(row,unit='cohort-00001')})
        self.assertEqual(self.invoke()['systems'],2)

    def test_duplicate_ownership_rejected_across_chunks(self):
        row=dict(status='activity_nonconvergence',unit='cohort-00000',solvent='s0',regime='RT')
        self.archive(0,{'calibration-results-v2/0000/lle/a.json':row})
        self.archive(1,{'production-results-v1/0000/lle/b.json':row})
        with self.assertRaises(AssertionError):self.invoke()

    def test_corrupted_archive_rejected(self):
        self.archive(0,{'production-results-v1/0000/lle/a.json':dict(status='activity_nonconvergence',unit='cohort-00000',solvent='s0',regime='RT')})
        with (self.d/'0.tar.gz').open('ab') as f:f.write(b'changed')
        with self.assertRaises(AssertionError):self.invoke()

    def test_primary_audit_priority_gate(self):
        p=self.d.parent/'phase9-v1/raw-lle-audit-v2/summary.json';self.save(p,dict(status='passed_for_collected_subset'))
        with self.assertRaises(AssertionError):self.invoke()
        self.assertFalse(self.out.exists())


if __name__=='__main__':unittest.main()
