"""Test cache identity, arithmetic and tamper rejection without thermodynamic solves."""
import ast
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name('phase10_worker.py')
FUNCTION = next(n for n in ast.parse(SOURCE.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'reused_polymers')
CODE = compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(SOURCE), 'exec')


class ReuseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'unit.json'
        self.unit = dict(id='cohort-00001', inchikey='INPUT', surface_sha256='SURFACE',
                         primary_partition='unit.json', primary_plan_sha256='PLAN')
        self.manifest = dict(polymers=[f'p{i}' for i in range(10)], controls=['s0', 's1'])
        self.rows = []
        for i, polymer in enumerate(self.manifest['polymers']):
            for convention in ['normalized', 'existing']:
                for j in range(32):
                    gp = i / 3 + (convention == 'existing') * .12
                    gs = j / 4
                    x = (gp-gs)/math.log(10)
                    self.rows.append(dict(unit='cohort-00001', inchikey='INPUT', solute_surface_sha256='SURFACE',
                        temperature_K=298.15, status='predicted', polymer=polymer, convention=convention,
                        solvent='s'+str(j), ln_gamma_polymer=gp, ln_gamma_solvent=gs,
                        polymer_volume_cm3_mol=100+i, solvent_volume_cm3_mol=30+j,
                        logP_x=x, logP_concentration=x+math.log10((100+i)/(30+j))))
        self.seal()
        env = dict(D=self.root, json=json, math=math,
                   sha=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest())
        exec(CODE, env)
        self.operation = env['reused_polymers']

    def tearDown(self): self.temp.cleanup()

    def seal(self):
        self.path.write_text(json.dumps(self.rows))
        self.path.with_suffix('.json.sha256.json').write_text(json.dumps(dict(
            signature=dict(plan_sha256='PLAN', unit='cohort-00001'),
            sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())))

    def test_reuses_every_ensemble_and_original_controls(self):
        result = self.operation(self.unit, self.manifest)
        self.assertEqual(len(result['coefficients']), 20)
        self.assertEqual(result['control_activities'], {'s0': 0., 's1': .25})

    def test_tamper_is_rejected(self):
        self.path.write_text(self.path.read_text() + ' ')
        with self.assertRaises(AssertionError): self.operation(self.unit, self.manifest)

    def test_wrong_plan_is_rejected(self):
        self.unit['primary_plan_sha256'] = 'OTHER'
        with self.assertRaises(AssertionError): self.operation(self.unit, self.manifest)

    def test_wrong_identity_is_rejected(self):
        self.unit['inchikey'] = 'OTHER'
        with self.assertRaises(AssertionError): self.operation(self.unit, self.manifest)

    def test_inconsistent_solvent_activity_is_rejected(self):
        self.rows[32]['ln_gamma_solvent'] += .1
        self.seal()
        with self.assertRaises(AssertionError): self.operation(self.unit, self.manifest)

    def test_incorrect_partition_arithmetic_is_rejected(self):
        self.rows[0]['logP_concentration'] += .01
        self.seal()
        with self.assertRaises(AssertionError): self.operation(self.unit, self.manifest)

    def test_missing_ensemble_row_is_rejected(self):
        self.rows.pop()
        self.seal()
        with self.assertRaises(AssertionError): self.operation(self.unit, self.manifest)


if __name__ == '__main__': unittest.main()
