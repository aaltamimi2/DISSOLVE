"""Stream A-9 checkpoints into the A-8.4 delivery schema, never a product write.

--preview writes a clearly partial schema rehearsal under phase9-v1. Default
requires all 5,830 units and complete numerical/cost/CPU authorization evidence.
The sealed promotion-v1 directory is never overwritten.
"""
import collections, csv, datetime, fcntl, gzip, hashlib, json, math, shutil, sys
from pathlib import Path
import duckdb
from audit_phase9_results import main as audit, records, sha
from build_phase84_release import PART, LLE, NUM, parquet

R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler')
D=B/'phase9-v1';BASE=B/'phase8-v1';OLD=B/'phase83-v1'
PART=PART+['solute_mole_fraction','reference_state','cpu_models_json','activity_job_ids_json','chunk_plan_sha256']
LLE=LLE+['cpu_model','job_id','chunk_plan_sha256','failure_evidence_json']
NUM.add('solute_mole_fraction')


def save(p,value):p.write_text(json.dumps(value,indent=2)+'\n')


def main(preview=False):
    lock=(R/'state/phase9-v1/release.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    registry=json.loads((D/'collection.json').read_text())
    check=audit(require_complete=not preview,registry=registry)
    assert check['collection_snapshot_sha256']==hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest(), 'Audited/exported registry mismatch'
    if not preview:
        assert (R/'reports/phase9-workbook-validation/summary.json').exists(),'Exact-zero workbook supplement required'
        clearance=json.loads((D/'production-clearance.json').read_text());assert clearance['status']=='passed'
        for name,digest in clearance['file_pins'].items():assert sha(D/name)==digest,name
        assert json.loads((D/'chunk-cost-final.json').read_text())['cost_gate_passes']
        assert json.loads((D/'gate-comparison-final.json').read_text())['lle_compared']==2240
    cohort=json.loads((OLD/'cohort.json').read_text());bykey={r['inchikey']:r for r in cohort['rows']}
    byunit={f"cohort-{r['index']:05d}":r for r in cohort['rows']};assert len(bykey)==len(byunit)==5830
    manifest=json.loads((BASE/'manifest.json').read_text());validation=json.loads((BASE/'validation-inputs.json').read_text())
    manifest_sha=sha(BASE/'manifest.json');assert manifest_sha==cohort['phase81_manifest_sha256']
    solvent_sha={s['name']:sha(BASE/s['B']) for s in manifest['solvents']}
    target=B/'promotion-v1'
    if not preview:assert not target.exists(),'Existing release must never be overwritten'
    out=D/'release-preview-v1' if preview else B/('promotion-v1.preparing-a9-'+sha(OLD/'cohort.json')[:12])
    out.mkdir(exist_ok=True)
    work=D/'release-working-v2';work.mkdir(exist_ok=True)
    partition=work/'partition.csv.gz';binary=work/'lle.csv.gz'
    counts=collections.Counter();quality={};chunks={};cpu_models=collections.Counter()
    plans={root:json.loads((D/name).read_text()) for root,name in [('chunk-probe-results-v1','chunk-probe-plan.json'),('production-results-v1','production-plan.json')] if (D/name).exists()}
    for root,plan in plans.items():
        plan_name='chunk-probe-plan.json' if root=='chunk-probe-results-v1' else 'production-plan.json'
        for index,units in enumerate(plan['chunks']):
            chunks[f'{root}/{index:04d}']=dict(cpu_models=set(),activity_job_ids=set(),units=[u['id'] for u in units],plan_sha256=sha(D/plan_name))
    def unit_quality(key):return quality.setdefault(key,dict(partition_predicted_rows=0,partition_failed_rows=0,lle_qualified_rows=0,lle_unresolved_or_failed_rows=0))
    with gzip.open(partition,'wt',newline='') as pf,gzip.open(binary,'wt',newline='') as lf:
        pw=csv.DictWriter(pf,fieldnames=PART);pw.writeheader();lw=csv.DictWriter(lf,fieldnames=LLE);lw.writeheader()
        for path,data in records(registry):
            chunk='/'.join(path.split('/')[:2]);ctx=chunks[chunk]
            if '/activities/' in path:
                ctx['cpu_models'].add(data['execution']['cpu_model']);ctx['activity_job_ids'].add(data['execution']['job_id'])
            elif '/partition/' in path:
                assert ctx['cpu_models']
                for r in data:
                    c=byunit[r['unit']];assert r['inchikey']==c['inchikey']
                    assert r['solute_surface_sha256']==c['surface_sha256'] and r['solvent_surface_sha256']==solvent_sha[r['solvent']]
                    assert all(math.isfinite(r[k]) for k in ['logP_x','logP_concentration'])
                    row=dict(input_inchikey=r['inchikey'],product_solvent_key=r['solvent'],campaign_polymer=r['polymer'],temperature_K=r['temperature_K'],
                             convention=r['convention'],logP_x=r['logP_x'],logP_concentration=r['logP_concentration'],status=r['status'],failure_mode='',
                             solute_surface_sha256=r['solute_surface_sha256'],solvent_surface_sha256=r['solvent_surface_sha256'],
                             polymer_ensemble_manifest_sha256=manifest_sha,parameterization='openCOSMO-RS 24a',
                             sign_convention='log10 P(solvent/polymer); positive favors solvent',units='dimensionless log10 ratio; x=mole-fraction, concentration=mol/L',
                             solute_mole_fraction=0.,reference_state='pure_component',cpu_models_json=json.dumps(sorted(ctx['cpu_models'])),
                             activity_job_ids_json=json.dumps(sorted(ctx['activity_job_ids'])),chunk_plan_sha256=ctx['plan_sha256'])
                    pw.writerow(row);counts['partition_'+r['status']]+=1;unit_quality(r['inchikey'])['partition_predicted_rows']+=1
            elif '/lle/' in path:
                r=data;c=byunit[r['unit']];assert r['inchikey']==c['inchikey']
                valid=r['status'] in ['single_liquid_phase','two_liquid_phases'];ties=r.get('tie_lines',[]);sig=r['signature']
                row={k:r.get(k,'') for k in LLE}
                row.update(input_inchikey=r['inchikey'],product_solvent_key=r['solvent'],temperature_regime=r['regime'],value_validated=valid,
                           tie_lines_json=json.dumps(ties,separators=(',',':')),grid_checks_json=json.dumps(r['grid_checks'],separators=(',',':')),
                           failure_mode='' if valid else r.get('failure_mode',r['status']),solute_surface_sha256=c['surface_sha256'],solvent_surface_sha256=sig['solvent_sha256'],
                           parameterization='openCOSMO-RS 24a',solver_sha256=sig['solver_sha256'],
                           units='mole fractions: mol/mol; weight percentages: g per 100 g solution; grid differences: percentage points; chemical potentials and tangent distances: RT',
                           sign_convention='Compositions are nonnegative; solubility is contaminant content in the solvent-rich phase. No signed transfer coefficient.',
                           cpu_model=r['execution']['cpu_model'],job_id=r['execution']['job_id'],chunk_plan_sha256=sig['plan_sha256'],
                           failure_evidence_json=json.dumps({k:r[k] for k in ['exception_type','exception_message','traceback','failure_policy_sha256','recovery_policy'] if k in r},separators=(',',':')) if not valid else '')
                if ties:
                    t=ties[0];xa=t['x_solvent_rich'];xb=t['x_solute_rich'];mw=c['molecular_weight_g_mol'];ms=validation['solvent_identities'][r['solvent']]['molecular_weight_g_mol']
                    row.update(x_contaminant_solvent_rich=xa,x_solvent_solvent_rich=1-xa,x_contaminant_solute_rich=xb,x_solvent_solute_rich=1-xb,
                               wt_percent_contaminant_solvent_rich=100*xa*mw/(xa*mw+(1-xa)*ms),wt_percent_contaminant_solute_rich=100*xb*mw/(xb*mw+(1-xb)*ms),
                               max_chemical_potential_residual_RT=max(t['chemical_potential_residual'] for t in ties),minimum_tangent_distance_RT=min(t['minimum_tangent_distance_RT'] for t in ties))
                lw.writerow(row);counts['lle_'+r['status']]+=1;unit_quality(r['inchikey'])['lle_qualified_rows' if valid else 'lle_unresolved_or_failed_rows']+=1
                cpu_models[r['execution']['cpu_model']]+=1
            elif path.endswith('/complete.json'):ctx['completion']=data
    con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='256MB'");con.execute('SET temp_directory=?',[str(work/'duckdb-temp')])
    n_partition=parquet(con,partition,PART,out/'partition.parquet')
    assert n_partition==counts['partition_predicted']
    assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,campaign_polymer,temperature_K,convention,count(*) n FROM staging GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
    n_lle=parquet(con,binary,LLE,out/'binary-lle.parquet')
    assert n_lle==sum(n for k,n in counts.items() if k.startswith('lle_'))
    assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,temperature_K,count(*) n FROM staging GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
    con.close()
    if not preview:assert (n_partition,n_lle)==(3731200,373120)
    for q in quality.values():
        q['all_requested_quantities_evaluated']=q['partition_predicted_rows']+q['partition_failed_rows']==640 and q['lle_qualified_rows']+q['lle_unresolved_or_failed_rows']==64
        q['all_requested_quantities_qualified']=q['partition_predicted_rows']==640 and q['lle_qualified_rows']==64
    pins=json.loads((OLD/'release-metadata-pins.json').read_text());assert pins['cohort_sha256']==sha(OLD/'cohort.json')
    for name,pin in pins['files'].items():
        source=OLD/'release-metadata'/name;assert sha(source)==pin['sha256'] and source.stat().st_size==pin['bytes']
        shutil.copyfile(source,out/name)
    with gzip.open(OLD/'release-metadata/contaminants.csv.gz','rt',newline='') as src,gzip.open(out/'contaminants.csv.gz','wt',newline='') as dst:
        reader=csv.DictReader(src);columns=list(next(iter(quality.values()))) if quality else []
        writer=csv.DictWriter(dst,fieldnames=reader.fieldnames+columns);writer.writeheader();identity_count=0
        for row in reader:
            row.update(quality.get(row['input_inchikey'],{}));writer.writerow(row);identity_count+=1
        assert identity_count==6103
    for name,folder in [('phase81','phase8-1-route-comparison'),('phase82','phase8-2-workbook-validation'),('phase9-exact-zero','phase9-workbook-validation')]:
        for source in (R/'reports'/folder).rglob('*'):
            if source.is_file():
                dest=out/'validation'/name/source.relative_to(R/'reports'/folder);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    provenance=out/'provenance';provenance.mkdir(exist_ok=True)
    for name in ['manifest.json','validation-inputs.json','package-pins.json']:shutil.copyfile(BASE/name,provenance/('phase8-'+name))
    for name in ['cohort.json','release-metadata-pins.json']:shutil.copyfile(OLD/name,provenance/name)
    for name in ['production-clearance.json','gate-comparison-final.json','chunk-cost-final.json','genoa-comparison.json','finite-dilution-clarification.json','documented-finite-dilution-differences.csv','results-audit.json','entry-resume-test.json','active-throttle-controller.json','phase9_throttle_remote_v2.py']:
        if (D/name).exists():shutil.copyfile(D/name,provenance/name)
    for source in [D/'phase9_throttle_remote_v3.py', D/'phase9_throttle_remote_v4.py', D/'recovery-code-pins.json', D/'tail-code-pins.json', D/'recovery-gate-fresh-passed.json', *D.glob('production-retry-*-submission.json'), *D.glob('tail-0002-v1/*.json')]:
        if source.exists():shutil.copyfile(source,provenance/source.name)
    for ctx in chunks.values():ctx['cpu_models']=sorted(ctx['cpu_models']);ctx['activity_job_ids']=sorted(ctx['activity_job_ids'])
    with gzip.open(provenance/'chunk-execution.json.gz','wt') as f:json.dump(chunks,f)
    save(provenance/'raw-archive-pins.json',dict(archives=registry['archives'],bulk_directory=str(D/'returns'),collection_snapshot_sha256=check['collection_snapshot_sha256']))
    code=provenance/'code';code.mkdir(exist_ok=True)
    for name in ['phase9_worker.py','phase9_worker_cpu.py','phase9_profiles.py','phase9_grid.py','phase9_entry.py','phase9_retry_entry.py','phase9_failure_policy.py','phase9_tail_common.py','phase9_tail_helper.py','phase9_tail_supervisor.py','audit_phase9_results.py','build_phase9_release.py','build_phase84_release.py','verify_phase9_delivery.py','collect_phase9.py','analyze_phase9_gate.py','analyze_phase9_genoa.py','analyze_phase9_cost.py','refresh_phase9_workbook_validation.py']:
        shutil.copyfile(R/'scripts'/name,code/name)
    shutil.copyfile(BASE/'phase8_lle.py',code/'phase8_lle.py')
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='partial_preview_not_a_release' if preview else 'complete',
                 frozen_contaminants=5830,accepted_main=5803,accepted_tier2=27,identity_status_rows=identity_count,polymers=10,solvents=32,
                 partition_rows=n_partition,partition_denominator=3731200,LLE_rows=n_lle,LLE_denominator=373120,
                 counts=dict(counts),fully_evaluated_contaminants=sum(q['all_requested_quantities_evaluated'] for q in quality.values()),
                 fully_qualified_contaminants=sum(q['all_requested_quantities_qualified'] for q in quality.values()),LLE_cpu_models=dict(cpu_models),
                 cohort_sha256=sha(OLD/'cohort.json'))
    save(out/'summary.json',summary)
    (out/'README.md').write_text(f'''# {'PARTIAL PREVIEW — NOT A RELEASE' if preview else 'Contaminant thermodynamic promotion release v1'}

Snapshot: {summary['utc']}. This is a delivery artifact; no product code or database was changed. {'Only returned rows are present. This preview must not be promoted or read as complete coverage.' if preview else 'Every frozen contaminant has all requested partition rows and LLE status rows.'}

The frozen cohort is 5,803 accepted main-tier CHNO structures plus 27 accepted tier-2 structures, 5,830 total. The identity table also records failed, not-yet-run and nine isotope-excluded inputs, for 6,103 rows at that snapshot. Later returns are not silently added. Names, SMILES, available CAS, tier, input/perceived InChIKeys, agreeing perception engines and original campaign status are retained. Identity acceptance is connectivity-first per D-IDENT, not full stereo-key equality.

`partition.parquet` contains neutral-species log10 P(solvent/polymer), positive toward solvent, at 298.15 K. No pH-dependent ionization model is applied. It retains both normalized and existing ensemble conventions, on both mole-fraction and concentration bases: logP_x=(ln gamma_polymer-ln gamma_solvent)/ln(10); logP_concentration=logP_x+log10(V_polymer/V_solvent). BOTH conventions now evaluate activity coefficients at exact solute x=0, with pure-component reference states. The existing convention still retains its historical ensemble normalization and cavity-volume choices. The owner has not selected a convention for product promotion. The 59 historical existing/water comparisons at x=1e-5 are documented reference differences, not reproduction passes or empirical corrections.

`binary-lle.parquet` contains liquid-liquid equilibrium at 298.15 K and each solvent's literal workbook high temperature, using the unmodified validated solver. It records raw mole fractions, weight percentages, both coexisting-phase compositions, all tie lines and both grid checks. Single-liquid-phase results have solubility 1 (100 wt%) and blank coexistence endpoints. Unresolved values retain their status and `value_validated=false`; any numerical estimates in those rows must not be treated as qualified predictions. Both 15 mol% and 15 wt% verdicts are retained, including the indeterminate band. No solid fusion correction is applied: this is LLE, not new solid-liquid solubility. Failures are data; nothing is interpolated or recalibrated.

Included polymers: EVOH, nylon6, nylon66, PC, PE, PET, PP, PS, PVC and PVDF, 236 conformers. Nitrocellulose, polyethersulfone, polyurethane and PETG were outside the fully converged 8.1 snapshot; absence is not a partitioning result. `polymer-product-map.csv` explicitly maps PE to both LDPE and HDPE, without inventing separate crystallinity/fusion properties. Polymer S(T) remains legacy for leaching/STRAP; PVDF lacks the legacy grid, so partition coverage alone does not establish full dissolution-screen coverage.

Validation packages under `validation/` preserve the finalized ten-polymer route comparison and every overlapping workbook phthalate comparison. They are computational-reference comparisons, not experimental-accuracy claims. The routes differ in engine, parameterization and reoptimized geometry simultaneously; no parameterization-only attribution is supported. The A-9 gate reproduces all 2,240 LLE verdicts and separates 17,733 numerical partition passes from the 59 owner-documented finite-dilution differences. Independent arithmetic checks reconstruct both ensemble conventions from stored phase activities and verify units, volume/sign arithmetic, unique keys, source/CPU linkage, and LLE qualification evidence. That audit does not independently re-solve COSMOspace. Raw grids are retained in hash-pinned bulk archives listed in provenance.

The `validation/phase9-exact-zero/` supplement recomputes workbook metrics and sign/threshold confusion matrices from A-9's actual x=0 outputs, retaining 8.2's historical files separately. It enumerates any screening sign changes and carries both workbook layouts and threshold bases without choosing one.

The quantity tables use openCOSMO-RS 24a and link solute/solvent surface SHA-256s, the full ensemble manifest and execution plan. Source ORCA surfaces remain Milan-made. Activity calculations may use Genoa only after the owner-required cross-CPU gate passes; CPU provenance is explicit. PFAS and the separate common-solvent DFT side task are outside this frozen 32-solvent grid. Generic xylene identity, the didecyl phthalate experimental source, threshold basis and workbook-layout interpretation remain owner questions.

Reproduction: `/home/aaltamimi2/plastchem-euler/scripts/build_phase9_release.py` {'--preview' if preview else ''}, after `/home/aaltamimi2/plastchem-euler/scripts/audit_phase9_results.py` {'(partial audit)' if preview else '--require-complete'}. Scientific worker/solver and audit/exporter sources are copied into provenance. Execution/collection evidence is under `/mnt/r/plastchem-euler/phase9-v1/`. The orchestrator, not this lane, performs product promotion.
''')
    files={str(p.relative_to(out)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(out.rglob('*')) if p.is_file() and p!=out/'manifest.json'}
    size=sum(v['bytes'] for v in files.values());assert size<200_000_000,('Payload exceeds approximately 200 MB target',size)
    save(out/'manifest.json',dict(utc=summary['utc'],status=summary['status'],files=files,payload_bytes=size,cohort_sha256=summary['cohort_sha256']))
    if not preview:
        seal=dict(release_id=sha(out/'manifest.json'),manifest_sha256=sha(out/'manifest.json'),file_count=len(files)+1,total_bytes=size+(out/'manifest.json').stat().st_size,path=str(target))
        out.rename(target);save(D/'release-seal.json',seal)
        report=R/'reports/phase84-release';report.mkdir(exist_ok=True);save(report/'release-seal.json',seal)
        (report/'REPORT.md').write_text(f"# Phase 8.4 release\n\nReleased `{target}` with ID `{seal['release_id']}`: {seal['file_count']} files, {seal['total_bytes']/1e6:.2f} MB. All 5,830 frozen contaminants have 640 partition rows and 64 LLE status rows. Qualified and unresolved counts are in `summary.json`; no unresolved value is silently promoted. Both x=0 conventions, the full validation packages and polymer/product mapping are included. Product data/code were not modified. Stop at the release gate.\n")
        print(json.dumps(seal,indent=2))
    else:print(json.dumps(dict(preview=str(out),bytes=size,summary=summary),indent=2))


if __name__=='__main__':main('--preview' in sys.argv)
