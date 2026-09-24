"""Prove bounded archives drain without losing rows or partition prerequisites."""
import contextlib
import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import collect_phase9 as collector


def main():
    checks=[]
    with tempfile.TemporaryDirectory(dir=collector.D,prefix='collection-batch-test-') as temp:
        home=Path(temp);root=home/'plastchem-euler/phase9-v1'
        root.mkdir(parents=True)
        def sealed(path,value):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value));seal=path.with_name(path.name+'.sha256.json')
            seal.write_text(json.dumps(dict(sha256=collector.sha(path))))
            return {str(p.relative_to(root)):dict(sha256=collector.sha(p),archive='fixture') for p in [path,seal]}
        def execute(known,cursor=None,**limits):
            output=io.StringIO()
            with patch.object(Path,'home',return_value=home),patch('subprocess.check_output',return_value=''),contextlib.redirect_stdout(output):
                exec(collector.remote_code(known,cursor=cursor,**limits),{})
            snap=json.loads(output.getvalue());pins={}
            if snap.get('archive'):
                assert collector.sha(snap['archive'])==snap['archive_sha256']
                with tarfile.open(snap['archive']) as t:
                    pins=json.load(t.extractfile('return-pins.json'))
                    for name,digest in pins.items():assert hashlib.sha256(t.extractfile(name).read()).hexdigest()==digest
            return snap,pins
        expected={}
        for c in range(3):
            for i in range(3):expected.update(sealed(root/f'production-results-v1/{c:04d}/lle/{i}.json',dict(index=i)))
        known={};cursor=None;archives=0
        for _ in range(20):
            snap,pins=execute(known,cursor,max_files=2)
            assert len(pins)<=2 and not set(pins)&set(known)
            known.update({p:dict(sha256=h,archive='verified') for p,h in pins.items()})
            cursor=snap['scan_cursor'];archives+=bool(pins)
            if not snap['scan_more']:break
        assert set(known)==set(expected) and archives==9
        checks.append('Nine payloads across three chunks drain in nine bounded archives with no duplicate or omission')
        first=root/'production-results-v1/0000/lle/0.json'
        byte_cap=first.stat().st_size+first.with_name(first.name+'.sha256.json').stat().st_size
        byte_known={};byte_cursor=None;byte_archives=0
        for _ in range(20):
            bounded,pins=execute(byte_known,byte_cursor,max_bytes=byte_cap)
            assert bounded['selected_payload_bytes']<=byte_cap
            assert len(pins)<=2 and not set(pins)&set(byte_known)
            byte_known.update({p:dict(sha256=h,archive='verified') for p,h in pins.items()})
            byte_cursor=bounded['scan_cursor'];byte_archives+=bool(pins)
            if not bounded['scan_more']:break
        assert set(byte_known)==set(expected) and byte_archives==9
        checks.append('Byte-boundary cuts preserve every payload/seal pair across repeated archives')
        small,old_pins=execute({},max_bytes=128*1024**2,scan_seconds=30)
        large,new_pins=execute({},max_bytes=256*1024**2,scan_seconds=45)
        assert old_pins==new_pins=={p:r['sha256'] for p,r in expected.items()}
        assert large['collection_limits']==dict(max_bytes=256*1024**2,max_files=4000,scan_seconds=45)
        checks.append('Production-sized limit configuration preserves the frozen fixture bytes and records its limits')
        snap,pins=execute(known,scan_seconds=0)
        assert not pins and snap['scan_more'] and snap['scanned_seals']==1
        following,_=execute(known,snap['scan_cursor'],scan_seconds=0)
        assert following['scan_cursor']!=snap['scan_cursor']
        checks.append('Time-limited scans advance even when all visited files are already collected')

        base=root.parent/'phase8-v1';base.mkdir()
        (base/'manifest.json').write_text(json.dumps(dict(solvents=[dict(name='a'),dict(name='z')],polymers={})))
        chunk=root/'production-results-v1/0003'
        before=sealed(chunk/'activities/solvent-z.json',dict(values=[2]))
        after=sealed(chunk/'activities/solvent-a.json',dict(values=[1]))
        partition=sealed(chunk/'partition/unit.json',dict(fixture='partition'))
        known.update(before)
        cursor='production-results-v1/0003/activities/solvent-z.json.sha256.json'
        snap,pins=execute(known,cursor)
        assert set(after)<=set(pins) and not set(partition)&set(pins)
        known.update({p:dict(sha256=h,archive='verified') for p,h in pins.items()})
        snap,pins=execute(known,snap['scan_cursor'])
        assert set(partition)<=set(pins)
        checks.append('Late-created earlier activity checkpoint collected before its dependent partition row')
        known.update({p:dict(sha256=h,archive='verified') for p,h in pins.items()})
        slow=sealed(root/'production-results-v1/0004/lle/pending.json',dict(fixture='large LLE'))
        later=root/'production-results-v1/0005'
        first=sealed(later/'activities/solvent-a.json',dict(values=[3]))
        second=sealed(later/'activities/solvent-z.json',dict(values=[4]))
        table=sealed(later/'partition/unit.json',dict(fixture='later complete partition'))
        cursor=None;returned=[]
        for _ in range(3):
            snap,pins=execute(known,cursor,max_files=2)
            assert snap['scan_order']==collector.SCAN_ORDER
            assert not set(slow)&set(pins),'LLE delayed a completed later partition table'
            returned.append(set(pins))
            known.update({p:dict(sha256=h,archive='verified') for p,h in pins.items()});cursor=snap['scan_cursor']
        assert returned==[set(first),set(second),set(table)]
        snap,pins=execute(known,cursor,max_files=2)
        assert set(slow)==set(pins),'Priority order dropped the deferred LLE payload'
        checks.append('Later chunk activities and partition precede earlier chunk LLE, which is subsequently collected with no omission')
    print(json.dumps(dict(passed=len(checks),checks=checks),indent=2))


if __name__=='__main__':main()
