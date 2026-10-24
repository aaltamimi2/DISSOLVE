"""A-13 (owner decision 2026-10-12, "if justifiable, fix, otherwise drop"): a third D-IDENT observation for geometries
whose bond ORDERS the perception engines could not assign, although every atom is bonded exactly as in the input.

D-IDENT asks that the optimized geometry have the input's connectivity. RDKit DetermineBonds and Open Babel answer that
by guessing bond orders from the coordinates and comparing InChIKey first blocks; on sulfonated nitro and azo dyes and
triarylmethane dyes the guess fails ("Final molecular charge does not match input; could not find valid bond ordering")
or lands on another valence structure, and the structure was rejected with its bonds unchanged. This observation tests
the connectivity directly and takes the bond orders from the input, where they are known:

  1. bonds from the coordinates alone, twice and independently: RDKit DetermineConnectivity (the first step of the
     campaign's own DetermineBonds, default covalent factor) and Open Babel's connect-the-dots;
  2. each of the two bonded graphs, all atoms including every hydrogen, element-labelled, must be isomorphic to the
     input's graph; a moved hydrogen (a tautomer, a zwitterion), a broken or a new bond fails here;
  3. no hydrogen may sit between two heavy atoms: exactly one heavy atom within SHARED_H_A of every hydrogen;
  4. the input's bond orders and charges are laid on the coordinates through the isomorphism, stereochemistry is read
     from the 3D geometry, and the InChIKey of that molecule is the perceived key; its first block must be the input's.

Anything else stays rejected, with the reason recorded. No geometry is changed and nothing is recomputed.

    python3 scripts/template_identity.py XYZ SMILES INPUT_KEY     (anaconda RDKit 2023.09.2, the identity environment)"""
import json
import re
import subprocess
import sys

import networkx as nx
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
from rdkit.Geometry import Point3D

OBABEL = '/home/aaltamimi2/anaconda3/bin/obabel'
METHOD = 'input_bond_orders_on_perceived_connectivity'
SHARED_H_A = 1.40  # a hydrogen with two heavy atoms inside this distance is shared, not assigned


def graph(mol):
    g = nx.Graph()
    for a in mol.GetAtoms():
        g.add_node(a.GetIdx(), el=a.GetAtomicNum())
    for b in mol.GetBonds():
        g.add_edge(b.GetBeginAtomIdx(), b.GetEndAtomIdx())
    return g


def mapping(reference, observed):
    """Reference atom -> observed atom, or None when the bonded graphs differ."""
    matcher = nx.algorithms.isomorphism.GraphMatcher(graph(reference), graph(observed), node_match=lambda a, b: a['el'] == b['el'])
    return dict(matcher.mapping) if matcher.is_isomorphic() else None


def template_row(xyz, smiles, input_key):
    row = {'method': METHOD, 'shared_hydrogen_limit_A': SHARED_H_A}
    try:
        reference = Chem.AddHs(Chem.MolFromSmiles(smiles))
        geometry = Chem.MolFromXYZFile(str(xyz))
        if [a.GetAtomicNum() for a in geometry.GetAtoms()].count(0) or geometry.GetNumAtoms() != reference.GetNumAtoms():
            raise ValueError('atom count differs from the input')
        rdDetermineBonds.DetermineConnectivity(geometry)
        sdf = subprocess.run([OBABEL, str(xyz), '-osdf'], capture_output=True, text=True, check=True).stdout
        babel = Chem.MolFromMolBlock(sdf.split('$$$$')[0], sanitize=False, removeHs=False)
        if babel is None or babel.GetNumAtoms() != reference.GetNumAtoms():
            raise ValueError('Open Babel connectivity could not be read')
        to_geometry, to_babel = mapping(reference, geometry), mapping(reference, babel)
        row['bonded_graph_isomorphic_to_input'] = {'rdkit_determine_connectivity': to_geometry is not None,
                                                   'openbabel_connect_the_dots': to_babel is not None}
        conformer = geometry.GetConformer()
        heavy = [a.GetIdx() for a in geometry.GetAtoms() if a.GetAtomicNum() > 1]
        closest = []
        for a in geometry.GetAtoms():
            if a.GetAtomicNum() == 1:
                p = conformer.GetAtomPosition(a.GetIdx())
                closest.append(sorted(p.Distance(conformer.GetAtomPosition(j)) for j in heavy)[:2])
        row['hydrogen_bonded_distance_max_A'] = round(max(d[0] for d in closest), 3) if closest else None
        seconds = [d[1] for d in closest if len(d) > 1]
        row['hydrogen_second_heavy_distance_min_A'] = round(min(seconds), 3) if seconds else None
        if to_geometry is None or to_babel is None:
            raise ValueError('bonded graph differs from the input (a hydrogen moved, or a bond broke or formed)')
        if seconds and min(seconds) < SHARED_H_A:
            raise ValueError(f'a hydrogen lies within {SHARED_H_A} A of two heavy atoms (shared, not assigned)')
        placed = Chem.Mol(reference)
        placed.RemoveAllConformers()
        coordinates = Chem.Conformer(placed.GetNumAtoms())
        for i, j in to_geometry.items():
            p = conformer.GetAtomPosition(j)
            coordinates.SetAtomPosition(i, Point3D(p.x, p.y, p.z))
        placed.AddConformer(coordinates, assignId=True)
        Chem.AssignStereochemistryFrom3D(placed)
        key = Chem.MolToInchiKey(placed)
        if not re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]', key or ''):
            raise ValueError('no InChIKey from the input bond orders on the optimized coordinates')
        if key.split('-')[0] != input_key.split('-')[0]:
            raise ValueError(f'first block {key.split("-")[0]} from the input bond orders differs from the input key')
        row['full_inchikey'] = key
    except Exception as exc:
        row['error'] = str(exc)
    return row


if __name__ == '__main__':
    print(json.dumps(template_row(*sys.argv[1:4])))
