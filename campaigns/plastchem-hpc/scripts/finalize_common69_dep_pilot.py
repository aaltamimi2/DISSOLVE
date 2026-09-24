"""Independent arithmetic checks and feasibility report, no scientific recomputation."""
import csv,json,hashlib,math,statistics,datetime,shutil
from pathlib import Path
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/common69-pilot-20260917')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
s=json.loads((D/'pilot-summary.json').read_text());a=json.loads((D/'availability.json').read_text());w=json.loads((D/'water-activity.json').read_text());pure=list(csv.DictReader((D/'pure-solvents.csv').open()));grid=list(csv.DictReader((D/'composition-grid.csv').open()));assert len(pure)==67 and len(grid)==737
checks=[]
for path in sorted((D/'mixtures').glob('*.json')):
 g=json.loads(path.read_text())
 for sample in g['samples']:
  assert abs(sum(sample['composition'])-1)<1e-12 and min(sample['composition'])>=0
  assert all(math.isfinite(v) for v in sample['ln_gamma'])
 if g['status']=='converged':
  v=g['samples'][-1]['ln_gamma'][0];prior=g['samples'][-2]['ln_gamma'][0];shift=abs(v-prior)/math.log(10)
  assert shift<=.005 and abs(shift-g['dilution_shift_log10'])<1e-12
  assert abs((w['ln_gamma']-v)/math.log(10)-g['log_activity_ratio_vs_pure_water'])<1e-12
 checks.append({'file':str(path.relative_to(D)),'status':g['status'],'sha256':sha(path)})
old=json.loads(Path('/mnt/r/plastchem-euler/thermodynamics-v1/FLKPEMZONWLCSK-UHFFFAOYSA-N.json').read_text());deltas=[]
for r in a['rows']:
 p=D/'pure'/(r['common_key']+'.json')
 if not p.exists():continue
 x=json.loads(p.read_text());previous=old['activities'].get(r.get('panel_key'))
 if previous and previous['status']==x['status']=='converged':
  assert previous['solvent_surface_sha256']==x['solvent_surface_sha256'];deltas.append({'solvent':r['common_key'],'delta_log10':abs(previous['ln_gamma']-x['ln_gamma'])/math.log(10)})
