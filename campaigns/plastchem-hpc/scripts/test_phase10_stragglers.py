"""Scheduler-progress controls, including equal-duration but later-start tails."""
import unittest
from observe_phase10_production_remote import straggler_candidates

def row(index,elapsed,seals,state='RUNNING',**extra):
    return dict(chunk=index,elapsed_seconds=elapsed,LLE_seals=seals,LLE_expected=3900,
                scheduler_state=state,complete=state=='COMPLETED',**extra)

class Tests(unittest.TestCase):
    def test_late_start_with_same_total_duration_is_detected(self):
        rows=[row(i,8000,3900,'COMPLETED') for i in range(5)]
        rows += [row(i,7200,3510) for i in range(5,8)]
        rows += [row(8,4000,1950)]
        result=straggler_candidates(rows)
        self.assertEqual([r['chunk'] for r in result],[8])
        self.assertEqual(result[0]['reasons'],['remaining_time_after_late_start'])
        self.assertAlmostEqual(result[0]['projected_remaining_seconds'],4000)
        self.assertAlmostEqual(result[0]['comparison_median_remaining_seconds'],800)

    def test_original_duration_outlier_is_preserved(self):
        rows=[row(i,8000,3900,'COMPLETED') for i in range(5)]+[row(5,4000,500)]
        result=straggler_candidates(rows)
        self.assertEqual([r['chunk'] for r in result],[5])
        self.assertIn('total_duration',result[0]['reasons'])

    def test_young_failed_complete_and_handed_off_are_not_candidates(self):
        rows=[row(i,7200,3510) for i in range(3)]
        rows += [row(3,1800,1),row(4,8000,1,'FAILED'),row(5,8000,3900,'COMPLETED'),
                 row(6,8000,1,tail_handoff={'status':'paused_helpers_permitted'})]
        self.assertEqual(straggler_candidates(rows),[])

    def test_no_measurement_and_zero_progress_do_not_trigger(self):
        self.assertEqual(straggler_candidates([]),[])
        self.assertEqual(straggler_candidates([row(0,None,0),row(1,9000,0)]),[])

if __name__=='__main__':unittest.main()
