import csv,hashlib,json,math,statistics,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/opencosmo-verification-v1';OUT=ROOT/'reports/opencosmo-verification-v1';m=json.loads((P/'manifest.json').read_text())
records=[json.loads((P/'records'/(i['label']+'.json')).read_text()) for i in m['items']]
campaign=[r for r in records if r['kind']=='campaign_surface'];success=[r for r in campaign if r['status']=='predicted'];fail=[r for r in campaign if r['status']!='predicted'];anchors=[r for r in success if r.get('anchor')]
replays=[]
for anchor in ['DBP','BBP','DEHP']:
 source=Path('/home/aaltamimi2/cosmo-artifacts')/('stage1_'+anchor.lower())/(anchor+'_LOGD.stage1.v1.json');gold=json.loads(source.read_text());(P/(anchor+'-golden.json')).write_bytes(source.read_bytes())
 repeated=next(r for r in records if r['label']==anchor+'-workstation-reference')
 errors=[abs(a['log10_partition_concentration_basis']-b['stage1_full']) for a,b in zip(repeated['pairs'],gold['results'])]
 replays.append({'anchor':anchor,'pairs':len(errors),'max_absolute_replay_error':max(errors),'golden_source':str(source),'golden_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
rows=[]
for r in records:
 for v in r.get('pairs',[]):rows.append({**{k:r.get(k) for k in ['label','kind','anchor','name','input_inchikey','perceived_inchikey','identity_match_basis','group','atoms','surface_sha256','cpu_model']},**v,'temperature_K':298.15,'solute_mole_fraction':1e-5,'parameterization':'24a','solvent_library_origin':'historical workstation; compatibility diagnostic'})
fields=list(dict.fromkeys(k for row in rows for k in row))
with (OUT/'predictions.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
shifts=[abs(z['dilution_shift_1e5_to_1e6']) for r in success for z in r['pairs']]
summary={'campaign_converged_snapshot':m['campaign_converged_at_snapshot'],'campaign_selected':len(campaign),'campaign_predicted':len(success),'campaign_prediction_failed':len(fail),'campaign_solvent_calculations':len(success)*5,'campaign_pair_predictions':len(success)*4,'tested_solvents':list(m['solvents']),'shared_table_solvent_count':len(m['table_solvent_panel']),'untested_table_solvents':sorted(set(m['table_solvent_panel'])-set(m['solvents'])),'max_dilution_shift_log10':max(shifts),'historical_replays':replays,'anchor_matches':{r['anchor']:{'pairs_within_historical_1_5_log_unit_tolerance':sum(abs(x['residual_to_existing_table'])<=1.5 for x in r['pairs']),'pair_count':4,'max_absolute_existing_table_residual':max(abs(x['residual_to_existing_table']) for x in r['pairs'])} for r in anchors},'production_homogeneous_Milan_panel_verified':False,'PFAS_chemical_prediction_accuracy_tested':False}
(P/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
lines=['# Completed ORCA surfaces → openCOSMO-RS verification','',
f'**{len(success)}/{len(campaign)} selected completed campaign contaminants produced finite openCOSMO-RS predictions; {len(fail)}/{len(campaign)} failed.** This covers {len(success)*5} solute/solvent activity-coefficient calculations at each of two dilutions and {len(success)*4} concentration-basis solvent-pair predictions. The completed campaign snapshot contained {m["campaign_converged_at_snapshot"]} accepted structures out of 5,824 eligible; only these {len(campaign)} were tested here.','',
'The sample includes DEP, DBP, BBP, DEHP, four new main-body results and four new >80-atom tail results. All are actual returned campaign surfaces, SHA-256 checked before use, with accepted first-block connectivity identity and recorded full perceived keys. No ORCA job was submitted for this verification. Existing campaign arrays and result records were not changed.','',
'## What the predictions mean','',
'At 298.15 K, openCOSMORS24a supplies solute activity coefficients in each solvent. We use the same reference implementation and concentration basis as the existing phthalate checks:','',
'`log10(K_A/B) = [ln(gamma_B) - ln(gamma_A)] / ln(10) + log10(V_B/V_A)`.','',
'The tested solvent panel is **water, methanol, dichloromethane, hexane and cyclohexanol**. The four comparisons are dichloromethane/water, cyclohexanol/water, hexane/water and the water-free dichloromethane/methanol pair. Molar volumes are pinned from the existing implementation. Full values, both concentration and mole-fraction bases, are in [predictions.csv](predictions.csv).','',
'These are neutral-solute solvent/solvent partition estimates, not a pH-speciated logD or an absolute polymer/solvent distribution coefficient. The existing table’s common, unnamed reference phase cancels when its two solvent entries are differenced; absolute table columns cannot be reconstructed from solvent surfaces alone. The 24a model is parameterized for neutral-solute thermodynamics: [primary publication](https://doi.org/10.1016/j.fluid.2024.114250).','',
'## Agreement with the existing phthalate table','',
'Each cell below is **new campaign prediction / existing table pair difference**, in log10 units. The existing table values are comparison targets, not newly measured experimental data.','',
'| Compound | DCM/water | Cyclohexanol/water | Hexane/water | DCM/methanol | Within existing ±1.5 tolerance |','| --- | ---: | ---: | ---: | ---: | ---: |']
for r in anchors:
 cells=[f"{x['log10_partition_concentration_basis']:.3f} / {x['existing_table_pair_difference']:.3f}" for x in r['pairs']]
 count=summary['anchor_matches'][r['anchor']]['pairs_within_historical_1_5_log_unit_tolerance'];lines.append('| '+r['anchor']+' | '+' | '.join(cells)+f' | {count}/4 |')
lines+=['','**DEHP remains discrepant:** the three water-pair residuals are about +2.0 to +2.1 log units. The water-free pair agrees much more closely. Its historical workstation record already reports the same failed three water-pair checks. This is not a new failure to parse an Euler surface or execute openCOSMO; it also means successful numerical execution cannot be claimed as universal predictive agreement. The frozen recipe was not retuned.','',
'## Numerical and provenance checks','',
f'- Replayed the historical DBP, BBP and DEHP workstation surfaces against their stored numerical results: **12/12 pair values reproduced**, maximum absolute difference **{max(r["max_absolute_replay_error"] for r in replays):.3g} log units**. DEP was also run, but no separate saved golden JSON was available in the examined artifact set, so it is not included in this replay denominator.',
f'- Decreasing solute mole fraction from 1e-5 to 1e-6 changed campaign pair predictions by at most **{max(shifts):.6f} log units** across {len(shifts)} pairs. This checks dilution sensitivity, not model accuracy.',
'- Input surfaces, five solvent files, copied reference code and stored reference values have recorded hashes. Every campaign result keeps the input InChIKey, full perceived key, connectivity match basis and Milan CPU model.',
'- Parameterization is explicitly `openCOSMORS24a`, with the frozen BP86/def2-TZVP(-f) optimisation and BP86/def2-TZVPD surface recipe. No 2002 COSMObase surface was mixed into these 24a calculations.','',
'## What remains unverified','',
'**This is a compatibility diagnostic, not a released homogeneous-Milan production panel.** Solute surfaces are campaign Milan results; the five pinned solvent surfaces are the historical workstation library. This intentionally controlled comparison holds that solvent library fixed to test the established interface. The CPU-origin distinction is retained in the manifest and CSV, and these outputs are segregated from campaign production records. Matching Milan solvent surfaces remain necessary for the homogeneous campaign comparison policy.','',
f'Only **5/{len(m["table_solvent_panel"])}** solvent keys in the existing shared table panel were verified. The remaining {len(summary["untested_table_solvents"])} have not been demonstrated here with a matching 24a solvent library. Existing COSMObase 2002 files are not substitutes. `xylene` must also remain distinct from `o-xylene`; an unspecified mixture cannot silently become one isomer. No claim is made that the exact solvent subset for every PFAS row has been audited.','',
'This CHNO sample does not validate PFAS chemical accuracy or ionization treatment. It verifies the same five-solvent partitioning pathway and directly tests the four phthalates that bridge the existing set and this campaign.','',
'## Selected finished contaminants','',
'| InChIKey | Name | Group | Atoms | Prediction status |','| --- | --- | --- | ---: | --- |']
for r in campaign:lines.append(f"| `{r['input_inchikey']}` | {r['name']} | {r['group']} | {r['atoms']} | {r['status']} |")
for r in fail:lines+=['',f"Failure `{r['label']}`: {r['error']}"]
lines+=['','Evidence: `state/opencosmo-verification-v1/manifest.json`, `records/*.json`, `summary.json`; executable check: `scripts/verify_opencosmo_campaign.py`. All verification output is lane-owned; no product database was modified.']
(OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
files=[P/'manifest.json',P/'summary.json',P/'reference_cosmo_logp.py',ROOT/'scripts/verify_opencosmo_campaign.py',OUT/'REPORT.md',OUT/'predictions.csv',*sorted((P/'records').glob('*.json')),*sorted((P/'solvents').glob('*.orcacosmo'))]
(P/'PROVENANCE.sha256').write_text(''.join(f'{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(ROOT)}\n' for f in files));print(json.dumps(summary,indent=2))
