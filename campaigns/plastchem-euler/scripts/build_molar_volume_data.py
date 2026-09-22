"""Extract primary 298.15 K densities and derive molar volumes with recorded provenance."""
import hashlib,json,math,xml.etree.ElementTree as E
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import Descriptors
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/thermodynamics-v1';source=P/'density-sources/PMC11477502.xml';root=E.fromstring(source.read_bytes())
table=next(t for t in root.findall('.//table-wrap') if ''.join(t.find('label').itertext())=='Table 1')
aliases={'Ethyl alcohol':'ethanol','n-Propyl alcohol':'1-propanol','Tetrahydrofuran':'tetrahydrofuran','N,N-Dimethylformamide':'n,n-dimethylformamide'}
inventory={r['solvent_key']:r for r in json.loads((P/'solvent-inventory.json').read_text())['solvents']};data={};section=None
for row in table.findall('.//tr'):
 cells=[' '.join(''.join(c.itertext()).split()) for c in row]
 if len(cells)==1:section=cells[0]
 if section in aliases and len(cells)==4 and cells[0]=='298.15':
  key=aliases[section];density=float(cells[1]);mol=Chem.MolFromSmiles(inventory[key]['smiles']);mass=Descriptors.MolWt(mol);uc=0.004
  data[key]={'molar_volume_cm3_mol':mass/density,'density_g_cm3':density,'temperature_K':298.15,'pressure_MPa':0.1,'density_expanded_uncertainty_g_cm3':uc,'density_uncertainty_confidence':0.95,'molar_volume_density_only_expanded_uncertainty_cm3_mol':mass*uc/density**2,'log10_volume_density_only_expanded_uncertainty':uc/(density*math.log(10)),'molar_mass_g_mol':mass,'molar_mass_method':'RDKit standard molecular weight from pinned solvent SMILES','source_inchikey':inventory[key]['connectivity_inchikey'],'source_doi':'10.3390/molecules29194521','source_url':'https://doi.org/10.3390/molecules29194521','table':'1, experimental row at 298.15 K','source_xml_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_xml_path':str(source),'uncertainty_note':'Published density uncertainty contribution only; not the total partition-model uncertainty.'}
assert len(data)==4
# Primary experimental column, not the adjacent literature comparison column.
source=P/'density-sources/PMC11040728.xml';root=E.fromstring(source.read_bytes())
table=next(t for t in root.findall('.//table-wrap') if ''.join(t.find('label').itertext())=='Table 3')
compound=None;selected=[]
for row in table.findall('.//tr'):
 cells=[' '.join(''.join(c.itertext()).split()) for c in row]
 if len(cells)!=6:continue
 if cells[0]:compound=cells[0]
 if compound=='dodecane' and cells[1]=='298.15':selected.append(cells)
