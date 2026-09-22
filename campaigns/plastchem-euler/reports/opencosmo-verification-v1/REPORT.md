# Completed ORCA surfaces → openCOSMO-RS verification

**12/12 selected completed campaign contaminants produced finite openCOSMO-RS predictions; 0/12 failed.** This covers 60 solute/solvent activity-coefficient calculations at each of two dilutions and 48 concentration-basis solvent-pair predictions. The completed campaign snapshot contained 155 accepted structures out of 5,824 eligible; only these 12 were tested here.

The sample includes DEP, DBP, BBP, DEHP, four new main-body results and four new >80-atom tail results. All are actual returned campaign surfaces, SHA-256 checked before use, with accepted first-block connectivity identity and recorded full perceived keys. No ORCA job was submitted for this verification. Existing campaign arrays and result records were not changed.

## What the predictions mean

At 298.15 K, openCOSMORS24a supplies solute activity coefficients in each solvent. We use the same reference implementation and concentration basis as the existing phthalate checks:

`log10(K_A/B) = [ln(gamma_B) - ln(gamma_A)] / ln(10) + log10(V_B/V_A)`.

The tested solvent panel is **water, methanol, dichloromethane, hexane and cyclohexanol**. The four comparisons are dichloromethane/water, cyclohexanol/water, hexane/water and the water-free dichloromethane/methanol pair. Molar volumes are pinned from the existing implementation. Full values, both concentration and mole-fraction bases, are in [predictions.csv](predictions.csv).

