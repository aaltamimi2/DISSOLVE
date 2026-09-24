"""A-9 licensed source inventory only; serial, no DFT, no product writes."""
import ast
import datetime
import hashlib
import json
import re
from pathlib import Path

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1')
SOURCE = Path('/mnt/c/Users/aaltamimi2/AppData/Local/COSMOlogic/COSMOthermX19/COSMOtherm/COSMObase-1901/BP-TZVP-COSMO')
PRODUCT = Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/thermodynamics.py')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    D.mkdir(parents=True, exist_ok=True)
    tree = ast.parse(PRODUCT.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, 'id', '') == 'COMMON_INTERP_KEYS')
    keys = sorted(ast.literal_eval(node.value.args[0]))
    assert len(keys) == 69
    files = {}
    for folder in SOURCE.iterdir():
        if folder.is_dir():
            for p in folder.glob('*.cosmo'):
                name = re.sub(r'_c\d+$', '', p.stem).casefold()
                files.setdefault(name, []).append(p)
    rows = []
    for key in keys:
        candidates = []
        for p in files.get(key.casefold(), []):
            energy = p.with_suffix('.energy')
            if not energy.exists():
                continue
            text = energy.read_text(errors='replace')
            match = re.search(r'ENERGY=([-+\d.Ee]+);', text)
            assert match, str(energy)
            candidates.append((float(match.group(1)), str(p), str(energy)))
        row = dict(solvent_key=key, identity_match_basis='exact library basename; connectivity verification still required', conformer_count=len(candidates))
        if candidates:
            value, surface, energy = min(candidates)
            row.update(status='name_mapped_identity_pending', source_cosmo=surface,
                       source_cosmo_sha256=sha(Path(surface)), source_energy=energy,
                       source_energy_sha256=sha(Path(energy)), lowest_energy_hartree=value,
                       conformer_energies=[dict(energy_hartree=e, cosmo=s, energy_file=f) for e, s, f in sorted(candidates)],
                       route_A='default_turbomole, original COSMObase surface; comparison only',
                       route_B='frozen ORCA 24a recipe from selected geometry; not yet run')
        else:
            row.update(status='missing_from_exact_name_lookup', route_B='structure required')
            if key == 'gvl':
                row.update(structure_proposal_smiles='CC1CCC(=O)O1', structure_name='gamma-valerolactone')
        rows.append(row)
        print(key, row['status'], len(candidates), flush=True)
    out = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), source_root=str(SOURCE),
               product_source=str(PRODUCT), product_source_sha256=sha(PRODUCT), common_denominator=69,
               library_unique_names=len(files), rows=rows,
               license='Licensed source. No source surfaces/energy files may be committed or distributed outside lane/Euler home.')
    (D/'common-name-inventory.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps(dict(mapped=sum(r['conformer_count']>0 for r in rows), denominator=69)), flush=True)


if __name__ == '__main__':
    main()
