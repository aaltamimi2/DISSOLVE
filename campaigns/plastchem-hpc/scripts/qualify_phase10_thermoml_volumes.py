"""Select documented direct density measurements, independent of predicted logP.

One representative *observed* value per solvent, never an averaged invented
measurement. Preserve all candidates/spread and defer phase/stereo ambiguities.
No source or calculation in the original panel is changed.
"""
import csv
import hashlib
import json
import math
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

D=Path('/mnt/r/plastchem-euler/phase10-v1')
METHODS={'Vibrating tube method','Pycnometric method','Buoyancy - hydrostatic balance'}
DEFER={'1,3-dioxolan-2-one':'Liquid reference below the reported melting range needs explicit source/phase review.',
       'tetrahydrothiophene-1,1-dioxide':'Near/below melting range; source phase-state review remains open.',
       'dipentene':'No full-key matched candidate with an explicit experimental method; review stereochemical identity and source.'}


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def recheck(row):
    p=Path(row['source_path']);assert sha(p)==row['source_sha256']
    root=ET.parse(p).getroot()
    for n in root.iter():n.tag=n.tag.split('}')[-1]
    assert root.findtext('Citation/sDOI')==row['doi']
    b=next(b for b in root.findall('PureOrMixtureData') if b.findtext('nPureOrMixtureDataNumber')==row['block'])
    assert len(b.findall('Component'))==1 and b.findtext('PhaseID/ePhase')=='Liquid'
    org=b.findtext('Component/RegNum/nOrgNum')
    c=next(c for c in root.findall('Compound') if c.findtext('RegNum/nOrgNum')==org)
    assert c.findtext('sStandardInChIKey')==row['input_inchikey']==row['source_inchikey']
    prop=next(p for p in b.findall('Property') if p.findtext('nPropNumber')==row['property_number'])
    assert prop.findtext('.//ePropName')=='Mass density, kg/m3'
    assert prop.findtext('ePresentation')=='Direct value, X'
    assert prop.findtext('PropPhaseID/ePropPhase')=='Liquid'
    assert prop.findtext('.//eMethodName') in METHODS
    v=b.findall('NumValues')[row['row_index']]
    conditions={c.findtext('.//ConstraintType/*'):float(c.findtext('nConstraintValue')) for c in b.findall('Constraint')}
    for x in v.findall('VariableValue'):
        definition=next(d for d in b.findall('Variable') if d.findtext('nVarNumber')==x.findtext('nVarNumber'))
        conditions[definition.findtext('.//VariableType/*')]=float(x.findtext('nVarValue'))
    assert conditions==row['conditions'] and conditions['Temperature, K']==298.15
    assert 95<=conditions['Pressure, kPa']<=105
    value=next(x for x in v.findall('PropertyValue') if x.findtext('nPropNumber')==row['property_number'])
    assert float(value.findtext('nPropValue'))==row['density_kg_m3']
    uncertainty=value.findtext('CombinedUncertainty/nCombExpandUncertValue')
    assert uncertainty==row['expanded_uncertainty_kg_m3']
    assessment=value.findtext('CombinedUncertainty/nCombUncertAssessNum')
    metadata=next((a for a in prop.findall('CombinedUncertainty') if a.findtext('nCombUncertAssessNum')==assessment),None)
    return dict(value_kg_m3=float(uncertainty) if uncertainty else None,
        confidence_percent=float(metadata.findtext('nCombUncertLevOfConfid')) if metadata is not None and metadata.findtext('nCombUncertLevOfConfid') else None,
        evaluator=metadata.findtext('sCombUncertEvaluator') if metadata is not None else None,
        method=metadata.findtext('eCombUncertEvalMethod') if metadata is not None else None)


