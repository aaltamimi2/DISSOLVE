"""Export the separately keyed A-10 release after complete independent audit.

The original release is read-only. No product writes and no licensed surfaces.
"""
import collections
import csv
import datetime
import fcntl
import gzip
import hashlib
import json
import math
import shutil
from pathlib import Path

import duckdb

from audit_phase10_results import main as audit, D, R, VOLUME_PIN
from audit_phase9_results import sha
from build_phase9_release import PART as PRIMARY_PART, LLE, NUM, parquet

PART=PRIMARY_PART+['ln_gamma_polymer','ln_gamma_solvent','polymer_volume_cm3_mol',
    'solvent_volume_cm3_mol','volume_reference_sha256','volume_reference_qualification','primary_partition_sha256']
NUM.update(['ln_gamma_polymer','ln_gamma_solvent','polymer_volume_cm3_mol','solvent_volume_cm3_mol'])


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def partition_row(r,ctx,activities,plan_sha):
    physical=ctx['volumes'][r['solvent']]
    normalized=r['convention']=='normalized'
    vs=physical['molar_volume_cm3_mol'] if normalized else r['solvent_volume_cm3_mol']
    concentration=r['logP_x']+math.log10(r['polymer_volume_cm3_mol']/vs)
    assert math.isfinite(concentration)
    return dict(input_inchikey=r['inchikey'],product_solvent_key=r['solvent'],campaign_polymer=r['polymer'],
        temperature_K=298.15,convention=r['convention'],logP_x=r['logP_x'],logP_concentration=concentration,
        status='predicted',failure_mode='',solute_surface_sha256=r['solute_surface_sha256'],
        solvent_surface_sha256=r['solvent_surface_sha256'],polymer_ensemble_manifest_sha256=ctx['manifest']['primary_manifest_sha256'],
        parameterization='openCOSMO-RS 24a',sign_convention='log10 P(solvent/polymer); positive favors solvent',
        units='dimensionless log10 ratio; x=mole-fraction, concentration=mol/L',solute_mole_fraction=0.,reference_state='pure_component',
        cpu_models_json=json.dumps(sorted({v['execution']['cpu_model'] for v in activities.values()})),
        activity_job_ids_json=json.dumps(sorted({v['execution']['job_id'] for v in activities.values()})),
        chunk_plan_sha256=plan_sha,ln_gamma_polymer=r['ln_gamma_polymer'],ln_gamma_solvent=r['ln_gamma_solvent'],
        polymer_volume_cm3_mol=r['polymer_volume_cm3_mol'],solvent_volume_cm3_mol=vs,
        volume_reference_sha256=VOLUME_PIN if normalized else r['solvent_surface_sha256'],
        volume_reference_qualification=physical['qualification'] if normalized else 'COSMO_cavity_volume_existing_convention',
        primary_partition_sha256=r['primary_partition_sha256'])


def lle_row(r,ctx):
    u=ctx['units'][r['unit']];solvent=ctx['solvents'][r['solvent']]
    valid=r['value_validated'];ties=r['tie_lines'];sig=r['signature']
    row={k:r.get(k,'') for k in LLE}
    row.update(input_inchikey=r['inchikey'],product_solvent_key=r['solvent'],temperature_regime='RT',
        tie_lines_json=json.dumps(ties,separators=(',',':')),grid_checks_json=json.dumps(r['grid_checks'],separators=(',',':')),
        failure_mode='' if valid else r.get('failure_mode',r['status']),solute_surface_sha256=u['surface_sha256'],
        solvent_surface_sha256=solvent['surface_sha256'],parameterization='openCOSMO-RS 24a',
        solver_sha256=sha(D.parent/'phase8-v1/phase8_lle.py'),
        units='mole fractions: mol/mol; weight percentages: g per 100 g solution; grid differences: percentage points; chemical potentials and tangent distances: RT',
        sign_convention='Compositions are nonnegative; solubility is contaminant content in the solvent-rich phase. No signed transfer coefficient.',
        cpu_model=r['execution']['cpu_model'],job_id=r['execution']['job_id'],chunk_plan_sha256=sig['plan_sha256'],
        failure_evidence_json=json.dumps({k:r[k] for k in ['exception_type','exception_message','traceback','failure_policy_sha256'] if k in r},separators=(',',':')) if not valid else '')
    if ties:
        xa=ties[0]['x_solvent_rich'];xb=ties[0]['x_solute_rich'];mw=u['molecular_weight_g_mol'];ms=solvent['molecular_weight_g_mol']
        row.update(x_contaminant_solvent_rich=xa,x_solvent_solvent_rich=1-xa,x_contaminant_solute_rich=xb,x_solvent_solute_rich=1-xb,
            wt_percent_contaminant_solvent_rich=100*xa*mw/(xa*mw+(1-xa)*ms),
            wt_percent_contaminant_solute_rich=100*xb*mw/(xb*mw+(1-xb)*ms),
            max_chemical_potential_residual_RT=max(t['chemical_potential_residual'] for t in ties),
            minimum_tangent_distance_RT=min(t['minimum_tangent_distance_RT'] for t in ties))
    return row


