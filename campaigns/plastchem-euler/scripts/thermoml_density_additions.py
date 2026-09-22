"""Selected pure-liquid density measurements from the checksum-verified NIST archive."""
import hashlib,math,xml.etree.ElementTree as E
from rdkit import Chem
from rdkit.Chem import Descriptors
SELECTIONS=[('acetone','je900523k',6),('2-butanone','je900523k',9),('chloroform','je800157v',2),('benzene','je900194v',2),('o-xylene','je900194v',6),('cyclohexane','je900468u',3),('ethylene glycol','je100089s',1),('dimethyl sulfoxide','je100089s',3)]
def extract(base,inventory):
 out={}
 for key,article,block in SELECTIONS:
  source=base/'density-sources/thermoml'/(article+'.xml');root=E.fromstring(source.read_bytes())
  for node in root.iter():node.tag=node.tag.split('}')[-1]
  b=next(b for b in root.findall('PureOrMixtureData') if int(b.findtext('nPureOrMixtureDataNumber'))==block)
  assert len(b.findall('Component'))==1 and b.findtext('PhaseID/ePhase')=='Liquid'
  org=b.findtext('Component/RegNum/nOrgNum');compound=next(c for c in root.findall('Compound') if c.findtext('RegNum/nOrgNum')==org)
  assert compound.findtext('sStandardInChIKey')==inventory[key]['connectivity_inchikey'],key
  constraints=b.findall('Constraint');variables=b.findall('Variable')
  assert len(constraints)==1 and constraints[0].findtext('.//ConstraintType/ePressure')=='Pressure, kPa'
  pressure=float(constraints[0].findtext('nConstraintValue'));assert pressure==101
  assert len(variables)==1 and variables[0].findtext('.//VariableType/eTemperature')=='Temperature, K'
  var=variables[0].findtext('nVarNumber');prop=next(p for p in b.findall('Property') if p.findtext('.//ePropName')=='Mass density, kg/m3')
  assert prop.findtext('ePresentation')=='Direct value, X' and prop.findtext('PropPhaseID/ePropPhase')=='Liquid'
  rows=[v for v in b.findall('NumValues') if v.findtext('VariableValue/nVarNumber')==var and float(v.findtext('VariableValue/nVarValue'))==298.15];assert len(rows)==1
  val=next(v for v in rows[0].findall('PropertyValue') if v.findtext('nPropNumber')==prop.findtext('nPropNumber'))
  rho=float(val.findtext('nPropValue'))/1000;uc=float(val.findtext('CombinedUncertainty/nCombExpandUncertValue'))/1000
  assessment=val.findtext('CombinedUncertainty/nCombUncertAssessNum');meta=next(u for u in prop.findall('CombinedUncertainty') if u.findtext('nCombUncertAssessNum')==assessment)
  confidence=float(meta.findtext('nCombUncertLevOfConfid'))/100;assert confidence==.95
  mass=Descriptors.MolWt(Chem.MolFromSmiles(inventory[key]['smiles']));doi=root.findtext('Citation/sDOI')
  out[key]={'molar_volume_cm3_mol':mass/rho,'density_g_cm3':rho,'temperature_K':298.15,'pressure_MPa':pressure/1000,'density_expanded_uncertainty_g_cm3':uc,'density_uncertainty_confidence':confidence,'uncertainty_evaluator':meta.findtext('sCombUncertEvaluator'),'uncertainty_method':meta.findtext('eCombUncertEvalMethod'),'molar_volume_density_only_expanded_uncertainty_cm3_mol':mass*uc/rho**2,'log10_volume_density_only_expanded_uncertainty':uc/(rho*math.log(10)),'molar_mass_g_mol':mass,'molar_mass_method':'RDKit standard molecular weight from pinned solvent SMILES','source_inchikey':compound.findtext('sStandardInChIKey'),'source_doi':doi,'source_url':'https://doi.org/'+doi,'data_provider':'NIST/TRC ThermoML archive, DOI 10.18434/mds2-2422','data_block':block,'source_xml_path':str(source),'source_xml_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'uncertainty_note':'NIST/TRC combined expanded density uncertainty, including compiler evaluation; not represented as the author-reported uncertainty or total partition-model uncertainty.'}
 return out
