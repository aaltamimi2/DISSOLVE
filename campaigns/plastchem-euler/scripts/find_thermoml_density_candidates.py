"""Scan the pinned archive for pure-liquid, near-atmospheric 298.15 K density candidates."""
import json,tarfile,xml.etree.ElementTree as E,hashlib,time
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'state/thermodynamics-v1'
missing=set(json.loads((P/'density-coverage.json').read_text())['missing_solvents'])
targets={r['connectivity_inchikey'].split('-')[0]:r for r in json.loads((P/'solvent-inventory.json').read_text())['solvents'] if r['solvent_key'] in missing and r.get('connectivity_inchikey')}
archive=json.loads((P/'thermoml-download.json').read_text());rows=[];scanned=0
with tarfile.open(archive['path'],'r|gz') as tar:
 for member in tar:
  if not member.isfile() or not member.name.endswith('.xml'):continue
  raw=tar.extractfile(member).read();scanned+=1
  if not any(k.encode() in raw for k in targets):continue
  root=E.fromstring(raw)
  for n in root.iter():n.tag=n.tag.split('}')[-1]
  compounds={c.findtext('RegNum/nOrgNum'):c for c in root.findall('Compound')}
  for block in root.findall('PureOrMixtureData'):
   if len(block.findall('Component'))!=1 or block.findtext('PhaseID/ePhase')!='Liquid':continue
   c=compounds[block.findtext('Component/RegNum/nOrgNum')];key=c.findtext('sStandardInChIKey','');target=targets.get(key.split('-')[0])
   if target is None:continue
   props={p.findtext('nPropNumber'):p for p in block.findall('Property') if p.findtext('.//ePropName')=='Mass density, kg/m3' and p.findtext('ePresentation')=='Direct value, X'}
   if not props:continue
   constants={c.findtext('.//ConstraintType/*'):float(c.findtext('nConstraintValue')) for c in block.findall('Constraint')}
   variables={v.findtext('nVarNumber'):v.findtext('.//VariableType/*') for v in block.findall('Variable')}
   for i,nv in enumerate(block.findall('NumValues')):
    conditions=dict(constants)
    for v in nv.findall('VariableValue'):conditions[variables[v.findtext('nVarNumber')]]=float(v.findtext('nVarValue'))
    if conditions.get('Temperature, K')!=298.15 or not 95<=conditions.get('Pressure, kPa',-1)<=105:continue
    for val in nv.findall('PropertyValue'):
     prop=props.get(val.findtext('nPropNumber'))
     if prop is None:continue
     rows.append(dict(solvent=target['solvent_key'],input_key=target['connectivity_inchikey'],source_key=key,full_key_match=key==target['connectivity_inchikey'],archive_member=member.name,source_sha256=hashlib.sha256(raw).hexdigest(),doi=root.findtext('Citation/sDOI'),block=block.findtext('nPureOrMixtureDataNumber'),row_index=i,conditions=conditions,density_kg_m3=float(val.findtext('nPropValue')),expanded_uncertainty=val.findtext('CombinedUncertainty/nCombExpandUncertValue'),property_metadata=E.tostring(prop,encoding='unicode')))
result=dict(utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),archive_sha256=archive['sha256'],xml_scanned=scanned,candidate_count=len(rows),candidates=rows,adopted=False)
(P/'thermoml-density-candidates.json').write_text(json.dumps(result,indent=2)+'\n')
from collections import Counter
print(json.dumps(dict(xml_scanned=scanned,candidate_count=len(rows),by_solvent=dict(Counter(r['solvent'] for r in rows)))))
