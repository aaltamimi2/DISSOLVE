"""Consumer-side full-table, missingness and figure-data reconciliation."""
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BULK=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');args=parser.parse_args()
    source=BULK/'freeze' if args.frozen else ROOT
    numeric=BULK/'sealed-thermodynamics' if args.frozen else ROOT/'state/thermodynamics-v1'
    table=BULK/'partitioning-frozen.csv' if args.frozen else Path('/mnt/r/plastchem-euler/thermodynamics-v1/partitioning-current.csv')
    figures=BULK/('figures' if args.frozen else 'preview-figures')
    export=json.loads((numeric/'table-export.json').read_text())
    assert digest(table)==export['csv_sha256'], 'Table differs from completed export receipt'
    eligible={r['inchikey'] for r in json.loads((source/'state/campaign-v1/eligible.json').read_text())}
    registry=json.loads((numeric/'library-registry.json').read_text())
    solvents={s['solvent_key'] for s in registry['solvents'] if s['solvent_key']!='water'}
    records={p.stem:json.loads(p.read_text()) for p in (source/'state/campaign-v1/records').glob('*.json')}
    phases={r['solvent']:r for r in json.loads((numeric/'solvent-phase-review.json').read_text())['entries']}
    assert len(eligible)==5824 and len(solvents)==32
    seen=set();statuses=Counter();per_key=Counter();numeric_rows={};missing_count=0
    with table.open(newline='') as f:
        for row in csv.DictReader(f):
            key=row['input_inchikey'];name=row['solvent'];pair=(key,name)
            assert key in eligible and name in solvents and pair not in seen
            seen.add(pair);per_key[key]+=1;statuses[row['status']]+=1
            assert row['reference']=='water' and float(row['temperature_K'])==298.15
            assert row['parameterization']=='openCOSMORS24a' and row['phase_basis']=='liquid_reference'
            if name in phases:assert row['solvent_phase_note']==phases[name]['required_result_note']
            record=records.get(key,{})
            if args.frozen:assert row['orca_status']==record.get('status','not_yet_run')
            if row['status']=='predicted':
                assert row['orca_status']=='converged' and record['status']=='converged'
                assert key.split('-')[0]==row['perceived_inchikey'].split('-')[0]
                assert row['identity_match_basis']=='connectivity_first_block'
                assert row['cpu_model']=='AMD EPYC 7763 64-Core Processor'
                assert json.loads(row['perceived_keys_by_engine'])==record['perceived_keys_by_engine']
                assert json.loads(row['perception_engines_agreeing_on_perceived_key'])==record['perception_engines_agreeing_on_perceived_key']
                assert row['solute_surface_sha256']==record['surface_sha256']
                assert math.isfinite(float(row['log10_K_mole_fraction']))
                if row['log10_K_concentration']:
                    assert math.isfinite(float(row['log10_K_concentration']))
                    assert abs(float(row['log10_K_concentration'])-float(row['log10_K_mole_fraction'])-float(row['volume_correction_log10']))<1e-12
                    assert json.loads(row['volume_source'])
                    numeric_rows[pair]=row
                else:assert row['concentration_basis_status']=='missing_documented_molar_volume'
            else:
                assert not row['log10_K_mole_fraction'] and not row['log10_K_concentration']
                missing_count+=1
            if row['orca_status']=='failed':
                assert row['status']=='orca_or_preparation_failed' and row['reason']
    assert len(seen)==5824*32 and set(per_key)==eligible and set(per_key.values())=={32}
    assert dict(statuses)==export['row_status_counts']
    assert len(numeric_rows)==export['concentration_prediction_rows']
    plotted=set()
    with (figures/'computed-partition-values.csv').open(newline='') as f:
        for row in csv.DictReader(f):
            pair=(row['input_inchikey'],row['solvent']);assert pair not in plotted
            plotted.add(pair);full=numeric_rows[pair]
            assert float(row['log10_K_concentration'])==float(full['log10_K_concentration'])
            assert row['result_sha256']==full['result_sha256']
            assert row['perceived_inchikey']==full['perceived_inchikey']
    assert plotted==set(numeric_rows), 'Plot dataset is not the available full-table dataset'
    fm=json.loads((figures/'figure-manifest.json').read_text())
    for name,expected in fm['files'].items():assert digest(figures/name)==expected
    assert digest(table)==export['csv_sha256'], 'Table changed during audit'
    result={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'frozen':args.frozen,
            'status':'passed','eligible_molecules':len(eligible),'pairs_per_molecule':len(solvents),
            'rows_checked':len(seen),'concentration_prediction_rows':len(numeric_rows),
            'unavailable_rows_checked_blank':missing_count,'status_counts':dict(statuses),
            'table_sha256':export['csv_sha256'],'plot_data_exactly_reconciled':True,
            'interpretation':'Data integrity, identities, missingness and rendering-data reconciliation; not experimental accuracy or full campaign completion.'}
    target=BULK/'table-audit.json' if args.frozen else ROOT/'state/progress-2026-09-14/live-table-audit.json'
    target.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':main()
