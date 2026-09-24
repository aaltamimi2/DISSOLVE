"""Qualify the two liquid-reference measurements requiring phase caveats.

No solid-liquid equilibrium claim; no density extrapolation or model fitting.
"""
import datetime
import json
import math
from pathlib import Path

from qualify_phase10_thermoml_volumes import recheck, sha

D=Path('/mnt/r/plastchem-euler/phase10-v1')


def main():
    source=D/'qualified-physical-volumes-v3.json'
    result=json.loads(source.read_text());assert result['qualified']==37
    scan=json.loads((D/'thermoml-volume-candidates.json').read_text())
    solvents={s['name']:s for s in json.loads((D/'manifest.json').read_text())['solvents']}
    reviews=[
        dict(solvent='1,3-dioxolan-2-one',doi='10.1016/j.jct.2012.12.025',block='1',row_index=0,
             density_kg_m3=1350.,phase_qualification='explicitly_measured_subcooled_liquid',
             phase_evidence_url='https://pureadmin.qub.ac.uk/ws/portalfiles/portal/3278701/Low_pressure_carbon_dioxide_solubility_in_lithium_ion_batteries_based_electrolytes_as_a_function_of_temperature._Measurement_and_prediction.pdf',
             phase_evidence='Accepted manuscript Table 2, printed page 40 (PDF page 42), footnote c: the pure EC measurement was made in a sub-cooled liquid after heating to 353 K. Table temperature is 298.15 K. Footnote a reports precision 0.01 and accuracy 0.05 g/cm3.',
             access_note='Text inspected through web browser 2026-09-24; direct local PDF download returned HTTP 403 and browser screenshot failed. Numerical source XML is retained and digest-verified.'),
        dict(solvent='tetrahydrothiophene-1,1-dioxide',doi='10.1016/j.tca.2016.07.005',block='5',row_index=5,
             density_kg_m3=1266.4,phase_qualification='measured_liquid_reference_near_or_below_melting_range',
             phase_evidence_url='https://pure.kfupm.edu.sa/en/publications/experimental-densities-and-viscosities-of-binary-mixture-of-1-but/',
             phase_evidence='Author institution abstract reports density measurements across the entire composition range from 298.15 K. The archived primary-data XML identifies this row as pure liquid, direct mass density measured by vibrating tube. No claim that the original authors explicitly called it supercooled.',
             melting_context_url='https://www.sigmaaldrich.com/IT/en/product/mm/807993',
             melting_context='Manufacturer notes that the material can be solid, liquid, solidified melt or supercooled melt near its melting range.',
             access_note='Institutional abstract and manufacturer phase note inspected through web browser; primary numeric XML rechecked locally.'),
    ]
    for review in reviews:
        key=review['solvent'];s=solvents[key]
        candidates=[r for r in scan['candidates'] if all(r[k]==review[k] for k in ['solvent','doi','block','row_index','density_kg_m3'])]
        assert len(candidates)==1
        row=candidates[0];assert row['source_type']=='Original'
        uncertainty=recheck(row);rho=row['density_kg_m3']/1000
        result['entries'].append(dict(row,**{k:v for k,v in review.items() if k not in row},
            density_g_cm3=rho,temperature_K=298.15,molecular_weight_g_mol=s['molecular_weight_g_mol'],
            molar_volume_cm3_mol=s['molecular_weight_g_mol']/rho,source_url='https://doi.org/'+row['doi'],
            uncertainty=uncertainty,qualification='qualified_liquid_reference_with_explicit_phase_caveat',
            density_only_log10_volume_expanded_uncertainty=uncertainty['value_kg_m3']/row['density_kg_m3']/math.log(10),
            review_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            caveat='Liquid reference only, potentially metastable at 298.15 K. The calculation excludes crystallization, fusion and solid-liquid equilibrium; it is not a claim of a stable room-temperature liquid solvent or solid solubility. NIST compiler uncertainty is not total partition-model uncertainty.'))
        del result['missing'][key]
    result.update(status='complete_qualified_liquid_reference_set_with_caveats',qualified=39,
        previous_version_preserved=source.name,previous_version_sha256=sha(source),
        phase_qualifier_sha256=sha(Path(__file__)),
        selection_note=result['selection_note']+' The final two entries use direct measured liquid densities with explicit phase-state qualifications; no empirical adjustment or extrapolation.')
    assert len({r['solvent'] for r in result['entries']})==39 and not result['missing']
    target=D/'qualified-physical-volumes-v4.json';assert not target.exists()
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(path=str(target),sha256=sha(target),qualified=39)))


if __name__=='__main__':main()
