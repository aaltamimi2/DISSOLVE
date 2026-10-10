"""Add an attributed 25 C supplier density to the live legacy panel only.

Preserves the original live metadata and currently processed tier-2 records.
Does not modify immutable promotion releases, activity coefficients or science.
"""
import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil

from rdkit import Chem
from rdkit.Chem import Descriptors

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')
P = R / 'state/thermodynamics-v1'
D = B / 'tier2-v1/diphenyl-volume-20260924'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    assert not D.exists(), 'Reconcile existing volume amendment before any retry'
    supplier = P / 'supplier-molar-volumes.json'
    original = supplier.read_bytes()
    current = json.loads(original)
    assert current['status'] == 'verified_supplier_literature_density_additions'
    assert 'diphenyl ether' not in current['entries']
    inventory = json.loads((P / 'solvent-inventory.json').read_text())
    entry = next(r for r in inventory['solvents'] if r['solvent_key'] == 'diphenyl ether')
    key = 'USIUVYZYUHIAEV-UHFFFAOYSA-N'
    mol = Chem.MolFromSmiles('O(c1ccccc1)c2ccccc2')
    assert Chem.MolToInchiKey(mol) == entry['connectivity_inchikey'] == key
    phase = next(r for r in json.loads((P / 'solvent-phase-review.json').read_text())['entries']
                 if r['solvent'] == 'diphenyl ether')
    assert phase['reference_transition_K'] == 300.03
    mass = Descriptors.MolWt(mol)
    density = 1.073
    volume = mass / density
    assert abs(mass - 170.21) < .005 and 158 < volume < 160
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    evidence = dict(source_url='https://www.sigmaaldrich.com/US/en/product/aldrich/w366706',
        retrieved_utc=now, product='W366706', name='Diphenyl ether', cas='101-84-8',
        inchikey=key, smiles='O(c1ccccc1)c2ccccc2',
        capture_method='Curated factual extraction from manufacturer page opened with web tool; not original HTML bytes',
        density_excerpt='1.073 g/mL at 25 °C (lit.)', density_g_cm3=density,
        temperature_K=298.15, pressure_MPa=None, density_uncertainty=None,
        original_experimental_reference=None, source_class='supplier_literature_value',
        supplier_molar_mass_g_mol=170.21, supplier_form='solid or liquid',
        supplier_melting_range_C=[25,27],
        phase_reference_url='https://webbook.nist.gov/cgi/cbook.cgi?ID=C101848&Mask=225',
        phase_reference='Ginnings and Furukawa 1953, compiled by NIST; triple point 300.03 K',
        qualification='Supplier density used conditionally for the hypothetical/subcooled liquid reference at 298.15 K. Catalog does not independently document a liquid-phase measurement protocol or uncertainty. Not a solid-solvent partition coefficient.',
        primary_density_alternative='Pinned ThermoML j.jct.2012.02.001 has pure-liquid density beginning at 303.15 K; no mixture value or temperature extrapolation substituted.')
    D.mkdir(parents=True)
    (D / 'supplier-molar-volumes.before.json').write_bytes(original)
    save(D / 'evidence.json', evidence)
    qualified = dict(molar_volume_cm3_mol=volume, molar_mass_g_mol=mass,
        molar_mass_method='RDKit standard molecular weight from supplier SMILES matched to pinned inventory key',
        density_g_cm3=density, temperature_K=298.15, pressure_MPa=None,
        source_inchikey=key, source_url=evidence['source_url'], source_class='supplier_literature_value',
        source_evidence_path=str(D / 'evidence.json'), source_evidence_sha256=sha(D / 'evidence.json'),
        source_evidence_type='curated_attributed_extraction_not_original_document',
        density_uncertainty=None,
        uncertainty_note='Supplier literature value; original experiment, pressure and uncertainty unspecified. No total prediction uncertainty inferred.',
        qualification=evidence['qualification'], phase_reference_url=evidence['phase_reference_url'])
    save(D / 'qualified-volume.json', qualified)
    ledger = json.loads((R / 'state/tier2-v1/thermodynamics/processing-ledger.json').read_text())
    for key, item in ledger.items():
        raw = Path(item['result_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item['result_sha256']
        dest = D / 'before-records' / (key + '.json')
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(raw)
    save(D / 'processing-ledger.before.json', ledger)
    releases = {name: sha(B / name / 'manifest.json') for name in ['promotion-v1','promotion-ext39-v1']}
    current['entries']['diphenyl ether'] = qualified
    assert supplier.read_bytes() == original, 'Live supplier metadata changed during preparation'
    temp = supplier.with_suffix('.diphenyl.tmp')
    save(temp, current)
    save(D / 'supplier-molar-volumes.after.json', current)
    intent = dict(utc=now, status='prepared', live_metadata=str(supplier),
        previous_sha256=hashlib.sha256(original).hexdigest(), new_sha256=sha(temp),
        captured_records=len(ledger), release_manifest_pins=releases,
        volume_cm3_mol=volume, water_to_solvent_log10_correction=math.log10(18.07/volume),
        scope='Live legacy volume metadata only; immutable releases and stored activities remain unchanged.')
    save(D / 'application.json', intent)
    temp.replace(supplier)
    assert sha(supplier) == intent['new_sha256']
    from thermodynamic_prediction import refresh_volumes
    verified = refresh_volumes()
    assert verified['diphenyl ether'] == qualified
    assert all(sha(B / name / 'manifest.json') == pin for name, pin in releases.items())
    intent['status'] = 'applied_and_source_checked'
    intent['applied_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(D / 'application.json', intent)
    print(json.dumps(intent, indent=2))


if __name__ == '__main__':
    main()
