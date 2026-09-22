"""Separate all-Milan production from historical workstation/hybrid anchor checks."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BULK=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))
ANCHORS={'DEP':'FLKPEMZONWLCSK-UHFFFAOYSA-N','DBP':'DOIRQSBPFJWKBE-UHFFFAOYSA-N',
         'BBP':'IRIAEXORFWYRCZ-UHFFFAOYSA-N','DEHP':'BJQHLKABXJIVAM-UHFFFAOYSA-N'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');args=parser.parse_args()
    numeric=BULK/'sealed-thermodynamics' if args.frozen else ROOT/'state/thermodynamics-v1'
    history=numeric/'workstation-anchor-sources' if args.frozen else ROOT/'state/opencosmo-verification-v1/records'
    ledger=json.loads((numeric/'processing-ledger.json').read_text())
    rows=[]
    for anchor,key in ANCHORS.items():
        entry=ledger[key];payload=Path(entry['result_path']).read_bytes()
        assert hashlib.sha256(payload).hexdigest()==entry['result_sha256']
        production=json.loads(payload)
        pair=next(p for p in production['partitions_against_water'] if p['solvent']=='dichloromethane')
        assert pair['status']=='predicted' and pair['temperature_K']==298.15
        historical=[]
        for name in [anchor+'-workstation-reference.json',key+'.json']:
            raw=(history/name).read_bytes();record=json.loads(raw)
            p=next(p for p in record['pairs'] if p['solvent']=='dichloromethane' and p['reference']=='water')
            historical.append((record,p,hashlib.sha256(raw).hexdigest()))
        workstation,hybrid=historical
        value=pair['log10_K_concentration']
        rows.append({'anchor':anchor,'input_inchikey':key,'solvent':'dichloromethane','reference':'water',
                     'temperature_K':298.15,'all_Milan_log10_K_concentration':value,
                     'historical_workstation_log10_K_concentration':workstation[1]['log10_partition_concentration_basis'],
                     'Milan_solute_historical_solvents_log10_K_concentration':hybrid[1]['log10_partition_concentration_basis'],
                     'production_minus_workstation':value-workstation[1]['log10_partition_concentration_basis'],
                     'production_minus_hybrid':value-hybrid[1]['log10_partition_concentration_basis'],
                     'production_cpu':production['cpu_model'],'workstation_cpu':'13th Gen Intel Core i7-13700',
                     'production_solute_surface_sha256':production['solute_surface_sha256'],
                     'production_solvent_surface_sha256':pair['solvent_surface_sha256'],
                     'production_water_surface_sha256':pair['reference_surface_sha256'],
                     'production_result_sha256':entry['result_sha256'],
                     'workstation_result_sha256':workstation[2],'hybrid_result_sha256':hybrid[2],
                     'production_solute_fraction_water':production['activities']['water']['selected_solute_fraction'],
                     'production_solute_fraction_DCM':production['activities']['dichloromethane']['selected_solute_fraction'],
                     'historical_solute_fraction':1e-5,
                     'interpretation':'Preparation, geometry, solvent surfaces and dilution stopping differ; this is a documented implementation/production comparison, not an experimental accuracy test or isolated CPU effect.'})
    name='production-anchor-agreement.csv' if args.frozen else 'preview-production-anchor-agreement.csv'
    with (BULK/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps({'frozen':args.frozen,'anchor_count':len(rows),
                      'max_abs_production_minus_workstation':max(abs(r['production_minus_workstation']) for r in rows),
                      'max_abs_production_minus_hybrid':max(abs(r['production_minus_hybrid']) for r in rows),
                      'rows':[{k:r[k] for k in ['anchor','all_Milan_log10_K_concentration','production_minus_workstation','production_minus_hybrid']} for r in rows]}))

if __name__=='__main__':main()
