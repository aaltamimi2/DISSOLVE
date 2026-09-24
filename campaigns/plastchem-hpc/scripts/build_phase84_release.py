"""Construct the promotion payload only after every frozen unit is collected.

Streaming conversion keeps memory bounded. This does not promote into any product.
"""
import collections,csv,datetime,gzip,hashlib,json,math,shutil
from pathlib import Path
import duckdb
from audit_phase83_collected import main as audit_collected
from audit_phase83_ensembles import main as audit_ensembles
R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler');D=B/'phase83-v1'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
PART=['input_inchikey','product_solvent_key','campaign_polymer','temperature_K','convention','logP_x','logP_concentration','status','failure_mode','solute_surface_sha256','solvent_surface_sha256','polymer_ensemble_manifest_sha256','parameterization','sign_convention','units']
LLE=['input_inchikey','product_solvent_key','temperature_K','temperature_regime','status','value_validated','solute_mole_fraction_solubility','solute_wt_percent_solubility','x_contaminant_solvent_rich','x_solvent_solvent_rich','x_contaminant_solute_rich','x_solvent_solute_rich','wt_percent_contaminant_solvent_rich','wt_percent_contaminant_solute_rich','tie_lines_json','grid_checks_json','above_15_mol_percent','above_15_wt_percent','grid_change_mol_percentage_points','grid_change_wt_percentage_points','max_chemical_potential_residual_RT','minimum_tangent_distance_RT','failure_mode','solute_surface_sha256','solvent_surface_sha256','parameterization','solver_sha256','units','sign_convention']
NUM=set(['temperature_K','logP_x','logP_concentration','solute_mole_fraction_solubility','solute_wt_percent_solubility','x_contaminant_solvent_rich','x_solvent_solvent_rich','x_contaminant_solute_rich','x_solvent_solute_rich','wt_percent_contaminant_solvent_rich','wt_percent_contaminant_solute_rich','grid_change_mol_percentage_points','grid_change_wt_percentage_points','max_chemical_potential_residual_RT','minimum_tangent_distance_RT'])
def parquet(con,csvpath,columns,dest):
 schema={k:'DOUBLE' if k in NUM else 'BOOLEAN' if k in ['value_validated','above_15_mol_percent','above_15_wt_percent'] else 'VARCHAR' for k in columns}
 con.execute('CREATE OR REPLACE TABLE staging AS SELECT * FROM read_csv(?,header=true,columns=?,nullstr=\'\')',[str(csvpath),schema])
 con.execute('COPY staging TO ? (FORMAT PARQUET, COMPRESSION ZSTD)',[str(dest)])
 return con.execute('SELECT count(*) FROM staging').fetchone()[0]
