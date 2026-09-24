"""Add four documented supplier densities and the computed limonene stereoisomer.

Preserves v1/v2. Ethylene carbonate and sulfolane remain explicit phase reviews.
"""
import json
import statistics
from pathlib import Path

from qualify_phase10_thermoml_volumes import recheck, sha, METHODS

D=Path('/mnt/r/plastchem-euler/phase10-v1')


def main():
    source=D/'qualified-physical-volumes-v2.json';result=json.loads(source.read_text())
    assert result['qualified']==32
    solvents={s['name']:s for s in json.loads((D/'manifest.json').read_text())['solvents']}
    specifications=[
        ('2,6-dimethyl-4-heptanone',.808,'PTTPXKJBFFKCEK-UHFFFAOYSA-N','https://www.sigmaaldrich.com/US/en/product/aldrich/w353701','≥99%'),
        ('4-methyl-2-pentanol',.802,'WVYWICLMDOOCFB-UHFFFAOYSA-N','https://www.sigmaaldrich.com/US/en/product/aldrich/109916','98%'),
        ('4-oh-4-me-2-pentanone',.931,'SWXVUIWOUIDPGS-UHFFFAOYSA-N','https://www.sigmaaldrich.com/TR/en/product/sial/phr1916','neat secondary reference material'),
        ('isophorone',.923,'HJOVHMDZYOCNQW-UHFFFAOYSA-N','https://www.sigmaaldrich.com/US/en/product/aldrich/w355305','≥97%'),
    ]
    for key,rho,ik,url,grade in specifications:
        s=solvents[key];assert s['inchikey']==ik
        result['entries'].append(dict(solvent=key,input_inchikey=ik,source_inchikey=ik,
            density_g_cm3=rho,temperature_K=298.15,molecular_weight_g_mol=s['molecular_weight_g_mol'],
            molar_volume_cm3_mol=s['molecular_weight_g_mol']/rho,source_url=url,
            source_observed_utc='2026-09-24T14:37:00Z',timestamp_precision='approximate minute of web inspection',
            source_type='Manufacturer literature-listed physical property',product_grade=grade,
            evidence_text=f'{rho} g/mL at 25 °C (lit.)',qualification='documented_liquid_density_reference',
            caveat='Literature property on the manufacturer page; not a certified density measurement or campaign measurement. Underlying original literature and density uncertainty are not supplied.'))
        del result['missing'][key]
    key='dipentene';s=solvents[key]
    verified_path=D.parent/'phase9-solvent-library-v1/results'/s['inchikey']/'verified-result.json'
    verified=json.loads(verified_path.read_text());assert sha(verified_path)==s['verified_result_sha256']
    assert verified['connectivity_match'] and verified['all_perception_engines_agree_on_full_key']
    computed=verified['perceived_inchikey'];assert computed=='XMGQYMWWDOXHJM-JTQLQIEISA-N'
    scan=json.loads((D/'thermoml-volume-candidates.json').read_text())
    eligible=[r for r in scan['candidates'] if r['solvent']==key and r['source_inchikey']==computed and r['method'] in METHODS]
    median=statistics.median(r['density_kg_m3'] for r in eligible)
    row=min(eligible,key=lambda r:(abs(r['density_kg_m3']-median),float(r['expanded_uncertainty_kg_m3'] or 'inf'),r['doi']))
    # Recheck against the fully perceived species, while preserving the original
    # unspecified input key separately in the final reference record.
    uncertainty=recheck(dict(row,input_inchikey=computed))
    rho=row['density_kg_m3']/1000
    result['entries'].append(dict(row,density_g_cm3=rho,temperature_K=298.15,
        molecular_weight_g_mol=s['molecular_weight_g_mol'],molar_volume_cm3_mol=s['molecular_weight_g_mol']/rho,
        source_url='https://doi.org/'+row['doi'],uncertainty=uncertainty,
        computed_inchikey=computed,identity_match='full_source_key_equals_ORCA_perceived_key',
        identity_evidence_path=str(verified_path),identity_evidence_sha256=sha(verified_path),
        identity_engines=verified['perception_engines_agreeing_on_perceived_key'],
        qualification='direct_density_of_the_computed_pure_limonene_stereoisomer',
        independent_articles=len({r['doi'] for r in eligible}),
        candidate_density_range_kg_m3=[min(r['density_kg_m3'] for r in eligible),max(r['density_kg_m3'] for r in eligible)],
        caveat='The pinned dipentene calculation is one pure limonene stereoisomer. This density matches its full perceived key. It does not represent commercial dipentene terpene mixtures or a separately modeled racemate.'))
    del result['missing'][key]
    result.update(qualified=len(result['entries']),previous_version_preserved=source.name,
        previous_version_sha256=sha(source),extension_qualifier_sha256=sha(Path(__file__)),
        selection_note='32 measured references retain the v2 selection. Four absent identities use explicit 25 C supplier references. Dipentene uses an experimental source matching its full ORCA-perceived stereoisomer key.')
    assert result['qualified']==37 and len(result['missing'])==2
    target=D/'qualified-physical-volumes-v3.json';assert not target.exists()
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(qualified=37,denominator=39,missing=result['missing'],path=str(target),sha256=sha(target))))


if __name__=='__main__':main()
