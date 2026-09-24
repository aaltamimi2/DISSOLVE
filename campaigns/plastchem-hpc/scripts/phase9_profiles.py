"""Input-bound 24a profile reuse; no shared mutable engine segment indices.

The installed engine's ordinary parser, conversion and clustering run once per
surface. Only their compact output is retained. Each new engine receives fresh
Molecule/COSMOStruct shells and independently remapped segment-area dictionaries.
The original numerical engine and all physical parameters remain unchanged.
"""
import copy
import gc
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.molecules import Molecule
from opencosmorspy.parameterization import openCOSMORS24a


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ProfileCache:
    def __init__(self):
        self.profiles = {}
        self.parse_count = 0

    def get(self, path, expected=None):
        path = str(Path(path).resolve())
        if path in self.profiles:
            p = self.profiles[path]
            assert expected is None or p['sha256'] == expected
            return p
        digest = sha(path)
        assert expected is None or digest == expected, path
        engine = COSMORS(openCOSMORS24a())
        engine.add_molecule([path])
        mol = engine.enth.mol_lst[0]
        p = dict(path=path, sha256=digest, area=float(mol.get_area()),
                 volume=float(mol.get_volume()),
                 descriptors=copy.deepcopy(engine.enth.segtp_collection.segtp_lst),
                 areas=dict(mol.get_segtp_area_dct()))
        self.profiles[path] = p
        self.parse_count += 1
        del engine, mol
        gc.collect()
        return p


def profile_digest(p):
    import pickle
    return hashlib.sha256(pickle.dumps(p, protocol=4)).hexdigest()


def engine_from_profiles(profiles, threshold=None):
    par = openCOSMORS24a()
    if threshold is not None:
        par.cosmospace_conv_thresh = threshold
    engine = COSMORS(par)
    descriptors = engine.enth.segtp_collection.segtp_lst
    lookup = {}
    for profile in profiles:
        remap = {}
        for local, descriptor in enumerate(profile['descriptors']):
            key = tuple(sorted(descriptor.items()))
            if key not in lookup:
                lookup[key] = len(descriptors)
                descriptors.append(dict(descriptor))
            remap[local] = lookup[key]
        # Use the original Molecule methods, with only the fields they consume.
        mol = Molecule.__new__(Molecule)
        mol.cosmo_struct_lst = [SimpleNamespace(
            area=profile['area'], volume=profile['volume'],
            segtp_area_dct={remap[k]: v for k, v in profile['areas'].items()})]
        engine.enth.mol_lst.append(mol)
    engine.comb.set_area_array(np.array([p['area'] for p in profiles]))
    engine.comb.set_volume_array(np.array([p['volume'] for p in profiles]))
    return engine


def infinite_dilution(profiles, phase, temperature=298.15):
    engine = engine_from_profiles([*profiles, phase])
    x = np.zeros(len(profiles) + 1)
    x[-1] = 1.
    engine.add_job(x=x, T=temperature, refst='pure_component')
    values = engine.calculate()['tot']['lng'][0, :-1].copy()
    assert np.isfinite(values).all()
    return values