def main():
 hold=D/'cost-hold.json'
 assert not hold.exists() or not json.loads(hold.read_text()).get('active'),'Updated cost gate is held; no release before explicit clearance'
 c=json.loads((D/'cohort.json').read_text());cohort_sha=sha(D/'cohort.json');gate=json.loads((D/'cost-gate.json').read_text());assert gate['decision']=='continue'
 collection=json.loads((R/'state/phase83-v1/collection.json').read_text());expected={f'{r["index"]:05d}' for r in c['rows']}
 assert set(collection['collected'])==expected,('Collection incomplete',len(collection['collected']),len(expected))
 audit=audit_collected();assert audit['audited_contaminants']==len(expected)
 ensembles=audit_ensembles();assert ensembles['counts']['contaminants_checked']==len(expected)
 target=B/'promotion-v1';assert not target.exists(),'Existing release must never be overwritten'
 out=B/('promotion-v1.preparing-'+sha(D/'cohort.json')[:12]);out.mkdir(exist_ok=True)
 work=D/'release-working';work.mkdir(exist_ok=True);partition=work/'partition.csv.gz';binary=work/'lle.csv.gz'
 counts=collections.Counter();phase82=json.loads((D.parent/'phase8-v1/validation-inputs.json').read_text())
 model_manifest=json.loads((B/'phase8-v1/manifest.json').read_text());unit_quality={};cpu_models=collections.Counter()
 expected_partition={(p,s['name'],convention) for p in model_manifest['polymers'] for s in model_manifest['solvents'] for convention in ['normalized','existing']}
 expected_temperatures={(u['solvent'],u['regime']):u['temperature_K'] for u in phase82['lle_units']}
 expected_solvent_sha={s['name']:sha(B/'phase8-v1'/s['B']) for s in model_manifest['solvents']}
 driver_sha=json.loads((D/'staging-pins.json').read_text())['phase83_worker.py']
 reference_keys={v['input']['inchikey']:name for name,v in phase82['solutes'].items()};validation_rows=[];lle_repeat=[]
 sealed_lle=json.loads((B/'phase8-v1/phase82/lle-results.json').read_text())
 lle_reference={(r['solute'],r['solvent'],r['regime']):r for r in sealed_lle.values()}
 with gzip.open(partition,'wt',newline='') as pf,gzip.open(binary,'wt',newline='') as lf:
  pw=csv.DictWriter(pf,fieldnames=PART);pw.writeheader();lw=csv.DictWriter(lf,fieldnames=LLE);lw.writeheader()
  for c_row in c['rows']:
   idx=c_row['index'];key=f'{idx:05d}';p=D/'collected'/(key+'.json.gz');assert sha(p)==collection['collected'][key]['metadata_sha256']
   with gzip.open(p,'rt') as f:data=json.load(f)
   assert data['complete']['signature']['cohort_sha256']==cohort_sha
   assert data['complete']['signature']['driver_sha256']==driver_sha
   cpu_models[data['complete']['execution']['cpu_model']]+=1
   assert len(data['partition'])==640 and len(data['lle'])==64
   unique=set()
   for r in data['partition']:
    assert r['inchikey']==c_row['inchikey'] and r['solute_surface_sha256']==c_row['surface_sha256']
    assert r['solvent_surface_sha256']==expected_solvent_sha[r['solvent']] and float(r['temperature_K'])==298.15
    assert r['polymer_ensemble_manifest_sha256']==c['phase81_manifest_sha256']
    k=(r['polymer'],r['solvent'],r['convention']);assert k not in unique;unique.add(k)
    if r['status']=='predicted':assert all(math.isfinite(float(r[v])) for v in ['logP_x','logP_concentration'])
    row=dict(input_inchikey=r['inchikey'],product_solvent_key=r['solvent'],campaign_polymer=r['polymer'],temperature_K=r['temperature_K'],convention=r['convention'],
     logP_x=r.get('logP_x',''),logP_concentration=r.get('logP_concentration',''),status=r['status'],failure_mode=r.get('failure_mode',''),solute_surface_sha256=r['solute_surface_sha256'],solvent_surface_sha256=r['solvent_surface_sha256'],
     polymer_ensemble_manifest_sha256=r['polymer_ensemble_manifest_sha256'],parameterization='openCOSMO-RS 24a',sign_convention='log10 P(solvent/polymer); positive favors solvent',units='dimensionless log10 ratio; x=mole-fraction, concentration=mol/L')
    pw.writerow(row);counts['partition_'+r['status']]+=1
    if r['inchikey'] in reference_keys and r['polymer']=='pvc':validation_rows.append(dict(r,anchor=reference_keys[r['inchikey']]))
   assert unique==expected_partition
   unique=set()
   for n,r in data['lle'].items():
    assert r['solute']==c_row['inchikey'];k=(r['solvent'],r['regime']);assert k not in unique;unique.add(k)
    assert r['temperature_K']==expected_temperatures[k]
    valid=r['status'] in ['single_liquid_phase','two_liquid_phases'];ties=r.get('tie_lines',[]);sig=r.get('signature',{})
    if 'solute_sha256' in sig:
     assert sig['solute_sha256']==c_row['surface_sha256'] and sig['solvent_sha256']==expected_solvent_sha[r['solvent']] and sig['temperature_K']==r['temperature_K']
    if valid:assert r['solver_sha256']==data['complete']['signature']['solver_sha256']
    # Failures carry no fabricated solubility; unresolved numerical estimates stay explicitly unvalidated.
    row={k:r.get(k,'') for k in LLE};row.update(input_inchikey=r['solute'],product_solvent_key=r['solvent'],temperature_regime=r['regime'],value_validated=valid,
     tie_lines_json=json.dumps(ties,separators=(',',':')),grid_checks_json=json.dumps(r.get('grid_checks',[]),separators=(',',':')),
     failure_mode=r.get('error','') or ('' if valid else r['status']),solute_surface_sha256=c_row['surface_sha256'],
     solvent_surface_sha256=sig.get('solvent_sha256',''),parameterization='openCOSMO-RS 24a',solver_sha256=data['complete']['signature']['solver_sha256'],
     units='mole fractions: mol/mol; weight percentages: g per 100 g solution; grid differences: percentage points; chemical potentials and tangent distances: RT',
     sign_convention='Compositions are nonnegative; solubility is contaminant content in the solvent-rich phase. No signed transfer coefficient.')
    if not row['solvent_surface_sha256']:
     m=json.loads((D.parent/'phase8-v1/manifest.json').read_text());sv=next(s for s in m['solvents'] if s['name']==r['solvent']);row['solvent_surface_sha256']=sha(D.parent/'phase8-v1'/sv['B'])
    if ties:
     t=ties[0];xa=t['x_solvent_rich'];xb=t['x_solute_rich'];mw=c_row['molecular_weight_g_mol'];ms=phase82['solvent_identities'][r['solvent']]['molecular_weight_g_mol']
     row.update(x_contaminant_solvent_rich=xa,x_solvent_solvent_rich=1-xa,x_contaminant_solute_rich=xb,x_solvent_solute_rich=1-xb,
      wt_percent_contaminant_solvent_rich=100*xa*mw/(xa*mw+(1-xa)*ms),wt_percent_contaminant_solute_rich=100*xb*mw/(xb*mw+(1-xb)*ms),
      max_chemical_potential_residual_RT=max(t['chemical_potential_residual'] for t in ties),minimum_tangent_distance_RT=min(t['minimum_tangent_distance_RT'] for t in ties))
    if valid:
     # Preserve raw values; allow only floating-point roundoff at physical endpoints.
     assert -1e-12<=float(r['solute_mole_fraction_solubility'])<=1+1e-12 and -1e-10<=float(r['solute_wt_percent_solubility'])<=100+1e-10
     if ties:assert row['max_chemical_potential_residual_RT']<=1e-7 and row['minimum_tangent_distance_RT']>=-1e-7
    if c_row['inchikey'] in reference_keys:
     ref=lle_reference[(reference_keys[c_row['inchikey']],r['solvent'],r['regime'])];assert r['status']==ref['status']
     lle_repeat.append(max(abs(float(r[k])-float(ref[k])) for k in ['solute_mole_fraction_solubility','solute_wt_percent_solubility']))
    lw.writerow(row);counts['lle_'+r['status']]+=1
   assert unique==set(expected_temperatures)
   predicted=sum(r['status']=='predicted' for r in data['partition']);qualified=sum(r['status'] in ['single_liquid_phase','two_liquid_phases'] for r in data['lle'].values())
   unit_quality[c_row['inchikey']]={'partition_predicted_rows':predicted,'partition_failed_rows':640-predicted,'lle_qualified_rows':qualified,'lle_unresolved_or_failed_rows':64-qualified,'all_requested_quantities_qualified':predicted==640 and qualified==64}
   if idx%250==0:print('RELEASE_ROWS',idx,dict(counts),flush=True)
 # Numeric agreement with the independently sealed eight-phthalate 8.2 run.
 reference={}
 for p in (B/'phase8-v1/phase82/partition').glob('*/predictions.csv'):
  for row in csv.DictReader(p.open()):reference[(p.parent.name,row['solvent'],row['convention'])]=row
 repeat=[]
 for r in validation_rows:
  ref=reference[(r['anchor'],r['solvent'],r['convention'])];assert r['status']==ref['status']=='predicted'
  delta=max(abs(float(r[k])-float(ref[k])) for k in ['logP_x','logP_concentration']);repeat.append(delta)
 assert len(repeat)==512 and max(repeat)<1e-10,('Phase8.2 repeat discrepancy',len(repeat),max(repeat,default=None))
 assert len(lle_repeat)==512 and max(lle_repeat)<1e-9,('Phase8.2 LLE repeat discrepancy',len(lle_repeat),max(lle_repeat,default=None))
 con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='256MB'");con.execute('SET temp_directory=?',[str(work/'duckdb-temp')])
 assert parquet(con,partition,PART,out/'partition.parquet')==len(c['rows'])*640
 assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,campaign_polymer,temperature_K,convention,count(*) n FROM staging GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
 assert parquet(con,binary,LLE,out/'binary-lle.parquet')==len(c['rows'])*64
 assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,temperature_K,count(*) n FROM staging GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
 con.close()
 metadata_pins=json.loads((D/'release-metadata-pins.json').read_text())
 assert metadata_pins['cohort_sha256']==cohort_sha
 assert {p.name for p in (D/'release-metadata').iterdir() if p.is_file()}==set(metadata_pins['files'])
 for name,pin in metadata_pins['files'].items():
  p=D/'release-metadata'/name;assert sha(p)==pin['sha256'] and p.stat().st_size==pin['bytes'];shutil.copyfile(p,out/name)
 with gzip.open(D/'release-metadata/contaminants.csv.gz','rt',newline='') as src,gzip.open(out/'contaminants.csv.gz','wt',newline='') as dst:
  reader=csv.DictReader(src);writer=csv.DictWriter(dst,fieldnames=reader.fieldnames+list(next(iter(unit_quality.values()))));writer.writeheader()
  for r in reader:
   r.update(unit_quality.get(r['input_inchikey'],{}));writer.writerow(r)
 for name,folder in [('phase81','phase8-1-route-comparison'),('phase82','phase8-2-workbook-validation')]:
  source=R/'reports'/folder
  for p in source.rglob('*'):
   if p.is_file():
    dest=out/'validation'/name/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 provenance=out/'provenance';provenance.mkdir(exist_ok=True)
 for name in ['manifest.json','validation-inputs.json','package-pins.json']:shutil.copyfile(B/'phase8-v1'/name,provenance/('phase8-'+name))
 for name in ['cohort.json','cost-gate.json','all-surface-pins.json','surface-staging.json','collected-audit.json','ensemble-audit.json','release-metadata-pins.json']:shutil.copyfile(D/name,provenance/name)
 for name in ['cost-reassessment-first-wave.json','cost-hold.json','production-timing-diagnostic.json']:
  if (D/name).exists():shutil.copyfile(D/name,provenance/name)
 code=provenance/'code';code.mkdir(exist_ok=True)
 sources=[B/'phase8-v1'/name for name in ['phase8_worker.py','phase82_worker.py','phase8_lle.py']]
 sources += [R/'scripts'/name for name in ['phase83_worker.py','phase83.sbatch','prepare_phase83.py','submit_phase83_remote.py','calibrate_phase83.py','reassess_phase83_cost.py','collect_phase83.py','audit_phase83_collected.py','audit_phase83_ensembles.py','prepare_phase84_metadata.py','build_phase84_release.py']]
 code_pins={}
 for source in sources:
  dest=code/source.name;shutil.copyfile(source,dest);assert sha(source)==sha(dest)
  code_pins[source.name]={'source_path':str(source),'sha256':sha(dest)}
 save(provenance/'code-pins.json',code_pins)
 phase83_report=R/'reports/phase83-2026-09-23/REPORT.md'
 lines=phase83_report.read_text().splitlines();assert lines
 lines[0]='# Phase 8.3 complete — all frozen units collected';text='\n'.join(lines)+'\n'
 phase83_report.write_text(text);(D/'REPORT.md').write_text(text)
 shutil.copyfile(phase83_report,provenance/'phase83-REPORT.md')
 for source,name in [(R/'reports/phase83-2026-09-23/THROTTLE.md','THROTTLE.md'),(D/'throttle-changes.jsonl','throttle-changes.jsonl')]:
  if source.exists():shutil.copyfile(source,provenance/name)
 for name in ['solver-staging-correction.json','calibration-repair-submission.json','repair-code-pin.json','repair-timelimit.json']:
  p=R/'state/phase83-v1'/name
  if p.exists():shutil.copyfile(p,provenance/name)
 save(provenance/'collected-raw-archives.json',{'archives':[json.loads(p.read_text()) for p in sorted((D/'returns').glob('*.json'))],'bulk_directory':str(D/'returns')})
 summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'accepted_main':5803,'accepted_tier2':27,'polymer_count':10,'solvent_count':32,'counts':dict(counts),'all_requested_quantities_qualified_contaminants':sum(r['all_requested_quantities_qualified'] for r in unit_quality.values()),'cpu_models':dict(cpu_models),'phase82_repeat_rows':len(repeat),'phase82_max_abs_repeat_difference':max(repeat),'phase82_lle_repeat_rows':len(lle_repeat),'phase82_lle_max_abs_repeat_difference':max(lle_repeat),'cohort_sha256':cohort_sha}
 save(out/'summary.json',summary)
 (out/'README.md').write_text('''# Contaminant promotion release v1

This is a delivery package, not a product database update. It contains 5,803 accepted main-tier CHNO structures and 27 accepted tier-2 structures frozen for Phase 8.3; later returns are outside this run. `contaminants.csv.gz` also makes failed, not-yet-run and isotope-excluded inputs visible (6,103 rows). Its per-structure quality counts distinguish finished calculations from a fully qualified set of requested quantities. Identity acceptance requires the InChIKey first block, with the full perceived key and agreeing perception engines retained.

The ten included polymers are EVOH, nylon6, nylon66, PC, PE, PET, PP, PS, PVC and PVDF. Nitrocellulose, polyethersulfone, polyurethane and PETG were outside the fully converged 8.1 snapshot and are excluded from this run. Their absence is not a result about their partitioning.

`partition.parquet` is keyed by input InChIKey, product solvent key, campaign polymer, temperature and convention. It retains BOTH normalized and existing conventions at 298.15 K. These are neutral-species partition coefficients; no pH-dependent ionization or speciation model is applied. logP_x = (ln gamma_polymer - ln gamma_solvent)/ln(10); logP_concentration = logP_x + log10(V_polymer/V_solvent). Positive favors solvent. The owner has not selected a promoted convention. The immutable 236-conformer manifest supplies individual polymer surface digests, energies and cavity volumes; each row links that manifest in addition to its solute and solvent surface digests. Conformers were explicitly Boltzmann averaged, with no multi-conformer engine shortcut.

`binary-lle.parquet` is keyed by input InChIKey, product solvent key and temperature. It uses 298.15 K and each solvent's literal workbook high temperature. Solubilities are raw contaminant mole fractions and weight percentages; both phase compositions and all tie lines are retained. In a single liquid phase, solubility is 1 (100 wt%); coexistence endpoints remain blank because there is no second phase. Unresolved outputs are explicitly unvalidated; their numerical estimates must not be treated as qualified predictions. No solid fusion correction is included: this is binary liquid-liquid equilibrium, not new solid-liquid solubility. Both 15 mol% and 15 wt% verdicts are recorded, with the near-threshold indeterminate band used in 8.2. The owner still chooses the applicable basis and workbook layout interpretation.

The unmodified validated solver uses 1,000/2,000-interval grids with dilute tails, three tie-line initial guesses, chemical-potential equality and global tangent checks. Every failure is a status row. Nothing was interpolated or empirically recalibrated. Raw activities and detailed solver evidence remain in the hash-verified archives listed in provenance.

`polymer-product-map.csv` explicitly maps PE to BOTH LDPE and HDPE; this electronic ensemble does not supply distinct crystallinity or fusion properties. PVDF is recognized as an identity but lacks a stored legacy polymer-solubility grid. Existing polymer S(T) remains LEGACY for leaching/STRAP; no new 24a polymer solubility is claimed, so partition coverage alone does not imply complete dissolution-screen coverage.

Validation files retain the finalized ten-polymer route comparison and eight-phthalate workbook comparison. These are computational-reference agreement checks, not experimental accuracy claims. The two routes differ in engine, parameterization and reoptimized geometry simultaneously; the comparison measures the whole route, not a parameterization-only effect. Workbook ambiguity, the DEHP water-pair discrepancy, and both conventions remain visible. PFAS is outside scope. Generic xylene identity and the didecyl phthalate measured source remain owner questions.

Reproduction: `/home/aaltamimi2/plastchem-euler/scripts/prepare_phase83.py`, `phase83_worker.py`, `submit_phase83_remote.py`, `calibrate_phase83.py`, `collect_phase83.py`, `prepare_phase84_metadata.py`, and `build_phase84_release.py`. Bulk execution evidence: `/mnt/r/plastchem-euler/phase83-v1/`. Euler computation: research/milan, one CPU and 4 GB per task under the shared 64 cap. The release was constructed by streaming verified results, checking complete Cartesian coverage and unique keys, and repeating the sealed 512-row PVC partition comparison.
''')
 files={str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'}
 size=sum(v['bytes'] for v in files.values());assert size<200_000_000,('Release exceeds target',size)
 save(out/'manifest.json',{'utc':summary['utc'],'cohort_sha256':summary['cohort_sha256'],'files':files,'payload_bytes':size})
 # All payloads are hashed inside the manifest; the manifest itself is sealed externally.
 seal={'release_id':sha(out/'manifest.json'),'manifest_sha256':sha(out/'manifest.json'),'file_count':len(files)+1,'total_bytes':size+(out/'manifest.json').stat().st_size,'path':str(target)}
 out.rename(target);save(D/'release-seal.json',seal);report=R/'reports/phase84-release';report.mkdir(exist_ok=True);save(report/'release-seal.json',seal)
 successful=counts['partition_predicted'];qualified=counts['lle_single_liquid_phase']+counts['lle_two_liquid_phases']
 (report/'REPORT.md').write_text(f'''# Phase 8.4 promotion release

Released **`/mnt/r/plastchem-euler/promotion-v1/`**. Release ID: `{seal['release_id']}`. The delivery contains {seal['file_count']} files, {seal['total_bytes']/1e6:.2f} MB. Every payload file is hashed in `manifest.json`; the manifest itself is hashed in the external `release-seal.json`. Product code and databases were not changed. This is a delivery for orchestrator promotion.

## Coverage

The frozen cohort is **5,803 main-tier + 27 tier-2 = 5,830 accepted contaminants**, ten polymers, 32 solvents. The identity/status table also preserves 28 failed campaign records, 236 not-yet-run tier-2 records and nine isotope exclusions, for 6,103 structures at the snapshot. Later returns were not patched into this release.

| Quantity | Rows | Qualified/successful | Other statuses |
|---|---:|---:|---:|
| Partitioning, both conventions | 3,731,200 | {successful:,} | {3731200-successful:,} |
| Binary LLE, both temperatures | 373,120 | {qualified:,} | {373120-qualified:,} |

Every key is present exactly once. Failed/unresolved calculations remain explicit status rows; no missing value was interpolated. Partitioning is at 298.15 K. LLE is at 298.15 K and each solvent's literal workbook high temperature. Both mole-fraction and concentration partition bases are retained; LLE includes mole fraction, wt%, both phase compositions, tie lines and both grid-check records.

## Validation and execution

The sealed 8.2 PVC partition comparison repeats over 512 rows with maximum absolute difference {max(repeat):.3g}. All 512 sealed LLE systems repeat with maximum difference {max(lle_repeat):.3g} in their raw reported solubility fields. The finalized ten-polymer comparison and workbook validation reports, tables and figures are included under `validation/`.

These comparisons validate agreement with computational references, not experimental accuracy. The two routes differ in engine, parameterization and reoptimized geometry simultaneously; no parameterization-only attribution is supported. Calibration cost was 15.09 allocated CPU-hours, including the recovered provenance-staging error; the planning total was 3,748 CPU-hours under the 6,000-hour gate. Execution and archive evidence is in `provenance/` and `/mnt/r/plastchem-euler/phase83-v1/`.

## Owner decisions and limits

Both normalized and existing conventions are delivered; the owner chooses which to promote. The miscibility threshold basis and workbook layout interpretation remain owner decisions. Generic xylene identity and the didecyl phthalate measured source remain open. PE maps explicitly to both LDPE and HDPE. PVDF has partitioning results but lacks a stored legacy S(T) grid. Polymer S(T) remains legacy for leaching/STRAP; no new 24a polymer solubility or PFAS computation is claimed. The partition results describe neutral species without a pH/speciation model.

Reproduction commands and full script paths are in the release README. The requested Phase 8.4 release is complete; stop at this gate.
''')
 print(json.dumps(seal,indent=2))
if __name__=='__main__':main()
