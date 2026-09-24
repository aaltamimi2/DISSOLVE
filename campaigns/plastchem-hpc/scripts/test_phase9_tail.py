"""Ownership boundary and real process preservation controls; no COSMO compute."""
import json,multiprocessing as mp,os,signal,tempfile,time,unittest
from pathlib import Path
from phase9_tail_common import partition_ownership,keys_for,process,checked_process,save,keyname


def writer(root,units,solvents):
    root=Path(root)
    for key in keys_for(units,solvents):
        path=root/keyname(key)
        if path.exists():continue
        with (root/'calls').open('a') as f:f.write('__'.join(key)+'\n');f.flush()
        time.sleep(.03)
        save(path,{'key':key})


class TailOwnership(unittest.TestCase):
    def setUp(self):
        self.units=[{'id':f'u{i}'} for i in range(8)]
        self.solvents=[{'name':f's{i}'} for i in range(3)]
    def test_all_prefixes_exactly_once(self):
        keys=keys_for(self.units,self.solvents)
        for n in range(len(keys)):
            p=partition_ownership(self.units,self.solvents,keys[:n])
            self.assertEqual(n+sum(p['helper_systems'])+p['retained_missing'],len(keys))
            self.assertNotIn(p['retained_unit'],sum(p['groups'],[]))
    def test_nonprefix_rejected(self):
        keys=keys_for(self.units,self.solvents)
        with self.assertRaises(AssertionError):partition_ownership(self.units,self.solvents,keys[:3]+keys[4:5])
    def test_live_inflight_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);child=mp.Process(target=writer,args=(root,self.units,self.solvents));child.start()
            identity=process(child.pid)
            try:
                while not (root/'calls').exists():time.sleep(.002)
                os.kill(child.pid,signal.SIGSTOP)
                for _ in range(100):
                    if checked_process(identity)['state']=='T':break
                    time.sleep(.005)
                checked_process(identity,stopped=True)
                keys=keys_for(self.units,self.solvents)
                sealed=[k for k in keys if (root/keyname(k)).exists()]
                original_bytes={keyname(k):(root/keyname(k)).read_bytes() for k in sealed}
                p=partition_ownership(self.units,self.solvents,sealed)
                for group in p['groups']:
                    for key in keys:
                        if key[0] not in group or key in sealed:continue
                        with (root/'calls').open('a') as f:f.write('__'.join(key)+'\n')
                        save(root/keyname(key),{'key':key})
                os.kill(child.pid,signal.SIGCONT);child.join(10)
                self.assertEqual(child.exitcode,0)
                calls=(root/'calls').read_text().splitlines()
                self.assertEqual(len(calls),len(keys));self.assertEqual(len(set(calls)),len(keys))
                for name,data in original_bytes.items():self.assertEqual((root/name).read_bytes(),data)
            finally:
                if child.is_alive():os.kill(child.pid,signal.SIGCONT);child.terminate();child.join()


if __name__=='__main__':unittest.main()
