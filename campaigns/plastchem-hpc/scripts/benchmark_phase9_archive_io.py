"""Compare serial/four-reader collection on the same completed LLE payloads.

No jobs are submitted and no checkpoint/collection registry is changed. Every
returned archive and member is verified locally; all copies stay in bulk.
"""
import datetime
import hashlib
import json
import tarfile
from pathlib import Path
from collect_phase9 import remote_code, sha
from euler_transport import run

D = Path('/mnt/r/plastchem-euler/phase9-v1')


def main():
    out = D / ('archive-io-benchmark-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out.mkdir()
    comparisons = []
    reference = None
    for index, readers in enumerate([1, 4, 4, 1]):
        code = remote_code({}, cursor='production-results-v1/0057/lle/__begin__.json.sha256.json',
                           max_files=64, archive_readers=readers, scan_readers=readers)
        response = run('ssh', ['euler', 'python3 -'], input=code, text=True, capture_output=True, timeout=180)
        assert response.returncode == 0, response.stderr
        snap = json.loads(response.stdout)
        (out / f'{index}-remote.json').write_text(json.dumps(snap, indent=2) + '\n')
        archive = out / f'{index}.tar.gz'
        response = run('scp', ['euler:' + snap['archive'], str(archive)], text=True, capture_output=True, timeout=180)
        assert response.returncode == 0, response.stderr
        assert sha(archive) == snap['archive_sha256']
        with tarfile.open(archive, 'r|gz') as tar:
            members = iter(tar)
            first = next(members)
            assert first.name == 'return-pins.json'
            pins = json.load(tar.extractfile(first))
            seen = {}
            order = []
            for member in members:
                assert member.isfile() and member.name in pins and member.name not in seen
                digest = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                assert digest == pins[member.name]
                seen[member.name] = digest
                order.append(member.name)
            assert seen == pins and order == sorted(pins)
        # Other chunks may finish during the benchmark. Their completion footers
        # are checked above but excluded from the frozen LLE comparison below.
        payloads = {k:v for k,v in pins.items() if '/lle/' in k}
        assert len(payloads) == 64
        assert all(k.startswith('production-results-v1/0057/lle/') for k in payloads)
        if reference is None: reference = payloads
        assert payloads == reference, 'Reader count changed archived checkpoint bytes'
        comparisons.append(dict(run=index, readers=readers, scan_readers=snap['scan_readers'], LLE_systems=32,
                                scan_seconds=snap['scan_seconds'], archive_seconds=snap['archive_seconds'],
                                archive_bytes=snap['archive_bytes'], peak_rss_kib=snap['peak_rss_kib'],
                                checkpoint_bytes_identical=True, all_archive_members_verified=True))
        (out / 'progress.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status='passed',
                  runs=comparisons, path=str(out), matched_checkpoint_files=len(reference),
                  scope='Same 32 completed systems, reader order 1/4/4/1. No scientific calculation, checkpoint mutation or registry publication. Small-batch I/O comparison, not a full-transfer forecast.')
    (D / 'archive-io-benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
