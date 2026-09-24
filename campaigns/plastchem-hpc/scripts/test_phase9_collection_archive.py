"""Round-trip and corruption controls for the single-read remote archiver."""
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
    results = []
    with tempfile.TemporaryDirectory(dir=collector.D, prefix='collection-test-') as temp:
        home = Path(temp)
        root = home / 'plastchem-euler/phase9-v1'
        payload = root / 'production-results-v1/0000/lle/example.json'
        payload.parent.mkdir(parents=True)
        raw = b'{"activities":{"0.5":[1,2]},"status":"fixture"}\n'
        payload.write_bytes(raw)
        seal = payload.with_name(payload.name + '.sha256.json')
        seal.write_text(json.dumps({'sha256': hashlib.sha256(raw).hexdigest()}))
        original_read = Path.read_bytes
        reads = []

        def counted_read(path):
            if path == payload:
                reads.append(path)
            return original_read(path)

        def execute(known):
            output = io.StringIO()
            with patch.object(Path, 'home', return_value=home), \
                    patch('subprocess.check_output', return_value=''), \
                    patch.object(Path, 'read_bytes', counted_read), \
                    contextlib.redirect_stdout(output):
                exec(collector.remote_code(known), {})
            return json.loads(output.getvalue())

        snap = execute({})
        assert len(reads) == 1, reads
        archive = Path(snap['archive'])
        assert collector.sha(archive) == snap['archive_sha256']
        with tarfile.open(archive) as tar:
            pins = json.load(tar.extractfile('return-pins.json'))
            assert set(pins) == {str(p.relative_to(root)) for p in [payload, seal]}
            for name, digest in pins.items():
                assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == digest
            assert tar.extractfile(str(payload.relative_to(root))).read() == raw
        results.append('One payload read; archived bytes and all manifest digests match')
        known = {name: dict(sha256=digest, archive=archive.name) for name, digest in pins.items()}
        reads.clear()
        assert execute(known)['new_files'] == 0 and not reads
        results.append('Verified known payload skipped; seal still rechecked')
        payload.write_bytes(raw + b'corrupt')
        try:
            execute({})
        except AssertionError:
            results.append('Corrupt payload rejected before any success receipt')
        else:
            raise AssertionError('Corrupt payload accepted')
        payload.write_bytes(raw)
        seal.write_text(json.dumps({'sha256': '0' * 64}))
        try:
            execute(known)
        except AssertionError:
            results.append('Changed known seal rejected')
        else:
            raise AssertionError('Changed seal accepted')
    print(json.dumps(dict(passed=len(results), results=results), indent=2))


if __name__ == '__main__':
    main()
