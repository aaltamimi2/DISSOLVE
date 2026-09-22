"""Validate individually selected near-atmospheric pure-liquid density records."""
import hashlib,json,math,xml.etree.ElementTree as E
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import Descriptors

def extract(base,inventory):
 rows=json.loads((base/'thermoml-density-selected-next.json').read_text())['rows'];out={}
 for selected in rows:
  key=selected['solvent'];source=Path(selected['source_path']);raw=source.read_bytes();assert hashlib.sha256(raw).hexdigest()==selected['source_sha256']
  root=E.fromstring(raw)
  for n in root.iter():n.tag=n.tag.split('}')[-1]
  assert root.findtext('Citation/sDOI')==selected['doi']
  b=next(b for b in root.findall('PureOrMixtureData') if b.findtext('nPureOrMixtureDataNumber')==selected['block'])
  assert len(b.findall('Component'))==1 and b.findtext('PhaseID/ePhase')=='Liquid'
  org=b.findtext('Component/RegNum/nOrgNum');c=next(c for c in root.findall('Compound') if c.findtext('RegNum/nOrgNum')==org)
  assert c.findtext('sStandardInChIKey')==inventory[key]['connectivity_inchikey']==selected['source_key']
  nv=b.findall('NumValues')[selected['row_index']]
  conditions={x.findtext('.//ConstraintType/*'):float(x.findtext('nConstraintValue')) for x in b.findall('Constraint')}
  variables={x.findtext('nVarNumber'):x.findtext('.//VariableType/*') for x in b.findall('Variable')}
  for v in nv.findall('VariableValue'):conditions[variables[v.findtext('nVarNumber')]]=float(v.findtext('nVarValue'))
  assert set(conditions)=={'Temperature, K','Pressure, kPa'} and conditions['Temperature, K']==298.15 and 95<=conditions['Pressure, kPa']<=105
  prop=next(x for x in b.findall('Property') if x.findtext('.//ePropName')=='Mass density, kg/m3')
  assert prop.findtext('ePresentation')=='Direct value, X' and prop.findtext('PropPhaseID/ePropPhase')=='Liquid'
  val=next(x for x in nv.findall('PropertyValue') if x.findtext('nPropNumber')==prop.findtext('nPropNumber'))
  density=float(val.findtext('nPropValue'));assert density==selected['density_kg_m3'];rho=density/1000
  uc=float(val.findtext('CombinedUncertainty/nCombExpandUncertValue'))/1000
  meta=next(u for u in prop.findall('CombinedUncertainty') if u.findtext('nCombUncertAssessNum')==val.findtext('CombinedUncertainty/nCombUncertAssessNum'))
  confidence=float(meta.findtext('nCombUncertLevOfConfid'))/100;assert confidence==.95
  mass=Descriptors.MolWt(Chem.MolFromSmiles(inventory[key]['smiles']))
  out[key]={'molar_volume_cm3_mol':mass/rho,'density_g_cm3':rho,'temperature_K':298.15,'pressure_MPa':conditions['Pressure, kPa']/1000,'density_expanded_uncertainty_g_cm3':uc,'density_uncertainty_confidence':confidence,'uncertainty_evaluator':meta.findtext('sCombUncertEvaluator'),'uncertainty_method':meta.findtext('eCombUncertEvalMethod'),'molar_volume_density_only_expanded_uncertainty_cm3_mol':mass*uc/rho**2,'log10_volume_density_only_expanded_uncertainty':uc/(rho*math.log(10)),'molar_mass_g_mol':mass,'molar_mass_method':'RDKit standard molecular weight from pinned solvent SMILES','source_inchikey':selected['source_key'],'source_doi':selected['doi'],'source_url':'https://doi.org/'+selected['doi'],'data_provider':'NIST/TRC ThermoML archive, DOI 10.18434/mds2-2422','data_block':selected['block'],'row_index':selected['row_index'],'source_xml_path':str(source),'source_xml_sha256':selected['source_sha256'],'uncertainty_note':'NIST/TRC combined expanded density uncertainty, including compiler evaluation; not author-reported uncertainty or total partition-model uncertainty.'}
 if 'tert-butanol' in out:
  out['tert-butanol']['phase_interpretation']='Measured liquid density; used for hypothetical/subcooled liquid-reference calculation at 298.15 K, not solid-phase partitioning.'
 return out
