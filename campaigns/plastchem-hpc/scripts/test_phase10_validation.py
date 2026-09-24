"""Adverse controls for independent extension arithmetic and reference checks."""
import copy
import json
import math
import unittest
import tempfile
import csv
import gzip
from unittest.mock import patch
from pathlib import Path

from build_phase10_primary_reference import extract
from phase10_lle_audit import check
from qualify_phase10_thermoml_volumes import recheck
from audit_phase10_results import check_partition,check_tail_evidence
import audit_phase10_results as extension_audit
from build_phase10_release import partition_row
from build_phase10_release import PART,parquet
from verify_phase10_delivery import verify_partition_arithmetic
import duckdb


class ValidationTests(unittest.TestCase):
    def single_phase(self):
        row=dict(status='single_liquid_phase',solute_mole_fraction_solubility=1.,
            solute_wt_percent_solubility=100.,tie_lines=[])
        return dict(row,value_validated=True,
            grid_checks=[dict(row,grid_intervals=n) for n in [1000,2000]],
            grid_change_mol_percentage_points=0.,grid_change_wt_percentage_points=0.,
            above_15_mol_percent=True,above_15_wt_percent=True)

    def test_valid_single_phase_and_wrong_mass_or_verdict(self):
        r=self.single_phase();check(r,100.,18.,'pin')
        for field,value in [('solute_wt_percent_solubility',10.),('above_15_wt_percent',False),('value_validated',False)]:
            bad=copy.deepcopy(r);bad[field]=value
            with self.assertRaises(AssertionError):check(bad,100.,18.,'pin')

    def test_nonconvergence_cannot_be_promoted(self):
        message='COSMOspace did not converge for binary grid'
        r=dict(status='activity_nonconvergence',value_validated=False,
            failure_mode='cosmospace_binary_grid_nonconvergence',exception_type='ValueError',
            exception_message=message,traceback='ValueError: '+message,failure_policy_sha256='pin',
            grid_checks=[],tie_lines=[],solute_mole_fraction_solubility=None,solute_wt_percent_solubility=None,
            above_15_mol_percent=None,above_15_wt_percent=None)
        check(r,100.,18.,'pin')
        for field in ['solute_wt_percent_solubility','above_15_wt_percent']:
            bad=copy.deepcopy(r);bad[field]=0.
            with self.assertRaises(AssertionError):check(bad,100.,18.,'pin')

    def test_original_polymer_reuse_complete_keys_and_formula(self):
        polymers=[str(i) for i in range(10)];solvents=['water','hexane']+[str(i) for i in range(30)]
        rows=[]
        for p in polymers:
            for convention in ['normalized','existing']:
                for solvent in solvents:
                    rows.append(dict(unit='u',status='predicted',temperature_K=298.15,polymer=p,convention=convention,
                        solvent=solvent,ln_gamma_polymer=2.,ln_gamma_solvent=1.,polymer_volume_cm3_mol=200.,
                        solvent_volume_cm3_mol=100.,logP_x=1/math.log(10),logP_concentration=1/math.log(10)+math.log10(2)))
        result=extract(rows,'u',polymers);self.assertEqual(len(result['coefficients']),20)
        bad=copy.deepcopy(rows);bad[-1]=bad[0]
        with self.assertRaises(AssertionError):extract(bad,'u',polymers)
        bad=copy.deepcopy(rows);bad[0]['logP_concentration']-=2*math.log10(2)
        with self.assertRaises(AssertionError):extract(bad,'u',polymers)

    def test_density_rechecks_actual_xml_identity_units_and_temperature(self):
        source=Path('/mnt/r/plastchem-euler/phase10-v1/qualified-physical-volumes-v2.json')
        r=json.loads(source.read_text())['entries'][0]
        self.assertIsNotNone(recheck(r))
        for field,value in [('input_inchikey','WRONG-UHFFFAOYSA-N'),('density_kg_m3',r['density_kg_m3']/1000)]:
            bad=copy.deepcopy(r);bad[field]=value
            with self.assertRaises(AssertionError):recheck(bad)
        bad=copy.deepcopy(r);bad['conditions']['Temperature, K']=303.15
        with self.assertRaises(AssertionError):recheck(bad)

    def test_extension_full_keys_source_reuse_and_volume_export(self):
        solvents={str(i):dict(cavity_volume_cm3_mol=100.) for i in range(39)}
        coefficients=[dict(polymer=str(p),convention=c,ln_gamma_polymer=2.,polymer_volume_cm3_mol=200.)
                      for p in range(10) for c in ['normalized','existing']]
        reference=dict(coefficients=coefficients,primary_sha256='source',primary_path='p/partition/u.json',control_activities={})
        unit=dict(id='u',inchikey='ik',surface_sha256='solute',primary_plan_sha256='primary-plan')
        activities={s:dict(values=[1.],execution=dict(cpu_model='AMD EPYC 7763',job_id='1',node='test'),
            solvent=s,phase_sha256='surface-'+s,solute_mole_fraction=0.,temperature_K=298.15,reference_state='pure_component') for s in solvents}
        ctx=dict(solvents=solvents,phase_pins={s:'surface-'+s for s in solvents},
            volumes={s:dict(molar_volume_cm3_mol=150.,qualification='documented') for s in solvents},
            manifest=dict(primary_manifest_sha256='manifest'))
        rows=[]
        for c in coefficients:
            for s in solvents:
                normalized=c['convention']=='normalized';x=1/math.log(10)
                rows.append(dict(c,unit='u',inchikey='ik',status='activity_predicted',temperature_K=298.15,
                    solvent=s,solute_surface_sha256='solute',solvent_surface_sha256='surface-'+s,
                    primary_partition_sha256='source',ln_gamma_solvent=1.,logP_x=x,
                    solvent_volume_cm3_mol=None if normalized else 100.,
                    logP_concentration=None if normalized else x+math.log10(2),
                    concentration_status='missing_documented_molar_volume' if normalized else 'predicted'))
        check_partition(rows,unit,reference,activities,0,ctx)
        for field,value in [('primary_partition_sha256','wrong'),('ln_gamma_polymer',2.1),('logP_concentration',0.)]:
            bad=copy.deepcopy(rows);bad[0][field]=value
            with self.assertRaises(AssertionError):check_partition(bad,unit,reference,activities,0,ctx)
        bad=copy.deepcopy(rows);bad[-1]=bad[0]
        with self.assertRaises(AssertionError):check_partition(bad,unit,reference,activities,0,ctx)
        exported=partition_row(rows[0],ctx,activities,'plan')
        self.assertAlmostEqual(exported['logP_concentration'],rows[0]['logP_x']+math.log10(200/150),places=14)
        self.assertIsNone(rows[0]['logP_concentration'])  # Raw checkpoint unchanged.
        # Reproduce actual tar member ordering: partition before polymer-reuse.
        chunk='production-results-v1/0000';ctx.update(reference={'u':reference},units={'u':unit},
            primary_manifest_sha256='primary',chunks={chunk:dict(units=[unit],positions={'u':0},signature={})})
        reuse=dict(coefficients=coefficients,control_activities={},source_sha256='source',
            source='/remote/phase9-v1/p/partition/u.json',original_plan_sha256='primary-plan',
            source_seal=dict(sha256='source',signature=dict(plan_sha256='primary-plan',unit='u')))
        records=[(chunk+'/activities/'+s+'.json',a) for s,a in activities.items()]
        records += [(chunk+'/partition/u.json',rows),(chunk+'/polymer-reuse/u.json',reuse)]
        registry=dict(files={p+suffix:{} for p,_ in records for suffix in ['', '.sha256.json']})
        with tempfile.TemporaryDirectory() as tmp,patch.object(extension_audit,'D',Path(tmp)),patch.object(extension_audit,'context',return_value=ctx),patch.object(extension_audit,'records',return_value=iter(records)):
            checked=extension_audit.main(require_complete=False,registry=registry)
            self.assertEqual(checked['partition_molecules'],1)

    def test_independent_sql_rejects_wrong_volume_sign_or_original(self):
        con=duckdb.connect()
        self.addCleanup(con.close)
        con.execute('CREATE TABLE original(ik VARCHAR,polymer VARCHAR,convention VARCHAR,gp DOUBLE,vp DOUBLE,source_sha VARCHAR)')
        con.execute("INSERT INTO original VALUES ('ik','pe','normalized',2,200,'source')")
        con.execute('CREATE TABLE volumes(solvent VARCHAR,physical DOUBLE,cavity DOUBLE)')
        con.execute("INSERT INTO volumes VALUES ('s',150,100)")
        con.execute('''CREATE TABLE partition_rows(input_inchikey VARCHAR,campaign_polymer VARCHAR,convention VARCHAR,
            product_solvent_key VARCHAR,primary_partition_sha256 VARCHAR,ln_gamma_polymer DOUBLE,ln_gamma_solvent DOUBLE,
            polymer_volume_cm3_mol DOUBLE,solvent_volume_cm3_mol DOUBLE,logP_x DOUBLE,logP_concentration DOUBLE)''')
        x=1/math.log(10);lc=x+math.log10(200/150)
        con.execute("INSERT INTO partition_rows VALUES ('ik','pe','normalized','s','source',2,1,200,150,?,?)",[x,lc])
        verify_partition_arithmetic(con)
        for change in ['logP_concentration=logP_x-log10(200.0/150)','solvent_volume_cm3_mol=100','ln_gamma_polymer=2.1','logP_x=NULL']:
            con.execute('BEGIN TRANSACTION');con.execute('UPDATE partition_rows SET '+change)
            with self.assertRaises(AssertionError):verify_partition_arithmetic(con)
            con.execute('ROLLBACK')

    def test_tail_provenance_rejects_wrong_owner_or_job(self):
        assignment=dict(groups=[['u'],['v'],['w']],retained_unit='retained',
            code_pins={'phase10_tail_helper.py':'helper'},signature={'plan_sha256':'plan'})
        ctx=dict(tails={'assignment':dict(assignment=assignment,receipt={'job_id':'123'})})
        value=dict(unit='u',signature={'plan_sha256':'plan'},execution=dict(array_job_id='123',array_task_id='0'),
            tail_helper=dict(assignment_sha256='assignment',group=0,helper_sha256='helper'))
        check_tail_evidence(value,ctx)
        for field,change in [('unit','retained'),('execution',dict(array_job_id='999',array_task_id='0')),
                             ('tail_helper',dict(assignment_sha256='assignment',group=1,helper_sha256='helper'))]:
            bad=copy.deepcopy(value);bad[field]=change
            with self.assertRaises(AssertionError):check_tail_evidence(bad,ctx)

    def test_export_column_types_survive_parquet_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);row={k:'' for k in PART}
            row.update(input_inchikey='ik',product_solvent_key='s',campaign_polymer='pe',temperature_K=298.15,
                convention='normalized',logP_x=1.2,logP_concentration=1.3,ln_gamma_polymer=2.,ln_gamma_solvent=1.,
                polymer_volume_cm3_mol=200.,solvent_volume_cm3_mol=150.,solute_mole_fraction=0.,status='predicted')
            with gzip.open(p/'input.csv.gz','wt',newline='') as f:
                w=csv.DictWriter(f,fieldnames=PART);w.writeheader();w.writerow(row)
            con=duckdb.connect()
            try:
                self.assertEqual(parquet(con,p/'input.csv.gz',PART,p/'out.parquet'),1)
                got=con.execute('SELECT ln_gamma_polymer,ln_gamma_solvent,polymer_volume_cm3_mol,solvent_volume_cm3_mol,solute_mole_fraction,logP_x,logP_concentration FROM read_parquet(?)',[str(p/'out.parquet')]).fetchone()
                self.assertEqual(got,(2.,1.,200.,150.,0.,1.2,1.3))
                self.assertTrue(all(type(v) is float for v in got))
            finally:con.close()


if __name__=='__main__':unittest.main()
