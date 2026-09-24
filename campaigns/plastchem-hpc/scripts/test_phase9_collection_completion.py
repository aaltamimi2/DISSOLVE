"""Reject premature transport completion at every required file boundary."""
import unittest
from collect_phase9 import frozen_release_collected


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.plans={'chunk-probe-results-v1':{'chunks':[[{'id':'u0'}]]},
                    'production-results-v1':{'chunks':[[{'id':'u1'},{'id':'u2'}]]}}
        self.manifest={'solvents':[{'name':'water'},{'name':'hexane'}],
                       'polymers':{'pe':[{'entry_id':'pe1'},{'entry_id':'pe2'}]}}
        self.files={}
        for root,units in [('chunk-probe-results-v1',['u0']),('production-results-v1',['u1','u2'])]:
            prefix=root+'/0000/';self.files[prefix+'complete.json']={}
            payloads=[prefix+'activities/'+s+'.json' for s in ['solvent-water','solvent-hexane','polymer-pe1','polymer-pe2']]
            payloads += [prefix+'partition/'+u+'.json' for u in units]
            payloads += [prefix+'lle/'+u+'__'+s+'__'+r+'.json' for u in units for s in ['water','hexane'] for r in ['RT','high']]
            for p in payloads:self.files[p]={};self.files[p+'.sha256.json']={}

    def test_complete_frozen_set_without_old_diagnostics(self):
        self.assertTrue(frozen_release_collected(self.files,self.plans,self.manifest))

    def test_every_single_missing_payload_seal_or_footer_prevents_exit(self):
        for p in self.files:
            partial=dict(self.files);del partial[p]
            with self.subTest(path=p):self.assertFalse(frozen_release_collected(partial,self.plans,self.manifest))

    def test_unrelated_or_extra_files_cannot_fill_missing_coverage(self):
        missing='production-results-v1/0000/lle/u2__water__high.json'
        partial=dict(self.files);del partial[missing]
        partial['gate-results-v1/0000/lle/u2__water__high.json']={}
        partial['production-results-v1/0000/lle/not-frozen__water__high.json']={}
        self.assertFalse(frozen_release_collected(partial,self.plans,self.manifest))

    def test_empty_plan_is_not_a_release(self):
        self.assertFalse(frozen_release_collected({}, {}, self.manifest))
        self.assertFalse(frozen_release_collected({}, {'production-results-v1':{'chunks':[]}}, self.manifest))


if __name__=='__main__':unittest.main()
