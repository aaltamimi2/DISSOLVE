"""A-13 (owner decision 2026-10-13, "add the hydrogen check"): a D-IDENT observation that does not go through InChI.

D-IDENT compares InChIKey first blocks, and standard InChI treats mobile hydrogens as equivalent: a geometry in which a
proton left its atom during the optimization (a sulfonic acid proton on an imine nitrogen, a hydroxy-azo dye turned
hydrazone) can keep the input's first block and pass. Acid fuchsine FB (FKDGKTNDRAXENR) was released in promotion-v13
that way, as the zwitterion. This observation asks the question directly:

  every hydrogen of the optimized geometry must have, as its nearest heavy atom, the heavy atom it is bonded to in
  the input.

The atoms of the geometry are the input's atoms in the input's order (the geometry is embedded from
Chem.AddHs(Chem.MolFromSmiles(smiles)) and ORCA keeps the order); the element sequence is checked and a geometry that
does not have it fails the observation with that reason, it is not mapped some other way. There is no distance
threshold: a hydrogen in a short hydrogen bond that is still nearer its own atom passes (owner, 2026-10-13: the three
released compounds with a near-symmetric O-H...O bridge are kept), and the smallest margin is recorded.

A structure is verified only when D-IDENT's first-block rule is met AND this observation finds no moved hydrogen
(identity_campaign.decide). No geometry is changed and nothing is recomputed.

    python3 scripts/hydrogen_identity.py XYZ SMILES     (anaconda RDKit 2023.09.2, the identity environment)"""
import json
import math
import sys

from rdkit import Chem

METHOD = 'hydrogen_nearest_heavy_atom_is_the_input_one'


def hydrogen_row(xyz, smiles):
    row = {'method': METHOD}
    try:
        reference = Chem.AddHs(Chem.MolFromSmiles(smiles))
        lines = open(xyz).read().splitlines()
        count = int(lines[0])
        atoms = [line.split() for line in lines[2:2 + count]]
        if len(atoms) != count or count != reference.GetNumAtoms():
            raise ValueError('atom count differs from the input')
        table = Chem.GetPeriodicTable()
        if [table.GetAtomicNumber(a[0].capitalize()) for a in atoms] != [a.GetAtomicNum() for a in reference.GetAtoms()]:
            raise ValueError('the geometry does not list the input atoms in the input order')
        position = [tuple(float(x) for x in a[1:4]) for a in atoms]
        heavy = [a.GetIdx() for a in reference.GetAtoms() if a.GetAtomicNum() > 1]
        moved, margin = [], None
        for atom in reference.GetAtoms():
            if atom.GetAtomicNum() != 1:
                continue
            declared = [n.GetIdx() for n in atom.GetNeighbors() if n.GetAtomicNum() > 1]
            if len(declared) != 1:
                continue  # H2, or a hydrogen the input does not put on one heavy atom: nothing to compare
            h, own = atom.GetIdx(), declared[0]
            d_own = math.dist(position[h], position[own])
            others = [(math.dist(position[h], position[j]), j) for j in heavy if j != own]
            if not others:
                continue
            d_other, other = min(others)
            if margin is None or d_other - d_own < margin[0]:
                margin = (d_other - d_own, h, own, other, d_own, d_other)
            if d_other < d_own:
                name = lambda i: reference.GetAtomWithIdx(i).GetSymbol() + str(i)
                moved.append(dict(hydrogen=name(h), input_atom=name(own), input_atom_distance_A=round(d_own, 3),
                                  nearest_atom=name(other), nearest_atom_distance_A=round(d_other, 3)))
        row['moved_hydrogens'] = moved
        if margin:
            row['smallest_margin'] = dict(hydrogen_index=margin[1], input_atom_index=margin[2], other_atom_index=margin[3],
                                          input_atom_distance_A=round(margin[4], 3), other_atom_distance_A=round(margin[5], 3))
        if moved:
            row['error'] = (f'{len(moved)} hydrogen(s) left the atom they are bonded to in the input: ' +
                            '; '.join(f"{m['hydrogen']} from {m['input_atom']} ({m['input_atom_distance_A']} A) to "
                                      f"{m['nearest_atom']} ({m['nearest_atom_distance_A']} A)" for m in moved))
    except Exception as exc:
        row.pop('moved_hydrogens', None)
        row['error'] = 'hydrogen check not possible: ' + str(exc)
    return row


if __name__ == '__main__':
    print(json.dumps(hydrogen_row(*sys.argv[1:3])))