def main():
    source=D/'thermoml-volume-candidates.json';scan=json.loads(source.read_text())
    assert scan['archive_sha256_reverified'] and scan['xml_scanned']==11923
    manifest=json.loads((D/'manifest.json').read_text())
    entries=[];missing={};summary=[]
    for s in manifest['solvents']:
        key=s['name'];all_rows=[r for r in scan['candidates'] if r['solvent']==key]
        if key in DEFER:missing[key]=DEFER[key];continue
        eligible=[r for r in all_rows if r['method'] in METHODS and r['identity_match']=='full_key'
                  and r['source_type']=='Original' and r['experimental_purpose']=='Principal objective of the work']
        if not eligible:missing[key]='No qualified direct pure-liquid 298.15 K near-atmospheric experimental observation in this archive.';continue
        # Each paper gets one vote, avoiding repeated pressure/sample rows
        # dominating a median. The selected value is a real source observation.
        unique={}
        priority=lambda r:(abs(r['conditions']['Pressure, kPa']-101.325),
                            float(r['expanded_uncertainty_kg_m3'] or 'inf'),r['block'],r['row_index'])
        for r in eligible:
            if r['doi'] not in unique or priority(r)<priority(unique[r['doi']]):unique[r['doi']]=r
        values=[r['density_kg_m3'] for r in unique.values()];median=statistics.median(values)
        selected=min(unique.values(),key=lambda r:(abs(r['density_kg_m3']-median),
            float(r['expanded_uncertainty_kg_m3'] or 'inf'),r['doi'],r['block'],r['row_index']))
        uncertainty=recheck(selected);rho=selected['density_kg_m3']/1000;mw=s['molecular_weight_g_mol']
        assert 0<rho<3 and mw>0
        entry=dict(selected,qualification='qualified_direct_pure_liquid_density_reference',
            density_g_cm3=rho,temperature_K=298.15,molecular_weight_g_mol=mw,molar_volume_cm3_mol=mw/rho,
            source_url='https://doi.org/'+selected['doi'],uncertainty=uncertainty,
            density_only_log10_volume_expanded_uncertainty=(uncertainty['value_kg_m3']/selected['density_kg_m3']/math.log(10)) if uncertainty['value_kg_m3'] else None,
            independent_articles=len(unique),candidate_density_range_kg_m3=[min(r['density_kg_m3'] for r in all_rows),max(r['density_kg_m3'] for r in all_rows)],
            eligible_article_median_kg_m3=median,eligible_article_range_kg_m3=[min(values),max(values)],
            caveat='NIST/TRC compiled original measurements; combined uncertainty may be compiler-evaluated, not author-reported. Inter-source spread is separate and is not covered by a single measurement uncertainty. No claim about total partition-model uncertainty.')
        entries.append(entry)
        summary.append(dict(solvent=key,density_g_cm3=rho,molar_volume_cm3_mol=mw/rho,
            articles=len(unique),density_min_kg_m3=min(values),density_max_kg_m3=max(values),doi=selected['doi'],method=selected['method']))
    result=dict(status='partial_qualified_volume_references',manifest_sha256=sha(D/'manifest.json'),
        source_candidates_sha256=sha(source),qualifier_sha256=sha(Path(__file__)),
        qualified=len(entries),denominator=39,entries=entries,missing=missing,
        selection_rule='Full-key original pure-liquid experimental measurements at exactly 298.15 K and 95–105 kPa, explicit measurement method; one observation per DOI (closest atmospheric pressure, then reported expanded uncertainty). Select an observed density nearest the across-article median, ties by uncertainty then DOI. No averaging substituted for a measurement; all source values retained.',
        previous_version_preserved='qualified-physical-volumes-v1.json',worker_inputs_changed=False,original_panel_changed=False)
    target=D/'qualified-physical-volumes-v2.json';assert not target.exists(),'Preserve previous qualification version'
    target.write_text(json.dumps(result,indent=2)+'\n')
    with (D/'thermoml-qualified-density-summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
    print(json.dumps(dict(qualified=len(entries),denominator=39,missing=missing,path=str(target),sha256=sha(target))))


if __name__=='__main__':main()
