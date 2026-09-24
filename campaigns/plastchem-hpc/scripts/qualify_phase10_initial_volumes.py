"""Qualify five explicit 25 C density references; no worker or panel mutation.

The other 34 references remain missing. This is an initial versioned table,
not complete solvent-volume coverage or experimental partition validation.
"""
import hashlib
import json
from pathlib import Path

D=Path('/mnt/r/plastchem-euler/phase10-v1')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    source=D/'volume-reference-candidates.json'
    candidates={r['solvent']:r for r in json.loads(source.read_text())['rows']}
    manifest=json.loads((D/'manifest.json').read_text())
    solvents={r['name']:r for r in manifest['solvents']}
    selected={
        '1,2,4-trimethylbenzene':(1,.8718,'0.8758 g/cu cm at 20 °C; 0.8718 g/cu cm at 25 °C'),
        '1-octanol':(1,.8262,'0.8262 g/cu cm at 25 °C'),
        'octane':(1,.6986,'0.6986 g/cu cm at 25 °C'),
    }
    rows=[]
    for key,(index,density,text) in selected.items():
        r=candidates[key];o=r['observations'][index];s=solvents[key]
        assert o['text']==text and not o['explicitly_estimated']
        assert r['input_inchikey'].split('-')[0]==s['inchikey'].split('-')[0]
        for evidence in [r['CID_lookup'],r['density_evidence']]:
            assert sha(Path(evidence['raw_path']))==evidence['response_sha256']
        assert any(x['SourceName']=='Hazardous Substances Data Bank (HSDB)' for x in o['references'])
        rows.append(dict(solvent=key,input_inchikey=s['inchikey'],density_g_cm3=density,
            temperature_K=298.15,source_temperature_C=25.,source_type='HSDB curated literature',
            evidence_text=text,references=o['references'],citation=o['raw_reference_strings'],
            retrieval=r['density_evidence'],CID_lookup=r['CID_lookup'],
            observation_index=index,raw_candidates_sha256=sha(source),
            molecular_weight_g_mol=s['molecular_weight_g_mol'],
            molar_volume_cm3_mol=s['molecular_weight_g_mol']/density,
            qualification='documented_pure_liquid_density_at_25_C',
            caveat='Literature reference, not a new measurement or experimental partition validation.'))
    for key,density,inchikey,url,mp in [
        ('gvl',1.05,'GAEKPEKOJKCEMS-UHFFFAOYSA-N','https://www.sigmaaldrich.com/US/en/product/aldrich/v403?context=pr',-31.),
        ('diethyleneglycol',1.118,'MTHSVFCYNBDYFN-UHFFFAOYSA-N','https://www.sigmaaldrich.com/FO/en/product/mm/803131',-6.5),
    ]:
        s=solvents[key];assert s['inchikey'].split('-')[0]==inchikey.split('-')[0]
        rows.append(dict(solvent=key,input_inchikey=s['inchikey'],source_inchikey=inchikey,
            density_g_cm3=density,temperature_K=298.15,source_temperature_C=25.,
            source_type='Manufacturer literature-listed physical property',url=url,
            source_observed_utc='2026-09-24T14:15:00Z',timestamp_precision='approximate minute of web inspection',
            evidence_text=f'{density} g/mL at 25 °C (lit.)',melting_point_C=mp,
            citation='Merck/Sigma-Aldrich product physical properties; literature attribution as stated, underlying paper not supplied.',
            molecular_weight_g_mol=s['molecular_weight_g_mol'],molar_volume_cm3_mol=s['molecular_weight_g_mol']/density,
            qualification='documented_pure_liquid_density_at_25_C',
            caveat='Supplier reference, not a campaign measurement; identity matched on connectivity.'))
    result=dict(status='partial_qualified_volume_references',manifest_sha256=sha(D/'manifest.json'),
        qualifier_sha256=sha(Path(__file__)),qualified=5,denominator=39,
        entries=sorted(rows,key=lambda r:r['solvent']),
        missing=sorted(set(solvents)-{r['solvent'] for r in rows}),
        formula='V (cm3/mol) = M (g/mol) / rho (g/cm3); 1 mL = 1 cm3',
        worker_inputs_changed=False,original_panel_changed=False,
        scope='Versioned postprocessing references only. No interpolation or empirical correction.')
    target=D/'qualified-physical-volumes-v1.json'
    raw=json.dumps(result,indent=2)+'\n'
    if target.exists():assert target.read_text()==raw,'Create a new reviewed version instead of replacing references'
    else:target.write_text(raw)
    print(json.dumps(dict(path=str(target),sha256=sha(target),qualified=5,missing=34)))


if __name__=='__main__':main()
