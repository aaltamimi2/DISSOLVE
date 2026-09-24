"""Resume a failed A-9 chunk, retaining explicit per-system nonconvergence.

Original plan, numerical worker, profile code, grids and solver stay immutable.
The wrapper hashes are recorded on newly written LLE and completion records;
previous checkpoints are reused with their original bytes and signatures.
"""
import fcntl
import importlib
import json
import sys
from pathlib import Path

import phase9_entry
import phase9_failure_policy as policy


def main(planpath, index):
    planpath = Path(planpath)
    plan = json.loads(planpath.read_text())
    assert plan['worker_module'] == 'phase9_worker_cpu'
    worker = importlib.import_module(plan['worker_module'])
    folder = worker.D / plan['output'] / f'{index:04d}'
    assert folder.exists(), 'Recovery requires an original attempted chunk'
    lock = (folder / 'retry.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    clearance = json.loads((worker.D / 'production-clearance.json').read_text())
    for name, expected in clearance['file_pins'].items():
        assert worker.sha(worker.D / name) == expected, name
    provenance = dict(entry_sha256=policy.digest(__file__),
                      failure_policy_sha256=policy.digest(policy.__file__),
                      purpose='Resume exact checkpoints; retain binary-grid nonconvergence as a failed system')
    original_save = worker.save

    def save_with_provenance(path, value):
        if isinstance(value, dict) and ('execution' in value or 'exception_message' in value):
            value = dict(value, recovery_policy=provenance)
        return original_save(path, value)

    worker.save = save_with_provenance
    worker.lle_result = policy.wrap(worker.lle_result)
    return phase9_entry.main(planpath, index)


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]))