def main():
    target=D.parent/'promotion-ext39-v1';assert not target.exists(),'Never overwrite a sealed release'
    lock=(D/'release.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    collector=(D/'collect.lock').open('a');fcntl.flock(collector,fcntl.LOCK_EX|fcntl.LOCK_NB)
    registry=json.loads((D/'collection.json').read_text())
    snapshot=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest()
    work=D/'release-working-v1';work.mkdir(exist_ok=True)
    out=D.parent/'promotion-ext39-v1.preparing';out.mkdir(exist_ok=True)
    partition=work/'partition.csv.gz';binary=work/'lle.csv.gz'
    quality=collections.defaultdict(lambda:collections.Counter());counts=collections.Counter();cpus=collections.Counter()
    with gzip.open(partition,'wt',newline='',compresslevel=3) as pf,gzip.open(binary,'wt',newline='',compresslevel=3) as lf:
        pw=csv.DictWriter(pf,fieldnames=PART);pw.writeheader();lw=csv.DictWriter(lf,fieldnames=LLE);lw.writeheader()
        def consume(path,value,ctx,activities):
            chunk='/'.join(path.split('/')[:2]);plan=ctx['chunks'][chunk]['signature']['plan_sha256']
            if '/partition/' in path:
                for r in value:
                    pw.writerow(partition_row(r,ctx,activities,plan))
                    quality[r['inchikey']]['partition_predicted_rows']+=1;counts['partition_predicted']+=1
            elif '/lle/' in path:
                lw.writerow(lle_row(value,ctx));counts['lle_'+value['status']]+=1
                quality[value['inchikey']]['lle_qualified_rows' if value['value_validated'] else 'lle_unresolved_rows']+=1
                cpus[value['execution']['cpu_model']]+=1
        result=audit(require_complete=True,registry=registry,callback=consume)
    assert result['collection_snapshot_sha256']==snapshot and len(quality)==5830
    con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='256MB'")
    con.execute('SET temp_directory=?',[str(work/'duckdb-temp')])
    np=parquet(con,partition,PART,out/'partition.parquet');assert np==4547400
    assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,campaign_polymer,temperature_K,convention,count(*) n FROM staging GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
    nl=parquet(con,binary,LLE,out/'binary-lle.parquet');assert nl==227370
    assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,temperature_K,count(*) n FROM staging GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
    con.close()
    primary=D.parent/'promotion-v1'
    # Byte identity is intentional: its qualification columns describe the
    # original panel. Extension-specific quality is a separate keyed table.
    for name in ['contaminants.csv.gz','polymer-product-map.csv']:shutil.copyfile(primary/name,out/name)
    with (out/'extension-quality.csv').open('w',newline='') as f:
        fields=['input_inchikey','partition_predicted_rows','lle_qualified_rows','lle_unresolved_rows','all_requested_quantities_evaluated','all_requested_quantities_qualified']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for key,q in sorted(quality.items()):
            assert q['partition_predicted_rows']==780 and q['lle_qualified_rows']+q['lle_unresolved_rows']==39
            writer.writerow(dict(input_inchikey=key,partition_predicted_rows=q['partition_predicted_rows'],
                lle_qualified_rows=q['lle_qualified_rows'],lle_unresolved_rows=q['lle_unresolved_rows'],
                all_requested_quantities_evaluated=True,all_requested_quantities_qualified=q['lle_qualified_rows']==39))
    provenance=out/'provenance';provenance.mkdir(exist_ok=True)
    for name in ['manifest.json','worker-pins-v2.json','calibration-clearance.json','results-audit.json',
        'qualified-physical-volumes-v4.json','primary-polymer-reference-receipt.json','primary-polymer-reference.json.gz',
        'production-submission.json','calibration-v2-observation.json']:
        shutil.copyfile(D/name,provenance/name)
    for name in ['calibration-plan-v2.json','production-plan.json']:
        with (D/name).open('rb') as src,gzip.open(provenance/(name+'.gz'),'wb',compresslevel=3) as dst:shutil.copyfileobj(src,dst)
    for name in ['manifest.json','summary.json']:shutil.copyfile(primary/name,provenance/('primary-'+name))
    shutil.copyfile(D.parent/'phase9-v1/delivery-verification.json',provenance/'primary-delivery-verification.json')
    for name in ['manifest.json','package-pins.json']:shutil.copyfile(D.parent/'phase8-v1'/name,provenance/('phase8-'+name))
    shutil.copyfile(D.parent/'phase83-v1/cohort.json',provenance/'cohort.json')
    shutil.copyfile(D.parent/'phase9-v1/genoa-comparison.json',provenance/'genoa-comparison.json')
    for source in sorted(D.glob('tail-*-v1/assignment.json')):
        dest=provenance/source.parent.name;dest.mkdir(exist_ok=True)
        shutil.copyfile(source,dest/source.name)
        for name in ['launch-intent.json','supervisor-start.json','handoff-state.json']:
            if (source.parent/name).exists():shutil.copyfile(source.parent/name,dest/name)
    for source in sorted(D.glob('production-retry-tail*-submission.json')):shutil.copyfile(source,provenance/source.name)
    save(provenance/'raw-archive-pins.json',dict(archives=registry['archives'],collection_snapshot_sha256=snapshot,
        bulk_directory=str(D/'returns'),compact_bundles=registry['compact_bundles']))
    code=provenance/'code';code.mkdir(exist_ok=True)
    for name in ['phase10_worker_v2.py','phase9_worker_cpu.py','phase9_profiles.py','phase9_grid.py','phase9_failure_policy.py',
        'audit_phase10_results.py','phase10_lle_audit.py','build_phase10_primary_reference.py','build_phase10_release.py',
        'verify_phase10_delivery.py','build_phase84_release.py','build_phase9_release.py',
        'audit_phase9_results.py','verify_phase9_delivery.py','audit_phase83_collected.py','audit_phase83_ensembles.py',
        'finalize_phase10_calibration.py','find_phase10_thermoml_volumes.py','qualify_phase10_initial_volumes.py',
        'qualify_phase10_thermoml_volumes.py','extend_phase10_volume_references.py','finish_phase10_volume_references.py']:
        shutil.copyfile(R/'scripts'/name,code/name)
    if list(D.glob('tail-*-v1/assignment.json')):
        for name in ['phase9_tail_common.py','phase10_tail_common.py','phase10_tail_helper.py','phase10_tail_supervisor.py',
                     'submit_phase10_tail_remote.py','launch_phase10_tail.py']:
            shutil.copyfile(R/'scripts'/name,code/name)
    shutil.copyfile(D.parent/'phase8-v1/phase8_lle.py',code/'phase8_lle.py')
    shutil.copyfile(R/'reports/phase10-2026-09-24/VOLUME_REFERENCE_REVIEW.md',out/'VOLUME_REFERENCE_REVIEW.md')
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='complete',frozen_contaminants=5830,
        solvents=39,polymers=10,partition_rows=np,partition_denominator=4547400,LLE_rows=nl,LLE_denominator=227370,
        counts=dict(counts),fully_evaluated_contaminants=5830,fully_qualified_contaminants=result['fully_qualified_molecules'],
        LLE_cpu_models=dict(cpus),cohort_sha256=sha(D.parent/'phase83-v1/cohort.json'),
        primary_release_manifest_sha256=sha(primary/'manifest.json'),volume_references_sha256=VOLUME_PIN)
    save(out/'summary.json',summary)
    (out/'README.md').write_text('''# Contaminant 39-solvent extension v1

This complete, separately keyed extension contains 5,830 frozen contaminants × 39 new solvents × 10 original converged polymer ensembles, at 298.15 K. It does not modify the original `promotion-v1` panel. The orchestrator performs product promotion; this lane has written no product code or database.

`partition.parquet` retains both normalized and existing conventions on both mole-fraction and concentration bases, at exact solute x=0. Positive log10 P(solvent/polymer) favors solvent. The original audited polymer activities are reused with per-row source hashes; new solvent activities come from the frozen 24a surfaces. logP_x=(ln gamma_polymer-ln gamma_solvent)/ln(10); logP_concentration=logP_x+log10(V_polymer/V_solvent). Normalized concentration uses individually documented physical liquid volumes; existing convention keeps cavity volumes and the historical ensemble normalization. No empirical correction, interpolation or pH-ionization model is applied. Which convention to serve remains an owner decision.

The 39 physical references include 35 original measured liquid densities compiled by NIST and four manufacturer literature properties. See VOLUME_REFERENCE_REVIEW.md and the complete provenance table for source, temperature, uncertainty and phase caveats. Ethylene carbonate is an explicitly measured subcooled liquid reference; sulfolane is a measured liquid reference near/below its melting range. Neither proves stable room-temperature liquid availability. The identity named dipentene is the computed pure limonene stereoisomer, not a commercial terpene mixture. Density qualification is not a validation of predicted partition accuracy.

`binary-lle.parquet` has one room-temperature liquid-liquid equilibrium status per contaminant/solvent, no high-temperature extension. Qualified single-phase or coexistence values retain both grids, all tie lines, mass conversion and both 15 mol%/15 wt% verdicts. Unresolved statuses are data, with value_validated=false and no promoted threshold verdict; nonconvergence has no numerical prediction. Estimated numbers in other unresolved rows must not be treated as qualified. There is no fusion/crystallization correction: this is LLE, not solid-liquid solubility.

`contaminants.csv.gz` and `polymer-product-map.csv` are byte-identical to the original release. The former's qualification columns concern the original panel only. `extension-quality.csv` supplies the separate 39-solvent qualification counts. Every accepted structure occurs once in that table; the original identity table also retains failed, not-run and isotope-excluded input records. The same ten frozen ensembles are EVOH, nylon6, nylon66, PC, PE, PET, PP, PS, PVC and PVDF (236 conformers); nitrocellulose, polyethersulfone, polyurethane and PETG are excluded from this frozen ensemble set. PE maps to both LDPE and HDPE without inventing crystallinity data.

The original route comparison differs simultaneously in engine, parameterization and reoptimized geometry; no parameterization-only attribution is justified. Existing route/workbook validations remain in the original release. The extension adds 80 exact original-batch water/hexane controls in the deliberate 40-molecule calibration, and checks every production control independently against the original archived activities. Its audit checks provenance, identities, unique full coverage, arithmetic and saved solver qualifications; it does not independently re-solve COSMOspace or establish experimental accuracy. Raw grids are retained in digest-pinned bulk archives. Generic xylene identity and didecyl phthalate's experimental source remain owner questions.

Reproduce with /home/aaltamimi2/plastchem-euler/scripts/build_phase10_primary_reference.py, then audit_phase10_results.py, build_phase10_release.py and verify_phase10_delivery.py in the same scripts directory. Execution and collected checkpoints are under /mnt/r/plastchem-euler/phase10-v1/. Scientific/audit/export code and pins are in provenance/. COSMObase and solvent surface files are not included in this delivery or git.
''')
    files={str(p.relative_to(out)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(out.rglob('*')) if p.is_file() and p!=out/'manifest.json'}
    size=sum(v['bytes'] for v in files.values());assert size<200_000_000,('Payload size',size)
    save(out/'manifest.json',dict(utc=summary['utc'],status='complete',files=files,payload_bytes=size,cohort_sha256=summary['cohort_sha256']))
    seal=dict(release_id=sha(out/'manifest.json'),manifest_sha256=sha(out/'manifest.json'),file_count=len(files)+1,
        total_bytes=size+(out/'manifest.json').stat().st_size,path=str(target))
    out.rename(target);save(D/'release-seal.json',seal)
    print(json.dumps(seal,indent=2))


if __name__=='__main__':main()
