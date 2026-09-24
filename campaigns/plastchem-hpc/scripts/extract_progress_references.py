"""Extract observed values only from the pinned Liang et al. supporting table."""
import csv
import hashlib
import json
import datetime as dt
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/mnt/r/plastchem-euler/progress-2026-09-14')
SOURCE = OUT / 'reference-sources/es7b01737_si_001.xlsx'
EXPECTED = '00679575c5bd77508732e61c66f4c9da8115b03b871e1f1742b695b4062d165d'
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == EXPECTED
eligible = json.loads((ROOT / 'state/campaign-v1/eligible.json').read_text())
by_cas = {}
for r in eligible:
    cas = r.get('cas', '').lstrip('0')
    if cas:
        by_cas.setdefault(cas, []).append(r)
inventory = json.loads((ROOT / 'state/thermodynamics-v1/solvent-inventory.json').read_text())
solvents = {r['solvent_key'] for r in inventory['solvents']}
aliases = {'dimethylformamide': 'n,n-dimethylformamide', 'propanone': 'acetone'}
workbook = openpyxl.load_workbook(SOURCE, read_only=True, data_only=True)
rows = []
for number, row in enumerate(workbook['Table S5'].iter_rows(min_row=4, values_only=True), 4):
    if not row[4] or not isinstance(row[7], (int, float)):
        continue
    cas = str(row[4]).lstrip('0')
    for target in by_cas.get(cas, []):
        solvent = aliases.get(str(row[3]).lower(), str(row[3]).lower())
        rows.append({'inchikey': target['inchikey'], 'name': target['name'],
                     'cas': cas, 'reference_name': row[2], 'solvent': solvent,
                     'reference': 'water', 'phase_annotation': row[6],
                     'observed_log10_partition': row[7],
                     'reference_class': 'published_experimental_compilation',
                     'identity_basis': 'exact_CAS_after_leading_zero_normalization',
                     'ambiguous_campaign_CAS': len(by_cas[cas]) != 1,
                     'solvent_in_campaign_panel': solvent in solvents,
                     'temperature_K': '',
                     'temperature_note': 'Table S5 has no row-specific temperature; requires source-method review',
                     'source_doi': '10.1021/acs.est.7b01737.s001',
                     'source_url': 'https://doi.org/10.1021/acs.est.7b01737.s001',
                     'source_sha256': EXPECTED, 'source_sheet': 'Table S5',
                     'source_row': number, 'source_column': 'H (Observed)'})
epa_file = OUT / 'reference-sources/epa-slow-stir-experimental.json'
if epa_file.exists():
    epa = json.loads(epa_file.read_text())
    assert hashlib.sha256(Path(epa['source_file']).read_bytes()).hexdigest() == epa['source_sha256']
    for observation in epa['rows']:
        for target in by_cas.get(observation['cas'], []):
            rows.append({'inchikey': target['inchikey'], 'name': target['name'],
                         'cas': observation['cas'], 'reference_name': observation['name'],
                         'solvent': 'octanol', 'reference': 'water',
                         'phase_annotation': 'mutually equilibrated octanol/water',
                         'observed_log10_partition': observation['measured_logKow'],
                         'reference_class': 'primary_experimental_slow_stir',
                         'identity_basis': 'exact_CAS',
                         'ambiguous_campaign_CAS': len(by_cas[observation['cas']]) != 1,
                         'solvent_in_campaign_panel': False, 'temperature_K': 298.15,
                         'temperature_note': 'EPA procedure explicitly specifies 25 C',
                         'source_doi': '', 'source_url': epa['source_url'],
                         'source_sha256': epa['source_sha256'],
                         'source_sheet': 'EPA/600/S-96/006 Table 1',
                         'source_row': observation['name'], 'source_column': 'Slow-Stir (measured)',
                         'retrieved_utc': epa['retrieved_utc'],
                         'reported_plus_minus': observation['reported_plus_minus'],
                         'reported_n': observation['reported_n']})
bound_file = OUT/'reference-sources/epa-phenolic-benzotriazoles-experimental.json'
if bound_file.exists():
    source = json.loads(bound_file.read_text())
    assert hashlib.sha256(Path(source['source_file']).read_bytes()).hexdigest() == source['source_sha256']
    for observation in source['rows']:
        for target in by_cas.get(observation['cas'], []):
            rows.append({'inchikey':target['inchikey'], 'name':target['name'],
                         'cas':observation['cas'], 'reference_name':observation['name'],
                         'solvent':'octanol', 'reference':'water', 'phase_annotation':'not specified in EPA summary',
                         'observed_log10_partition':observation['value'], 'observed_operator':observation['operator'],
                         'reference_class':'EPA_reported_measured_lower_bound', 'identity_basis':'exact_CAS',
                         'ambiguous_campaign_CAS':len(by_cas[observation['cas']])!=1,
                         'solvent_in_campaign_panel':False, 'temperature_K':observation['temperature_K'],
                         'temperature_note':'Temperature not specified in summary table', 'source_doi':'',
                         'source_url':source['source_url'], 'source_sha256':source['source_sha256'],
                         'source_sheet':source['source_table'], 'source_row':observation['cas'],
                         'source_column':'Log Kow, measured lower bound', 'retrieved_utc':source['retrieved_utc'],
                         'condition_limitation':observation['limitation']})
