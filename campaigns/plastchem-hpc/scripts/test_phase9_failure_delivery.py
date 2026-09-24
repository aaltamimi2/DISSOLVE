"""Actual recovered failure through CSV/Parquet plus independent negative controls."""
import csv,datetime,gzip,json,tempfile
from pathlib import Path
import duckdb
from build_phase9_release import LLE,parquet
from verify_phase9_delivery import verify_failure_exports
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase9-v1')

def main():
    original=json.loads((D/'recovery-first-nonconvergence-row.json').read_text())['value']
    row={key:original.get(key,'') for key in LLE}
    row.update(input_inchikey=original['inchikey'],product_solvent_key=original['solvent'],temperature_regime=original['regime'],
               value_validated=False,tie_lines_json='[]',grid_checks_json='[]',
               failure_evidence_json=json.dumps({key:original[key] for key in ['exception_type','exception_message','traceback','failure_policy_sha256','recovery_policy']}))
    checks=[]
    with tempfile.TemporaryDirectory(prefix='failure-delivery-',dir=D) as directory:
        folder=Path(directory);csvfile=folder/'failure.csv.gz';pq=folder/'failure.parquet'
        with gzip.open(csvfile,'wt',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=LLE);writer.writeheader();writer.writerow(row)
        con=duckdb.connect();con.execute("SET threads=1; SET memory_limit='256MB'");con.execute('SET temp_directory=?',[directory])
        assert parquet(con,csvfile,LLE,pq)==1
        con.execute('CREATE TABLE lle_rows AS SELECT * FROM read_parquet(?)',[str(pq)])
        assert verify_failure_exports(con,R/'scripts',D)==1
        checks.append('Actual recovered row survives the release CSV-to-Parquet path and independent export checks')
        def reject(label):
            try:verify_failure_exports(con,R/'scripts',D)
            except (AssertionError,ValueError,KeyError):checks.append(label)
            else:raise AssertionError('Accepted corrupted failure: '+label)
        numeric=['solute_mole_fraction_solubility','solute_wt_percent_solubility','x_contaminant_solvent_rich','x_solvent_solvent_rich','x_contaminant_solute_rich','x_solvent_solute_rich',
                 'wt_percent_contaminant_solvent_rich','wt_percent_contaminant_solute_rich','grid_change_mol_percentage_points','grid_change_wt_percentage_points','max_chemical_potential_residual_RT','minimum_tangent_distance_RT']
        for field in numeric:
            con.execute('UPDATE lle_rows SET '+field+'=0.0');reject('Reject fabricated '+field);con.execute('UPDATE lle_rows SET '+field+'=NULL')
        for field in ['above_15_mol_percent','above_15_wt_percent']:
            con.execute('UPDATE lle_rows SET '+field+'=FALSE');reject('Reject false-valued unresolved verdict '+field);con.execute('UPDATE lle_rows SET '+field+'=NULL')
        for field,value in [('failure_evidence_json','{}'),('tie_lines_json','[{}]'),('grid_checks_json','[{}]'),('failure_mode','unknown'),('status','unrecognized_status')]:
            con.execute('UPDATE lle_rows SET '+field+'=?',[value]);reject('Reject corrupted '+field);con.execute('UPDATE lle_rows SET '+field+'=?',[row[field]])
        assert verify_failure_exports(con,R/'scripts',D)==1
        con.close()
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='passed',checks=checks,count=len(checks),scope='One actual recovered failure exported in a bulk temporary fixture; all 12 numerical result fields, both false-valued verdicts, evidence, empty grids/ties, mode and status checked. Source data and jobs unchanged.')
    (D/'failure-delivery-negative-controls.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