assert len(selected)==1 and selected[0][2]=='0.7455'
density=float(selected[0][2]);key='dodecane';mass=Descriptors.MolWt(Chem.MolFromSmiles(inventory[key]['smiles']));u=0.0001
# Table 3: 0.1 kg/m3 standard uncertainty = 0.0001 g/cm3; not expanded uncertainty.
data[key]={'molar_volume_cm3_mol':mass/density,'density_g_cm3':density,'temperature_K':298.15,'pressure_MPa':0.1016,'density_standard_uncertainty_g_cm3':u,'molar_volume_density_only_standard_uncertainty_cm3_mol':mass*u/density**2,'log10_volume_density_only_standard_uncertainty':u/(density*math.log(10)),'molar_mass_g_mol':mass,'molar_mass_method':'RDKit standard molecular weight from pinned solvent SMILES','source_inchikey':inventory[key]['connectivity_inchikey'],'source_doi':'10.1021/acs.jcim.3c01737','source_url':'https://doi.org/10.1021/acs.jcim.3c01737','table':'3, dodecane experimental density column at 298.15 K','source_xml_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_xml_path':str(source),'uncertainty_note':'Published standard density uncertainty contribution only; not expanded uncertainty or total partition-model uncertainty.'}
# Additional pure-liquid measurements, same temperature, local atmospheric pressure.
source=P/'density-sources/PMC12613004.xml';root=E.fromstring(source.read_bytes())
table=next(t for t in root.findall('.//table-wrap') if t.find('label') is not None and ''.join(t.find('label').itertext())=='Table 2')
aliases={'methyl ethanoate':'methylacetate','ethyl ethanoate':'ethyl acetate'};found=set()
for row in table.findall('.//tr'):
 cells=[' '.join(''.join(c.itertext()).split()) for c in row]
 if not cells or cells[0] not in aliases:continue
 key=aliases[cells[0]];density=float(cells[1])/1000;mass=Descriptors.MolWt(Chem.MolFromSmiles(inventory[key]['smiles']));found.add(key)
 data[key]={'molar_volume_cm3_mol':mass/density,'density_g_cm3':density,'temperature_K':298.15,'pressure_MPa':0.098,'pressure_note':'Approximate local atmospheric pressure reported by authors','density_instrument_reading_g_cm3':0.00002,'molar_mass_g_mol':mass,'molar_mass_method':'RDKit standard molecular weight from pinned solvent SMILES','source_inchikey':inventory[key]['connectivity_inchikey'],'source_doi':'10.1016/j.dib.2025.112201','source_url':'https://doi.org/10.1016/j.dib.2025.112201','table':'2, pure compound experimental density column','source_xml_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_xml_path':str(source),'uncertainty_note':'Authors give instrument reading ±0.02 kg/m3; no total pure-density uncertainty is inferred from this specification.'}
assert found==set(aliases.values())
source=P/'density-sources/PMC11477799.xml';root=E.fromstring(source.read_bytes());table=root.find('.//table-wrap');compound=None;selected=[]
for row in table.findall('.//tr'):
 cells=[' '.join(''.join(c.itertext()).split()) for c in row]
 if cells and cells[0] in ['Toluene','2-propanol','2-methyl-1-propanol']:compound=cells[0]
 if compound=='Toluene' and cells and cells[0]=='298.15':selected.append(cells)
assert len(selected)==1 and selected[0][1]=='0.8622'
key='toluene';density=float(selected[0][1]);mass=Descriptors.MolWt(Chem.MolFromSmiles(inventory[key]['smiles']));uc=0.0006
data[key]={'molar_volume_cm3_mol':mass/density,'density_g_cm3':density,'temperature_K':298.15,'pressure_MPa':0.1,'density_expanded_uncertainty_g_cm3':uc,'density_uncertainty_confidence':0.95,'molar_volume_density_only_expanded_uncertainty_cm3_mol':mass*uc/density**2,'log10_volume_density_only_expanded_uncertainty':uc/(density*math.log(10)),'molar_mass_g_mol':mass,'molar_mass_method':'RDKit standard molecular weight from pinned solvent SMILES','source_inchikey':inventory[key]['connectivity_inchikey'],'source_doi':'10.3390/molecules29194706','source_url':'https://doi.org/10.3390/molecules29194706','table':'1, toluene experimental row at 298.15 K','source_xml_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_xml_path':str(source),'uncertainty_note':'Published density uncertainty contribution only; not total partition-model uncertainty.'}
from thermoml_density_additions import extract
thermoml=extract(P,inventory)
assert not (set(thermoml)&set(data))
data.update(thermoml)
from thermoml_additional_selected import extract as extract_more
more=extract_more(P,inventory)
assert not (set(more)&set(data))
data.update(more)
result={'status':'verified_primary_density_additions','existing_five_reference_volumes_unchanged':True,'entries':data}
(P/'additional-molar-volumes.json').write_text(json.dumps(result,indent=2)+'\n')
for k,v in data.items():print(k,v['density_g_cm3'],v['molar_volume_cm3_mol'])