perylene_file = OUT/'reference-sources/pubchem-perylene-logP.json'
if perylene_file.exists():
    receipt = json.loads((OUT/'reference-sources/pubchem-perylene-retrieval.json').read_text())
    assert hashlib.sha256(perylene_file.read_bytes()).hexdigest() == receipt['sha256']
    record = json.loads(perylene_file.read_text())['Record']
    assert record['RecordNumber']==9142 and record['RecordTitle']=='Perylene'
    hsdb = next(r for r in record['Reference'] if r['SourceName']=='Hazardous Substances Data Bank (HSDB)')
    info = record['Section'][0]['Section'][0]['Section'][0]['Information']
    observation = next(r for r in info if r['ReferenceNumber']==hsdb['ReferenceNumber'])
    assert observation['Value']['StringWithMarkup'][0]['String']=='log Kow = 6.30'
    assert any('3610-3614 (1999)' in c for c in observation['Reference'])
    for target in by_cas.get('198-55-0', []):
        rows.append({'inchikey':target['inchikey'], 'name':target['name'], 'cas':'198-55-0',
                     'reference_name':'Perylene', 'solvent':'octanol', 'reference':'water',
                     'phase_annotation':'octanol/water; exact saturation conditions not verified',
                     'observed_log10_partition':6.30, 'reference_class':'HSDB_reported_experimental_with_primary_citation',
                     'identity_basis':'exact_CAS_and_PubChem_record_identity',
                     'ambiguous_campaign_CAS':len(by_cas['198-55-0'])!=1, 'solvent_in_campaign_panel':False,
                     'temperature_K':'', 'temperature_note':'Temperature not specified in HSDB entry',
                     'source_doi':'10.1021/ac9902291', 'source_url':receipt['url'],
                     'source_sha256':receipt['sha256'], 'source_sheet':'PubChem Experimental Properties / LogP / HSDB',
                     'source_row':'CID 9142, HSDB 6767', 'source_column':'ReferenceNumber 27; log Kow',
                     'retrieved_utc':receipt['retrieved_utc'],
                     'condition_source_url':'https://pubs.acs.org/doi/10.1021/ac9902291',
                     'condition_limitation':'Value from peer-reviewed HSDB entry citing Andersson and Schrader (1999). Publisher abstract verifies a direct dialysis partition method; primary data table and temperature not independently retrieved. SangsterLogP value not extracted.'})
pubchem_additions = OUT/'reference-sources/pubchem-qualified-additions.json'
if pubchem_additions.exists():
    for row in json.loads(pubchem_additions.read_text())['rows']:
        key = row['inchikey']
        identity = json.loads((OUT/'reference-sources'/f'pubchem-expansion-{key}-identity.json').read_text())
        assert identity['PropertyTable']['Properties'][0]['InChIKey'] == key
        source = OUT/'reference-sources'/f'pubchem-expansion-{key}-logP.json'
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row['source_sha256']
        assert row['reference_class'] == 'PubChem_HSDB_measured_with_primary_citation'
        rows.append(row)
for row in rows:
    row.setdefault('observed_operator', '=')
    row.setdefault('retrieved_utc', dt.datetime.fromtimestamp(SOURCE.stat().st_mtime, dt.timezone.utc).isoformat())
    row.setdefault('reported_plus_minus', '')
    row.setdefault('reported_n', '')
    row.setdefault('condition_source_url', '')
    row.setdefault('condition_limitation', '')
    if row['inchikey']=='FLKPEMZONWLCSK-UHFFFAOYSA-N' and row['solvent']=='chloroform':
        review=json.loads((OUT/'reference-sources/DEP-chloroform-condition-review.json').read_text())
        assert float(row['observed_log10_partition'])==review['water_to_solvent_logP']
        row['temperature_K']=review['temperature_K']
        row['temperature_note']='Sprunger et al. (2009), accepted manuscript page 8; Tables 1–4 at 298.15 K'
        row['phase_annotation']='Wet: mutually saturated chloroform/water phases'
        row['condition_source_url']=review['source_url']
        row['condition_limitation']=review['comparison_limitation']
assert rows
with (OUT / 'experimental-reference-candidates.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
summary = {'source_sha256': EXPECTED, 'eligible_denominator': len(eligible),
           'candidate_rows': len(rows), 'candidate_molecules': len({r['inchikey'] for r in rows}),
           'panel_candidate_rows': sum(r['solvent_in_campaign_panel'] for r in rows),
           'censored_reference_rows':sum(r['observed_operator']!='=' for r in rows),
           'interpretation': 'Observed column only, never COSMO-SAC/ABSOLV/QCAP predictions. Candidate references require identity, temperature and phase review before error statistics. Source overlap is not an exhaustive literature census.'}
(ROOT / 'state/progress-2026-09-14/experimental-reference-coverage.json').write_text(json.dumps(summary, indent=2)+'\n')
print(json.dumps(summary))
