"""Report an exact, observed COSMOspace failure without changing its solver.

Only the binary-grid ValueError observed in production is converted to a failure
row. Memory, I/O, identity, assertions and unrecognized numerical errors still
propagate. No retry with relaxed criteria or fabricated phase verdict is made.
"""
import functools
import hashlib
import traceback
from pathlib import Path

MESSAGE = 'COSMOspace did not converge for binary grid'
STATUS = 'activity_nonconvergence'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def failure_row(exc):
    assert type(exc) is ValueError and str(exc) == MESSAGE
    return dict(status=STATUS, failure_mode='cosmospace_binary_grid_nonconvergence',
                exception_type='ValueError', exception_message=MESSAGE,
                traceback=traceback.format_exc(), grid_checks=[], tie_lines=[],
                solute_mole_fraction_solubility=None,
                solute_wt_percent_solubility=None,
                above_15_mol_percent=None, above_15_wt_percent=None,
                activities={}, screen='not completed: initial activity grid did not converge',
                failure_policy_sha256=digest(__file__),
                qualification='No LLE prediction or threshold verdict; retained failure row')


def wrap(operation):
    @functools.wraps(operation)
    def guarded(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ValueError as exc:
            if type(exc) is not ValueError or str(exc) != MESSAGE:
                raise
            return failure_row(exc)
    return guarded


def validate_failure(row):
    """Fail closed on fabricated values, verdicts or unpinned failure handlers."""
    assert row['status'] == STATUS
    assert row['failure_policy_sha256'] == digest(__file__)
    assert row['failure_mode'] == 'cosmospace_binary_grid_nonconvergence'
    assert row['exception_type'] == 'ValueError' and row['exception_message'] == MESSAGE
    assert 'ValueError: ' + MESSAGE in row['traceback']
    assert row['grid_checks'] == [] and row['tie_lines'] == []
    for key in ['solute_mole_fraction_solubility', 'solute_wt_percent_solubility',
                'above_15_mol_percent', 'above_15_wt_percent']:
        assert row[key] is None, key

