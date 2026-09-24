"""Offline measured-cost gate controls; no Euler connection or submission."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import finalize_phase10_calibration as gate


class GateTests(unittest.TestCase):
    def fixture(self, lle_seconds=1., omit_task=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        d=Path(tmp.name);r=d/'workspace';r.mkdir();(r/'CHARTER.txt').write_text('test-only')
        units=[f'cohort-{i:05d}' for i in range(40)]
        sizes=[7,4,4,6,4,4,3,3,5];chunks=[];offset=0
        for size in sizes:
            chunks.append([dict(id=u) for u in units[offset:offset+size]]);offset+=size
        def save(name,value): (d/name).write_text(json.dumps(value))
        cal=dict(chunks=chunks,subbatch=10);save('calibration-plan-v2.json',cal)
        remaining=[dict(id=f'cohort-{i:05d}',control_original_batch=[dict(surface_sha256=str(i))]) for i in range(40,5830)]
        save('production-plan.json',dict(chunks=[remaining[i:i+100] for i in range(0,len(remaining),100)],
            reused_calibration_units=units,calibration_plan_sha256=gate.sha(d/'calibration-plan-v2.json')))
        solvents=[dict(name=str(i)) for i in range(39)]
        population={str(i):600 for i in range(9)};population['8']+=430
        save('manifest.json',dict(solvents=solvents,population_strata=population))
        observed=[];timings=[]
        for i,c in enumerate(chunks):
            observed.append(dict(LLE_seals=39*len(c),control_comparisons=[dict(n=len(c),passed=True,max_abs_ln_gamma=0.)]*2,
                footer=dict(peak_rss_kib=100000,parse_seconds=1.,parse_count=50,
                    polymer_reuse_seconds=1.,partition_seconds=1.,unit_ids=[u['id'] for u in c],wall_seconds=40.)))
            timings += [dict(unit=u['id'],solvent=s['name'],stratum=str(i),wall_seconds=lle_seconds,
                             status='single_liquid_phase',cpu_model='test-only') for u in c for s in solvents]
        save('calibration-v2-observation.json',dict(status='measurement_complete_review_required',failures=[],
            completed_chunks=9,chunks=observed,timing_evidence=dict(timings=timings,plan_sha256=gate.sha(d/'calibration-plan-v2.json')),
            job_id='1',accounting='\n'.join(f'1_{i}|COMPLETED|45|' for i in range(8 if omit_task else 9))))
        for name in ['worker-pins-v2.json','phase10_worker_v2.py','phase9_worker_cpu.py','phase9_profiles.py','phase9_grid.py','phase9_failure_policy.py']:
            (d/name).write_text('test-only')
        return d,r

    def evaluate(self, d, r):
        with patch.object(gate,'D',d),patch.object(gate,'R',r),contextlib.redirect_stdout(io.StringIO()):gate.main()
        return json.loads((d/'calibration-clearance.json').read_text())

    def test_complete_low_cost_passes_and_components_reconcile(self):
        d,r=self.fixture();result=self.evaluate(d,r)
        self.assertEqual(result['status'],'passed')
        costs=result['costs_CPU_h'];cal=costs['all_calibration_allocated_including_failed_first_attempt']
        self.assertAlmostEqual(result['projected_CPU_h'],1.25*(sum(costs.values())-cal)+cal)
        self.assertEqual(result['production_units']+result['reused_units'],5830)

    def test_high_measured_cost_stops(self):
        d,r=self.fixture(lle_seconds=100.)
        self.assertEqual(self.evaluate(d,r)['status'],'above_cost_gate_stop')

    def test_missing_scheduler_task_cannot_pass(self):
        d,r=self.fixture(omit_task=True)
        with self.assertRaises(AssertionError):self.evaluate(d,r)
        self.assertFalse((d/'calibration-clearance.json').exists())


if __name__=='__main__':unittest.main()
