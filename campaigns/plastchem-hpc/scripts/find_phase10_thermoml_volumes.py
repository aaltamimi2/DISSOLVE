"""Find direct pure-liquid 298.15 K measurements in the existing NIST archive.

Candidate evidence only; no automatically selected density or worker mutation.
One serial process, run with nice/idle I/O priority; stop for primary assembly.
"""
import collections
import datetime
import hashlib
import json
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def guard():
    p=D.parent/'phase9-v1/release-watch-status.json'
    if json.loads(p.read_text())['status'] in ['running_full_audit_and_build','running_independent_delivery_verification']:
        raise RuntimeError('Primary release assembly takes priority; rerun reference scan afterward')
    available=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
    assert available>=2.5*1024**3,'Host memory guard'


def candidates(root, targets, member, digest):
    for node in root.iter():node.tag=node.tag.split('}')[-1]
    compounds={c.findtext('RegNum/nOrgNum'):c for c in root.findall('Compound')}
    citation=root.find('Citation')
    for block in root.findall('PureOrMixtureData'):
        if len(block.findall('Component'))!=1 or block.findtext('PhaseID/ePhase')!='Liquid':continue
        compound=compounds[block.findtext('Component/RegNum/nOrgNum')]
        key=compound.findtext('sStandardInChIKey','');target=targets.get(key.split('-')[0])
        if target is None:continue
        props={p.findtext('nPropNumber'):p for p in block.findall('Property')
            if p.findtext('.//ePropName')=='Mass density, kg/m3'
            and p.findtext('ePresentation')=='Direct value, X'
            and p.findtext('PropPhaseID/ePropPhase')=='Liquid'}
        if not props:continue
        constants={c.findtext('.//ConstraintType/*'):float(c.findtext('nConstraintValue')) for c in block.findall('Constraint')}
        variables={v.findtext('nVarNumber'):v.findtext('.//VariableType/*') for v in block.findall('Variable')}
        for index,values in enumerate(block.findall('NumValues')):
            conditions=dict(constants)
            for v in values.findall('VariableValue'):
                conditions[variables[v.findtext('nVarNumber')]]=float(v.findtext('nVarValue'))
            if conditions.get('Temperature, K')!=298.15 or not 95<=conditions.get('Pressure, kPa',-1)<=105:continue
            for value in values.findall('PropertyValue'):
                prop=props.get(value.findtext('nPropNumber'))
                if prop is None:continue
                yield dict(solvent=target['name'],input_inchikey=target['inchikey'],source_inchikey=key,
                    identity_match='full_key' if key==target['inchikey'] else 'connectivity_first_block',
                    source_names=[n.text for n in compound.findall('sCommonName')],
                    archive_member=member,source_sha256=digest,doi=root.findtext('Citation/sDOI'),
                    title=root.findtext('Citation/sTitle'),year=root.findtext('Citation/yrPubYr'),
                    authors=[n.text for n in citation.findall('sAuthor')],
                    publication=root.findtext('Citation/sPubName'),source_type=root.findtext('Citation/eSourceType'),
                    experimental_purpose=block.findtext('eExpPurpose'),
                    block=block.findtext('nPureOrMixtureDataNumber'),row_index=index,
                    property_number=value.findtext('nPropNumber'),conditions=conditions,
                    density_kg_m3=float(value.findtext('nPropValue')),
                    method=prop.findtext('.//eMethodName'),
                    expanded_uncertainty_kg_m3=value.findtext('CombinedUncertainty/nCombExpandUncertValue'),
                    property_metadata=ET.tostring(prop,encoding='unicode'),
                    value_metadata=ET.tostring(value,encoding='unicode'),
                    sample_metadata=ET.tostring(compound,encoding='unicode'),
                    qualification='candidate_pending_source_method_phase_and_consistency_review')


def main():
    guard()
    pin=json.loads((R/'state/thermodynamics-v1/thermoml-download.json').read_text())
    archive=Path(pin['path']);assert archive.stat().st_size==pin['bytes'] and sha(archive)==pin['sha256']
    manifest=json.loads((D/'manifest.json').read_text())
    targets={s['inchikey'].split('-')[0]:s for s in manifest['solvents']};assert len(targets)==39
    keys=[k.encode() for k in targets]
    rows=[];scanned=0;matched=0
    out=D/'volume-sources/thermoml';out.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'r|gz') as stream:
        for member in stream:
            if not member.isfile() or not member.name.endswith('.xml'):continue
            scanned+=1
            if scanned%100==0:guard()
            raw=stream.extractfile(member).read()
            if not any(k in raw for k in keys):continue
            digest=hashlib.sha256(raw).hexdigest()
            found=list(candidates(ET.fromstring(raw),targets,member.name,digest))
            if not found:continue
            matched+=1;p=out/(digest+'.xml')
            if p.exists():assert sha(p)==digest
            else:p.write_bytes(raw)
            for r in found:r['source_path']=str(p)
            rows.extend(found)
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='unqualified_candidates',
        input_archive=pin,archive_sha256_reverified=True,manifest_sha256=sha(D/'manifest.json'),
        scanner_sha256=sha(Path(__file__)),xml_scanned=scanned,matched_articles=matched,
        candidate_count=len(rows),solvents_with_candidates=len({r['solvent'] for r in rows}),
        missing=sorted(set(s['name'] for s in targets.values())-{r['solvent'] for r in rows}),
        by_solvent=dict(collections.Counter(r['solvent'] for r in rows)),candidates=rows,adopted=False)
    target=D/'thermoml-volume-candidates.json'
    assert not target.exists(),'Preserve previous candidate scan'
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='candidates'}),flush=True)


if __name__=='__main__':main()
