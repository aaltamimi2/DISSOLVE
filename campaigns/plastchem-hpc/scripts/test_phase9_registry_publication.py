"""Exercise the observed R: rename failure without touching real returns."""
import gzip
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import collect_phase9 as collector


def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix='registry-test-',dir=collector.D) as temp:
        root=Path(temp);registry=root/'collection.json'
        registry.write_text('{"old":true}')
        value=dict(files={'example':dict(sha256='test',archive='test.tar.gz')},archives=[])
        original_replace=Path.replace;attempts=[]

        def interrupted_replace(source,dest):
            if source==registry.with_suffix('.tmp'):
                attempts.append(1)
                if len(attempts)<=2:
                    Path(dest).unlink(missing_ok=True)
                    raise PermissionError('Simulated R: destination lost during rename')
            return original_replace(source,dest)

        with patch.object(Path,'replace',interrupted_replace):
            collector.publish_registry(registry,value)
        assert json.loads(registry.read_text())==value and len(attempts)==3
        snapshots=list((root/'collection-snapshots').glob('*.json.gz'))
        assert len(snapshots)==1
        with gzip.open(snapshots[0],'rt') as f:assert json.load(f)==value
        results.append('destination-losing rename retried; registry and recovery snapshot agree')
        registry.unlink();(root/'returns').mkdir();(root/'returns/prior.tar.gz').write_bytes(b'fixture')
        with patch.object(collector,'D',root),patch.object(collector,'S',root),patch.object(collector,'run') as remote:
            try:collector.main()
            except AssertionError as exc:assert 'Registry missing' in str(exc)
            else:raise AssertionError('Missing registry was treated as an empty collection')
            remote.assert_not_called()
        results.append('missing registry with prior returns rejected before SSH')
    print(json.dumps(dict(passed=len(results),results=results),indent=2))


if __name__=='__main__':main()