These are neutral-solute solvent/solvent partition estimates, not a pH-speciated logD or an absolute polymer/solvent distribution coefficient. The existing table’s common, unnamed reference phase cancels when its two solvent entries are differenced; absolute table columns cannot be reconstructed from solvent surfaces alone. The 24a model is parameterized for neutral-solute thermodynamics: [primary publication](https://doi.org/10.1016/j.fluid.2024.114250).

## Agreement with the existing phthalate table

Each cell below is **new campaign prediction / existing table pair difference**, in log10 units. The existing table values are comparison targets, not newly measured experimental data.

| Compound | DCM/water | Cyclohexanol/water | Hexane/water | DCM/methanol | Within existing ±1.5 tolerance |
| --- | ---: | ---: | ---: | ---: | ---: |
| DEP | 5.126 / 4.810 | 3.511 / 2.920 | 3.005 / 2.410 | 1.479 / 1.790 | 4/4 |
| DBP | 7.605 / 7.050 | 5.876 / 5.170 | 5.640 / 4.980 | 1.916 / 2.180 | 4/4 |
| BBP | 8.065 / 7.180 | 6.307 / 5.040 | 5.941 / 4.610 | 1.924 / 2.140 | 4/4 |
| DEHP | 12.115 / 9.980 | 10.214 / 8.130 | 10.355 / 8.330 | 2.713 / 2.600 | 1/4 |

**DEHP remains discrepant:** the three water-pair residuals are about +2.0 to +2.1 log units. The water-free pair agrees much more closely. Its historical workstation record already reports the same failed three water-pair checks. This is not a new failure to parse an Euler surface or execute openCOSMO; it also means successful numerical execution cannot be claimed as universal predictive agreement. The frozen recipe was not retuned.

## Numerical and provenance checks

- Replayed the historical DBP, BBP and DEHP workstation surfaces against their stored numerical results: **12/12 pair values reproduced**, maximum absolute difference **3.55e-15 log units**. DEP was also run, but no separate saved golden JSON was available in the examined artifact set, so it is not included in this replay denominator.
- Decreasing solute mole fraction from 1e-5 to 1e-6 changed campaign pair predictions by at most **0.008674 log units** across 48 pairs. This checks dilution sensitivity, not model accuracy.
- Input surfaces, five solvent files, copied reference code and stored reference values have recorded hashes. Every campaign result keeps the input InChIKey, full perceived key, connectivity match basis and Milan CPU model.
- Parameterization is explicitly `openCOSMORS24a`, with the frozen BP86/def2-TZVP(-f) optimisation and BP86/def2-TZVPD surface recipe. No 2002 COSMObase surface was mixed into these 24a calculations.

## What remains unverified

**This is a compatibility diagnostic, not a released homogeneous-Milan production panel.** Solute surfaces are campaign Milan results; the five pinned solvent surfaces are the historical workstation library. This intentionally controlled comparison holds that solvent library fixed to test the established interface. The CPU-origin distinction is retained in the manifest and CSV, and these outputs are segregated from campaign production records. Matching Milan solvent surfaces remain necessary for the homogeneous campaign comparison policy.

Only **5/33** solvent keys in the existing shared table panel were verified. The remaining 28 have not been demonstrated here with a matching 24a solvent library. Existing COSMObase 2002 files are not substitutes. `xylene` must also remain distinct from `o-xylene`; an unspecified mixture cannot silently become one isomer. The later read-only coverage audit below resolves the exact solvent subset for every PFAS and phthalate entry; it does not extend the five-solvent numerical diagnostic.

This CHNO sample does not validate PFAS chemical accuracy or ionization treatment. It verifies the same five-solvent partitioning pathway and directly tests the four phthalates that bridge the existing set and this campaign.

## Selected finished contaminants

| InChIKey | Name | Group | Atoms | Prediction status |
| --- | --- | --- | ---: | --- |
| `FLKPEMZONWLCSK-UHFFFAOYSA-N` | Diethyl Phthalate | main_le80 | 30 | predicted |
| `DOIRQSBPFJWKBE-UHFFFAOYSA-N` | Dibutyl Phthalate | main_le80 | 42 | predicted |
| `IRIAEXORFWYRCZ-UHFFFAOYSA-N` | Benzyl butyl phthalate | main_le80 | 43 | predicted |
| `BJQHLKABXJIVAM-UHFFFAOYSA-N` | Bis(2-ethylhexyl) phthalate | main_le80 | 66 | predicted |
| `JNHSEDRFFJZMLH-UHFFFAOYSA-N` | 2-Methyltricosane | main_le80 | 74 | predicted |
| `YKNWIILGEFFOPE-UHFFFAOYSA-N` | Pentacosane | main_le80 | 77 | predicted |
| `OKMWKBLSFKFYGZ-UHFFFAOYSA-N` | Glyceryl behenate | main_le80 | 79 | predicted |
| `NWDVCUXGELFJOC-UHFFFAOYSA-N` | 1,2,3-Propanetricarboxamide, N1,N2,N3-tris(2-methylcyclohexyl)- | main_le80 | 80 | predicted |
| `CLDFUWPQRCVRHQ-UHFFFAOYSA-N` | Hentriacontan-16-ol | tail_gt80 | 96 | predicted |
| `QHMGJGNTMQDRQA-UHFFFAOYSA-N` | Dotriacontane | tail_gt80 | 98 | predicted |
| `GWVDBZWVFGFBCN-UHFFFAOYSA-N` | Tetratriacontane | tail_gt80 | 104 | predicted |
| `VHQQPFLOGSTQPC-UHFFFAOYSA-N` | Pentatriacontane | tail_gt80 | 107 | predicted |

Evidence: `state/opencosmo-verification-v1/manifest.json`, `records/*.json`, `summary.json`; executable check: `scripts/verify_opencosmo_campaign.py`. All verification output is lane-owned; no product database was modified.

## Reference solvent coverage audit — 2026-09-14

Read the product contaminants database with DuckDB `read_only=True`, one thread and a 128 MB memory limit. Its SHA-256 was identical before and after inspection and matches the copied reference implementation’s pinned database: `866d769b6a140bf289c5036fd5c0d7d2b6f424cb7994e71c76e16a1a1d9a4c5f`. No product file was modified.

All 26 PFAS entries have 32 solvent rows each (832 values); all eight phthalates have 32 each (256 values). All 1,088 values are non-null. The families share 31 solvent keys. PFAS uses `xylene` and omits `o-xylene`; phthalates use `o-xylene` and omit `xylene`. Their union is 33 keys. The five solvents in the numerical diagnostic occur for every entry in both families. Unspecified `xylene` still needs an explicit identity before production predictions can cover the PFAS panel completely.

The database describes its own runtime claim as “screening proxy; not validated partition coefficients”. Matching these reference values therefore checks compatibility with the existing table, not independent experimental accuracy. PFAS ionization remains outside this neutral CHNO verification.

Per-entry solvent lists, metadata, source digest and read timestamp are preserved in `state/opencosmo-verification-v1/reference-solvent-coverage.json`.

### Xylene normalization evidence — follow-up

The same pinned database explicitly records PFAS `solvent_raw=Xylene`, `solvent_key=xylene`, and `solvent_normalized=1,4-dimethylbenzene` (p-xylene). Phthalates record `o-xylene` normalized to `1,2-dimethylbenzene`. The product `_solvent_keys` code also expressly distinguishes catalog xylene (p-xylene) from o-xylene. This resolves what the product mapping does; it does not establish the original workbook’s intended identity. That workbook is outside this lane’s permitted read surface and was not inspected. User clarification was requested on adopting the documented product mapping, with the original label and explicit p-xylene identity retained. Production inventory remains unchanged pending that answer. The evidence JSON now includes the exact database rows and product-code digest.