vol=sum(bool(r.get('logKconc_water_to_solvent')) for r in pure)
verify={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'grid_records_checked':len(checks),'checks':'Finite values, composition normalization, independent dilution and activity-ratio arithmetic; not empirical accuracy or phase stability','endpoint_max_delta_log10':s['endpoint_max_delta_log10'],'production_overlap':deltas,'concentration_predictions':vol,'mole_fraction_predictions':s['pure_converged']}
(D/'verification.json').write_text(json.dumps(verify,indent=2)+'\n');(D/'grid-verification.json').write_text(json.dumps(checks,indent=2)+'\n')
# Pin independently read product identity evidence and implementation source.
provenance={str(p):sha(p) for p in [R/'scripts/audit_common69_availability.py',R/'scripts/run_common69_dep_pilot.py',Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/data/thermodynamics.duckdb'),Path('/home/aaltamimi2/.venvs/cosmo-logp/lib/python3.11/site-packages/opencosmorspy/cosmors.py'),Path('/home/aaltamimi2/.venvs/cosmo-logp/lib/python3.11/site-packages/opencosmorspy/parameterization.py')]}
(D/'implementation-pins.json').write_text(json.dumps(provenance,indent=2)+'\n')
for name in ['audit_common69_availability.py','run_common69_dep_pilot.py','finalize_common69_dep_pilot.py']:shutil.copyfile(R/'scripts'/name,D/name)
report=f'''# Common-solvent feasibility pilot — 2026-09-17

## Scope and inventory

The product has 69 common solvent keys, excluding water. Existing homogeneous Milan/frozen-24a surfaces are available and hash/parser/deck verified for 67/69: 30 from the panel registry and 37 from accepted campaign returns. No new ORCA jobs were submitted. No full-contaminant 69-solvent precomputation was launched and no product database was changed.

The two missing compatible surfaces are chlorobenzene and tetrahydrothiophene-1,1-dioxide (sulfolane). Legacy COSMObase/Turbomole files exist for both but were used only for identity, never as 24a input. The installed 24a parameter source includes chlorine and sulfur parameters; this alone does not prove either missing solvent runs. Searches covered the verified campaign/support registries, COSMO-POLYMER-ML legacy solvent library, local cosmo-artifacts and the installed package. This is a scoped local inventory, not a claim that no compatible surface exists elsewhere.

GVL was resolved from the read-only product alias table to gamma-valerolactone, CAS 108-29-2, matching an accepted campaign structure. Water is an additional verified reference, not one of the 69.

## Identity qualifications

Product aliases and campaign CAS labels agree directly for 58 entries; three differ and eight have no campaign CAS comparison (including the two missing surfaces and support-reference records). All reused surfaces are tied to structure/connectivity rather than a name-only match. The three discrepancies are:

- 1-methoxy2-propanol: product CAS 107-98-2; campaign label 1320-67-8. Modeled structure COCC(C)O; retained perceived key ARXJGSRGQADJSQ-SCSAIBSYSA-N.
- dipentene: product dl-limonene CAS 138-86-3; campaign (+)-limonene CAS 5989-27-5, perceived key XMGQYMWWDOXHJM-JTQLQIEISA-N. This pilot uses a single enantiomer surface; it does not validate a racemic/commercial mixture identity.
- n-hexylacetate: product CAS 142-92-7; campaign label 88230-35-7. Modeled structure CCCCCCOC(C)=O, key AOGQPLXWSUTHQB-UHFFFAOYSA-N.

These flags remain explicit; no source CAS was overwritten. Computational operation is separate from exact commercial-solvent identity validation. See product-identity-crosscheck.json and availability.csv.

## One-contaminant tests

Diethyl phthalate (DEP), FLKPEMZONWLCSK-UHFFFAOYSA-N, at **298.15 K only**, openCOSMORS24a, pure-component reference state. Reused one frozen verified solute surface; no geometry or DFT rerun.

- Pure organic solvents: **{s['pure_converged']}/67 converged**, {s['pure_failed']} failed; 2/69 not run for missing compatible surfaces. Water baseline also passed.
- Water-referenced mole-fraction partition predictions: {s['pure_converged']}; concentration-based predictions with already documented molar volumes: **{vol}**. Others remain blank, not zero.
- Composition grid: **{s['mixture_converged']}/{s['mixture_points']} converged**, {s['mixture_failed']} failed. This is 67 solvents × 11 solvent/water fractions (0, 0.1, ..., 1). The pure-water endpoint repeats across solvents; there are 671 distinct composition systems, not 737 distinct liquids.
- For each grid point, DEP mole fractions 1e-5 then 1e-6 were tested, extending to 1e-7 and 1e-8 if required. Acceptance: last activity change ≤0.005 log10. Total composition is [x_DEP, (1-x_DEP)*f_solvent, (1-x_DEP)*(1-f_solvent)]. Raw samples are retained.
- Maximum difference between ternary-grid endpoints and separate binary calculations: **{s['endpoint_max_delta_log10']:.6g} log10**. Production overlap: {len(deltas)} solvents, maximum activity difference {max((x['delta_log10'] for x in deltas),default=0):.6g} log10.

## What this establishes

The available surfaces load into the installed engine, the solver and dilution checks pass as counted above, endpoints agree, and activity-ratio arithmetic independently checks. It is **not** new experimental validation. These are prescribed homogeneous liquid-branch calculations: phase stability, miscibility, mutual saturation and liquid-liquid tie lines were not solved. In particular water/hydrocarbon grid points must not be described as demonstrated stable single-phase mixtures. No mixture concentration partition coefficients are assigned without mixture-volume information. Near/below-melting pure solvents likewise represent liquid reference states, not a demonstrated stable liquid phase.

The grid varies the solvent/water blend at dilute DEP. It does not scan concentrated DEP formulations, all organic-organic solvent pairs, or temperature. No calibration or empirical offset was applied.

## Timing and caching implications

Pilot elapsed time including file I/O: **{s['wall_seconds']:.1f} s**; peak process RSS **{s['peak_rss_kib']/1024:.1f} MiB**. These are DEP measurements and cannot be assumed to hold for every contaminant. The production processor was temporarily SIGSTOP-paused, then SIGCONT-resumed by the pilot's finally block; receipts are under /home/aaltamimi2/plastchem-euler/state/common69-pilot-worker-*.json.

The 67 available solvent surfaces plus water are reusable assets. A cache should bind solute and solvent surface hashes, parameterization/implementation, temperature, complete composition vector and reference state; missing values and failed dilution checks need explicit status. Current data support deciding between pure-solvent caching and on-demand mixture calculations. No full-set cache policy is chosen here.

## Files and reproduction

Directory: {D}

- availability.csv / availability.json: all 69 keys, identities, source paths, hashes and missing surfaces.
- pure-solvents.csv: DEP pure-solvent results, volume availability and timings.
- composition-grid.csv: all 737 grid outcomes; mixtures/*.json: full raw dilution samples.
- pure/*.json and water-activity.json: raw pure-solvent activity calculations.
- verification.json / grid-verification.json: independent arithmetic and endpoint checks.
- implementation-pins.json and SHA256SUMS: source and output pins.

Scripts: /home/aaltamimi2/plastchem-euler/scripts/audit_common69_availability.py, /home/aaltamimi2/plastchem-euler/scripts/run_common69_dep_pilot.py, /home/aaltamimi2/plastchem-euler/scripts/finalize_common69_dep_pilot.py. Interpreter: /home/aaltamimi2/.venvs/cosmo-logp/bin/python.

The pilot script binds the production PID and checks its command before pausing; update that PID after verifying the live worker before a deliberate rerun. Do not blindly rerun against a different process or overwrite this sealed package. Availability requires the included gvl-product-identity.json extracted read-only from the product database using its recorded SQL query. No new ORCA is required to reproduce these existing-surface tests.
'''
(D/'REPORT.md').write_text(report)
manifest=''.join(f'{sha(p)}  {p.relative_to(D)}\n' for p in sorted(D.rglob('*')) if p.is_file() and p.name not in ['SHA256SUMS','pilot.log'])
(D/'SHA256SUMS').write_text(manifest)
receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(D),'manifest_sha256':sha(D/'SHA256SUMS'),'files':len(manifest.splitlines()),'summary':s,'verification':verify}
(R/'state/common69-pilot-sealed.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'summary':s,'concentration_predictions':vol,'manifest_sha256':receipt['manifest_sha256']}))
