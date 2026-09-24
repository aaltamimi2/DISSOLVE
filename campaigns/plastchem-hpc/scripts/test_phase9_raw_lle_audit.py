"""Analytic controls and adverse mutations for the saved-activity LLE audit."""
import copy
import unittest

import numpy as np

from audit_phase9_raw_lle import audit_value
from phase8_lle import solve_lle


def regular_solution(chi):
    cache = {}
    def evaluate(xs):
        xs = np.asarray(xs)
        values = np.column_stack((chi*(1-xs)**2, chi*xs**2))
        for x, row in zip(xs, values): cache[format(float(x), '.17g')] = row.tolist()
        return values
    result = solve_lle(evaluate, 100, 100)
    result.update(activities=cache, n_activity_points=len(cache))
    return result


class RawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.single = regular_solution(1.)
        cls.two = regular_solution(3.)

    def test_analytic_single_phase(self):
        result = audit_value(self.single)
        self.assertEqual(result['checked_grids'], 2)
        self.assertEqual(result['checked_ties'], 0)

    def test_analytic_coexistence(self):
        result = audit_value(self.two)
        self.assertEqual(result['checked_ties'], 2)
        self.assertLess(result['maximum_mu_residual'], 1e-7)
        a = self.two['tie_lines'][0]['x_solvent_rich']
        self.assertAlmostEqual(a, .07072018168, places=8)

    def test_false_single_phase_rejected(self):
        changed = copy.deepcopy(self.single)
        changed['activities']['0.5'][0] += .1
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_changed_endpoint_activity_rejected(self):
        changed = copy.deepcopy(self.two)
        x = changed['grid_checks'][0]['tie_lines'][0]['x_solvent_rich']
        changed['activities'][format(x, '.17g')][0] += .01
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_false_recorded_residual_rejected(self):
        changed = copy.deepcopy(self.two)
        changed['grid_checks'][0]['tie_lines'][0]['chemical_potential_residual'] = 1e-5
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_missing_grid_activity_rejected(self):
        changed = copy.deepcopy(self.single)
        del changed['activities']['0.5']
        changed['n_activity_points'] -= 1
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_nonfinite_activity_rejected(self):
        changed = copy.deepcopy(self.single)
        changed['activities']['0.5'][0] = float('nan')
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_missing_exact_uniform_coordinate_rejected(self):
        changed = copy.deepcopy(self.single)
        x = .5
        for _ in range(17): x = float(np.nextafter(x, 1.))
        changed['activities'][format(x, '.17g')] = changed['activities'].pop('0.5')
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_coordinate_outside_bound_rejected(self):
        changed = copy.deepcopy(self.single)
        x = .5
        for _ in range(64): x = float(np.nextafter(x, 1.))
        changed['activities'][format(x, '.17g')] = changed['activities'].pop('0.5')
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_extra_saved_probe_checked_for_convexity(self):
        changed = copy.deepcopy(self.single)
        x = .123456789
        changed['activities'][format(x, '.17g')] = [(1-x)**2+.1, x*x]
        changed['n_activity_points'] += 1
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_extra_saved_probe_checked_for_tangent_stability(self):
        changed = copy.deepcopy(self.two)
        x = .123456789
        changed['activities'][format(x, '.17g')] = [-10., -10.]
        changed['n_activity_points'] += 1
        with self.assertRaises(AssertionError): audit_value(changed)

    def test_unresolved_never_qualified(self):
        result = audit_value({'status': 'activity_nonconvergence'})
        self.assertEqual(result['checked_grids'], 0)
        self.assertEqual(result['checked_ties'], 0)


if __name__ == '__main__': unittest.main()
