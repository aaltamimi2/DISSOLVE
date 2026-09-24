"""Read-only A-9 full-roster source checks; no DFT, solve, or product writes.

Licensed source-derived metadata stay on bulk storage, never in git. Coordinate
agreement proves source-pair consistency, not an independent product/CAS match.
One subprocess handles one molecule at a time, with bounded time and memory.
"""
import collections
import datetime
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import time

for var in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[var] = '1'

D = Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(row):
    from rdkit import Chem, RDLogger
    from rdkit.Chem import rdDetermineBonds
    from opencosmorspy.input_parsers import SigmaProfileParser
    RDLogger.DisableLog('rdApp.warning')
    started = time.monotonic()
    result = dict(solvent_key=row['solvent_key'], status='unexamined')
    try:
        candidates = []
        for name in row['candidate_files']:
            p = Path(name); energy = p.with_suffix('.energy')
            assert p.is_file() and energy.is_file(), 'Missing source surface/energy pair'
            text = energy.read_text()
            match = re.search(r'ENERGY=([-+\d.Ee]+);', text)
            assert match, 'Missing recorded conformer energy'
            candidates.append((float(match.group(1)), str(p), str(energy)))
        if not candidates:
            result['status'] = 'missing_exact_name_source'
            return result
        e, surface, energy = min(candidates)
        result.update(conformer_count=len(candidates), source_cosmo=surface,
                      source_cosmo_sha256=sha(surface), source_energy=energy,
                      source_energy_sha256=sha(energy), lowest_energy_hartree=e)
        parsed = SigmaProfileParser(surface)
        n = len(parsed['atm_elmnt'])
        xyz = str(n)+'\nsource geometry\n'+''.join(
            f'{a} {p[0]:.10f} {p[1]:.10f} {p[2]:.10f}\n'
            for a,p in zip(parsed['atm_elmnt'], parsed['atm_pos']))
        lines = Path(energy).read_text().splitlines()
        energy_xyz = '\n'.join(lines[:int(lines[0])+2])+'\n'
        keys=[]
        for block in [xyz, energy_xyz]:
            mol = Chem.MolFromXYZBlock(block)
            assert mol is not None and mol.GetNumAtoms()==n, 'Geometry parse/atom count mismatch'
            rdDetermineBonds.DetermineBonds(mol, charge=0)
            assert Chem.GetFormalCharge(mol)==0 and len(Chem.GetMolFrags(mol))==1, 'Not one neutral structure'
            assert not any(a.GetNumRadicalElectrons() for a in mol.GetAtoms()), 'Radical requires separate policy'
            key = Chem.MolToInchiKey(mol)
            assert key, 'No perceived InChIKey'
            keys.append(key)
        result.update(atoms=n, cosmo_perceived_inchikey=keys[0], energy_perceived_inchikey=keys[1],
                      elements=sorted(set(parsed['atm_elmnt'])))
        assert keys[0].split('-')[0]==keys[1].split('-')[0], 'Source-pair connectivity mismatch'
        result['status']='source_pair_connectivity_agrees'
    except Exception as exc:
        result.update(status='source_preflight_unresolved', failure_mode=type(exc).__name__, error=str(exc))
    result['wall_seconds']=time.monotonic()-started
    return result


def main():
    source=D/'full-grid-name-inventory.json'; inv=json.loads(source.read_text())
    assert len(inv['rows'])==inv['denominator']==990
    out=D/'full-grid-source-audit';out.mkdir(exist_ok=True)
    pin=dict(inventory_sha256=sha(source), script_sha256=sha(__file__),
             scope='Source-pair geometry consistency under neutral, closed-shell perception; exact name association only. No independent CAS/product identity proof, no DFT or activity solve.')
    pinpath=out/'pins.json'
    if pinpath.exists(): assert json.loads(pinpath.read_text())==pin, 'Changed audit inputs/code; use a distinct version'
    else: pinpath.write_text(json.dumps(pin,indent=2)+'\n')
    log=out/'rows.jsonl'; done={}
    if log.exists():
        for line in log.read_text().splitlines():
            r=json.loads(line);assert r['solvent_key'] not in done
            done[r['solvent_key']]=r
    assert set(done)<={r['solvent_key'] for r in inv['rows']}
    pool=multiprocessing.Pool(1, maxtasksperchild=50)
    pause=None
    try:
        with log.open('a') as f:
            for row in inv['rows']:
                if row['solvent_key'] in done:continue
                available=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
                if available<2.5*1024**3:
                    pause='Host available memory below 2.5 GiB';break
                try: result=pool.apply_async(check,(row,)).get(timeout=45)
                except multiprocessing.TimeoutError:
                    pool.terminate();pool.join();pool=multiprocessing.Pool(1,maxtasksperchild=50)
                    result=dict(solvent_key=row['solvent_key'],status='source_preflight_unresolved',failure_mode='perception_timeout',error='Local source check exceeded 45 s; source unchanged')
                result['utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
                f.write(json.dumps(result)+'\n');f.flush();os.fsync(f.fileno())
                done[result['solvent_key']]=result
                if len(done)%50==0:print(json.dumps(dict(examined=len(done),statuses=dict(collections.Counter(r['status'] for r in done.values())))),flush=True)
    finally:
        pool.terminate();pool.join()
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),denominator=990,examined=len(done),
                 status='complete_source_inventory' if len(done)==990 else 'paused',pause_reason=pause,
                 counts=dict(collections.Counter(r['status'] for r in done.values())),pins=pin,
                 rows_sha256=sha(log),license='Source-derived metadata retained outside all git repositories.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
