# PlastChem reading notes and ORCA + openCOSMO campaign census

Drafted 2026-09-12 from the files in this folder plus https://github.com/PlastChem/DB. Si/B split after the census audit; CHNO-only submission the same day.
Live workbook: `plastchem_db_v1.0.xlsx` (Release date on the README sheet: 14.03.2024), sha256 `df4ccad3b238b3b76d180db48892c9c17e7cd823cecb495e7888ea0690216984`.
Audited MW≤500 census (Si/B still in): `plastchem_organics_orca_opencosmo_firstpass_audited_8065.csv`, sha256 `60f56f9faeb845a73ae3de89cb1b75eb1c9dcae7bb9f7f7da2bcbf635a05f8ad` (8,065 rows / 7,792 unique).
Si/B held out, still has F/P/S/halogens: `plastchem_organics_orca_opencosmo_firstpass_no_sib.csv`, sha256 `976013581fabbee8e7c35c7af1236d626e3385dd553d280f34a3d31fbcbea076` (7,826 rows / 7,554 unique).
**Submission (CHNO only):** `plastchem_organics_orca_opencosmo_firstpass.csv`, sha256 `a8e1ae169bbf8a5831a24bd8a83ba4a0e36cf69f3ce59e774fc4bd21aff92828` (6,064 rows / 5,833 unique).
Flagged F/P/S/Cl/Br/I: `plastchem_organics_orca_opencosmo_flagged_heteroatoms.csv`, sha256 `0270fc64a02c3d6ff426c67dab077ddbcf46feb52086f91505e8608a0741fc81` (1,762 rows / 1,721 unique).
Flagged Si/B: `plastchem_organics_orca_opencosmo_flagged_si_b.csv`, sha256 `050d79f70f81f1dd30aaf9c4a594ed2c033dc0022d9ab9588668a1bd39af3a7a` (239 rows / 238 unique).

This is a reading / scoping note. It does not write into `contaminants.duckdb`. These SMILES are **not** the Zhou 34 (26 PFAS + 8 phthalates) already in DISSOLVE.

---

## 1. What you handed over

| File | What it is |
|---|---|
| `s41586-025-09184-8.pdf` | Monclús et al., *Nature* **643**, 349 (9 July 2025), doi [10.1038/s41586-025-09184-8](https://doi.org/10.1038/s41586-025-09184-8). Title: *Mapping the chemical complexity of plastics*. |
| `41586_2025_9184_MOESM1_ESM.pdf` | Nature SI: Texts S1–S7, workflows, Tables S1–S10. |
| `41586_2025_9184_MOESM4_ESM.xlsx` | Figure 2 source (hazard categories; per-chemical hazard scores). |
| `41586_2025_9184_MOESM5_ESM.xlsx` | Figure 3 source (CAS × polymer use/detection). |
| `41586_2025_9184_MOESM6_ESM.xlsx` | Figure 4 source (priority groups). |
| `41586_2025_9184_MOESM7_ESM.csv` | 17,932 rows: PlastChem ID × which of the seven sources it came from. |
| `41586_2025_9184_MOESM8_ESM.csv` | 11,331 rows: OPERA log *K*OW vs molecular weight (Extended Data Fig. 3). |
| `41586_2025_9184_MOESM9_ESM.xlsx` | Extended Data Fig. 4 (P/B/M/T overlap and toxicity traits). |
| `41586_2025_9184_MOESM10_ESM.csv` | 17,932 rows: CAS × polymer-type presence plus `Hazard_score`. |
| `41586_2025_9184_MOESM11_ESM.csv` | 28 function groups with count, mean hazard, % of concern. |
| `PlastChem_State_of_the_Science_on_Plastic_Chemicals_Report.pdf` | Wagner et al. (2024) *State of the science on plastic chemicals*, Zenodo [10.5281/zenodo.10701706](http://dx.doi.org/10.5281/zenodo.10701706). The report the database launched with. |
| `plastchem_db_v1.0.xlsx` | The actual inventory. 11 sheets: README, Overview, Full database (232 columns), MEA/Red/Orange/Watch/White/Grey lists, Duplicates, abbreviations. |
| https://github.com/PlastChem/DB | R scripts that assembled a **beta**. Five of seven sources are open; two confidential sources are omitted. v1.00 was then **manually curated**. Do not treat the GitHub repo as the v1.0 table. |

---

## 2. What PlastChem is (and is not)

**Plastic chemical** (SI Text S1): anything present in plastic materials/products, including the polymer backbone, intentionally added substances (starting substances, processing aids, additives), and NIAS (impurities, residual intermediates, reaction by-products, oligomers).

**Explicitly excluded:** contaminants that **sorb onto plastics during use or end-of-life**. That is a different object from DISSOLVE’s Zhou leaching screen. Phthalates as *additives* are in PlastChem; environmental PFAS that only sorb onto debris are not, by definition. Some PFAS still appear because they are used or found *in* plastics (Nature: 440 PFAS in the 2024 report figure; SI Table S9 has 274 unregulated + 166 globally regulated PFAS).

CAS RN is the unique key. PubChem supplied structures: CID, formula, MW, canonical/isomeric SMILES, InChI, InChIKey, XLogP, TPSA, charge. Invalid CAS RNs were repaired then dropped if they still failed.

Seven sources (Nature Fig. 1 / report Table 1): PlasticMAP 10,547; Aurisano 5,983; FCCmigex 4,257; CPPdb 4,227; LitChemPlast 3,467; FCCdb ~3,543 (report) / FCC in the figure; ECHA food-contact 1,046. Overlap is large; each source still has unique entries. That is why the union is bigger than UNEP’s “>13,000”.

---

## 3. Headline counts — report (2024) vs Nature (2025) vs this xlsx

The live `Full database` sheet has **17,932** rows with a PlastChem ID (plus 8 empty trailing rows in Excel). Flags on that sheet:

| Flag | n in xlsx | Matches |
|---|---:|---|
| `inorganic_compounds` | 926 | Nature and report, exact |
| `organometallics` | 365 | SI Table S9, exact |
| `UVCBs` | 1,103 | report, exact |
| `polymers` | 2,329 | report, exact |
| `mixtures` | 1,521 | report, exact |
| any of UVCB/polymer/mixture | 3,667 | Nature, exact |
| canonical SMILES present | 12,955 | report said 12,990 unique structures; Nature 12,658 discrete |

**Nature 2025 Fig. 1a (the numbers to quote for the paper era):**

- 17,932 total entries
- 16,325 unique CAS RNs (the “core” they analyse)
- 1,607 without valid CAS
- 12,658 discrete compounds with known structures
- **11,500 organic** / **926 inorganic** / 232 unknown in that pie
- 3,667 polymers + mixtures + UVCBs (23%), not assessable as a single structure

**2024 report** used a slightly different organic cut: **11,950 organic (73%)**, 926 inorganic (6%), 3,449 lacking elemental-composition information. Same 16,325 CAS core, same 926 inorganics. The Nature 11,500 is the later, tighter organic discrete number. This campaign used the xlsx flags + SMILES chemistry, not the figure’s 11,500 as a row filter.

**Functions (Nature):** 5,776 additives, 3,498 processing aids, 1,975 starting substances, 1,788 NIAS.

**Hazards (Nature):** >4,200 chemicals of concern (PBMT: persistent, bioaccumulative, mobile, or toxic). 4,184 toxic; 2,760 aquatic tox; 1,774 STOT; 1,489 CMR. Only ~6% of the 16,325 are under international MEAs.

**15 priority groups** (SI Table S9), ranked by % of members that are of concern. Includes ortho-phthalates (61 chemicals, 54% of concern) and organometallics (365, 44.1% of concern). PFAS as a group is *not* in the final 15 because <40% of members have a CoC classification (data gap, not “safe”).

---

## 4. How they labelled organic vs inorganic / metal

SI Text S7: keyword search on names (Wang et al. 2020 keys) for inorganic, organometallic/metalorganic, UVCB, polymer, mixture; then element names/symbols in **names and SMILES** for organohalogens, organophosphates, organosilicons, and metal(loid)s; then list-matching (EPA CompTox, ECHA groups, etc.); then manual review.

So `inorganic_compounds=1` and `organometallics=1` are **name/SMILES keyword flags**, not a full periodic-table walk of the graph. That matters: after dropping those two flags we still found **1,237** remaining structures whose SMILES contain a metal atom. Most are salts (Na 558, K 86, Zn 75, Ca 72, Al 51, Cd 49, …) that PlastChem did not tag as inorganic. Examples: potassium laurate, cobalt(II) stearate, cadmium sulfate, phenylmercuric hydroxide. Those are exactly “inorganics / metallic centers” for DFT.

ORCA input in DISSOLVE is hard-coded `* xyz 0 1` (neutral singlet). Salts, net-charged molecules, and radicals are not a drop-in.

---

## 5. What “all the organics, ORCA + openCOSMO” actually means

DISSOLVE’s existing `/contaminant logp` path (do not change for this campaign):

- Level: BP86 / def2-TZVP(-f) geometry, then `COSMORS(Water)` which writes `.orcacosmo` at the 24a level (BP86 / def2-TZVPD).
- Serial ORCA, no `%pal`. `%maxcore 1500` per process. `MAX_CONCURRENT_DFT = 4` is the local flock cap, not a physics requirement.
- **One conformer** (`n_conformers: 1`). RDKit ETKDG+MMFF ranks, DFT the lowest. Ensemble is *not* the default.
- One solute DFT is enough for every solvent: the `.orcacosmo` surface is reused. Held solvents already exist (water, methanol, dichloromethane, hexane, cyclohexanol). openCOSMO-RS 24a after that is seconds, not hours.
- Identity is InChIKey after opt.

openCOSMO-RS 24a will *parse* any element symbol. The **installed** `openCOSMORS24a` class names element-specific van der Waals τ only for H, C, N, O, F, Si, P, S, Cl, Br (`tau_1,6,7,8,9,14,15,16,17,35`). That is not the same as “COSMO-typical organics.” See §5a.

### Funnel measured on `plastchem_db_v1.0.xlsx`

Start: **17,932** entries.

| Drop | n dropped | Remaining idea |
|---|---:|---|
| PlastChem `inorganic_compounds` or `organometallics` | 1,289 | 926 + 365 with 2-count overlap |
| UVCB / polymer / mixture (cannot DFT a composition) | 3,572 more | Nature’s 3,667 overlap with the inorganics |
| no SMILES | 2,381 | PubChem had no structure |
| SMILES has a metal atom | 1,237 | mostly untagged salts |
| multi-fragment (`.` salts, hydrates) | 382 | e.g. organic hydrochlorides |
| net charge ≠ 0 | 43 | `xyz 0 1` would be wrong |
| radical | 29 | same |
| element outside {H,B,C,N,O,F,Si,P,S,Cl,Se,Br,I} | 12 | As and similar. This was the *census* keep-set, not the 24a τ table. |
| unparsed SMILES | 1 | |
| no carbon | 25 | H2, N2, water, ammonia, HF, … |

**DFT-eligible organics, no MW cut: 8,961 rows / 8,661 unique InChIKeys.**

That is the honest “all organics we can actually submit”. It is **not** 11,500. The 11,500 includes metal salts, organometallics counted as organic, charged species, and disconnected salts.

### MW cut (so nothing is too large)

PubChem MW median on structures is 242 g/mol. Eligible-organics quantiles: p50 = 224, p90 = 500, p95 = 642, p99 = 907.

DEHP (already run) is 390.6 g/mol, 66 atoms including H, ~69 min serial. MW **500 g/mol** is the first-pass cut: it keeps the phthalate ladder and most plasticizers, and it sits at the 90th percentile so the DFT tail does not explode.

| Cut | rows | unique InChIKey |
|---|---:|---:|
| MW ≤ 300 | 6,080 | — |
| MW ≤ 400 | 7,358 | 7,111 |
| **MW ≤ 500 (census, Si/B still in)** | **8,065** | **7,792** |
| MW ≤ 500, Si/B held out | 7,826 | 7,554 |
| **MW ≤ 500, CHNO only (submission)** | **6,064** | **5,833** |
| MW ≤ 700 | 8,682 | 8,390 |
| no MW cut | 8,961 | 8,661 |
| n_atoms ≤ 66 (DEHP-sized) | 7,970 | 7,706 |

DEP, DBP, BBP and DEHP are CHNO and stay in the submission CSV. PFAS (F) and most flame-retardant phosphates/halogens do not — they sit in the heteroatom expansion file. Median MW on the CHNO list is 198; median atoms-with-H = 32; p90 = 61; max 107.

**Submit 5,833 unique structures**, not 6,064 jobs (231 remaining CHNO duplicates).

---

## 5a. Silicon and boron — domain check (audit correction)

The census keep-set was `{H, B, C, N, O, F, Si, P, S, Cl, Se, Br, I}`. The audited 8,065-row CSV therefore contained **226 silicon** rows and **14 boron** rows (239 distinct rows: one molecule has both Si and B). The notes called this “COSMO-typical elements only.” That claim does not hold as stated.

**What 24a actually names**

Müller et al., *Fluid Phase Equilibria* (2024) / arXiv:2407.03434 Table 1, and the installed class `opencosmorspy.parameterization.openCOSMORS24a`:

| τ | Z | Element | In the 24a paper? | In installed `openCOSMORS24a`? |
|---|---:|---|---|---|
| τ₁ | 1 | H | yes | yes |
| τ₆ | 6 | C | yes | yes |
| τ₇ | 7 | N | yes | yes |
| τ₈ | 8 | O | yes | yes |
| τ₉ | 9 | F | yes | yes |
| τ₁₄ | 14 | **Si** | **yes** (0.018 kJ mol⁻¹ Å⁻²) | **yes** (`tau_14 = 4.215503e-03` kcal mol⁻¹ Å⁻²) |
| τ₁₅ | 15 | P | yes | yes |
| τ₁₆ | 16 | S | yes | yes |
| τ₁₇ | 17 | Cl | yes | yes |
| τ₃₅ | 35 | Br | **not listed** in arXiv Table 1 | **yes** (`tau_35`) |
| τ₅₃ | 53 | I | **yes** (0.891 kJ mol⁻¹ Å⁻²) | **missing** |
| τ₅ | 5 | **B** | **no** | **no** |

The plotting colormap in `cosmo_visualizations.py` also names B and Si. That is not a parameter.

**What τ is for.** Equation 1 in the 24a paper uses τ_α only in **ΔG_solv** (cavity / van der Waals). DISSOLVE’s overlay is `ln γ^∞` at 298.15 K, `refst=pure_component`. The COSMOSPACE `tau` in `cosmors.py` is `exp(-A_int/RT)`, not these element τ. So **missing τ_5 does not crash a logD job**; it means boron was never given a fitted vdW term, and boron chemistry was not in the published 24a parameter table. Silicon *was* given τ₁₄, and the 24a molar-volume QSPR even has an explicit `n_Si,atoms` term — Si compounds were in that training chemistry.

**What we do anyway.** The audit asked to treat Si/B as a flagged tier before spending DFT. Even with τ₁₄ present, silanes/siloxanes are a thin slice of 24a and a large slice of PlastChem (report: 2329 polymers include silicones). Hold them out of the first-pass queue.

- Flagged Si/B: `plastchem_organics_orca_opencosmo_flagged_si_b.csv` (column `flag` = `silicon` / `boron` / `silicon+boron`). **239 rows / 238 unique.** Do not DFT these until an owner decision.

The 12 `non_cosmo_element` drops in the funnel were As and similar. Selenium: allowed in the census keep-set, **zero** rows in the MW≤500 CSV.

---

## 5b. CHNO-only first screen (owner restriction)

Do not spend the first remote queue on every 24a-named element. Prove the ORCA → `.orcacosmo` → openCOSMO `ln γ^∞` path on the chemistry DISSOLVE has already run (phthalates: C, H, O) plus other CHNO organics. Then expand.

Allowed atoms: **H, C, N, O only** (implicit H counts). Hydrocarbons with no N or O stay in; they do not have to contain all four.

Held for a later expansion (from the Si/B-free 7,826): **1,762 rows / 1,721 unique** with at least one of F, P, S, Cl, Br, I. Row-presence (a molecule can count in more than one): Cl 671, S 536, F 277, Br 222, P 194, I 22. File: `plastchem_organics_orca_opencosmo_flagged_heteroatoms.csv` (column `extra_elements`).

Suggested expansion order after CHNO proves out:

1. Cl, then Br (common plasticizers / residuals; 24a has τ₁₇; package also has τ₃₅)
2. S (thiols, sulfides; τ₁₆)
3. F / PFAS (277 in this cut; overlay already exists for the Zhou 26)
4. P (organophosphates / flame retardants; τ₁₅)
5. I (22 rows; paper τ₅₃, missing in the installed 24a class — only ΔG_solv)
6. Si, then B (already flagged)

---

## 6. First-pass CSV (CHNO submission list)

Path: `plastchem_organics_orca_opencosmo_firstpass.csv`  
sha256 `a8e1ae169bbf8a5831a24bd8a83ba4a0e36cf69f3ce59e774fc4bd21aff92828`

Columns (name and SMILES first, then join keys):

```
name,smiles,plastchem_id,cas,inchikey,molecular_weight_g_mol
```

- `name` = PubChem name, else IUPAC, else CAS. No blanks.
- `smiles` = isomeric if present, else canonical (PubChem).
- Sorted by name.
- Filters: carbon-containing, not inorganic flag, not organometallic flag, not UVCB/polymer/mixture, parseable single-fragment neutral singlet, no metal atoms, **MW ≤ 500 g/mol**, **elements ⊆ {H, C, N, O}**.

Not in this CSV (on purpose): polymers, UVCBs, mixtures, inorganics, metal salts, organometallics, ions, radicals, MW > 500, F, P, S, Cl, Br, I, Si, B.

Expansion files (do not submit yet):

- `plastchem_organics_orca_opencosmo_flagged_heteroatoms.csv` — F/P/S/Cl/Br/I
- `plastchem_organics_orca_opencosmo_flagged_si_b.csv` — Si/B
- `plastchem_organics_orca_opencosmo_firstpass_no_sib.csv` — CHNO + heteroatoms, Si/B already out
- `plastchem_organics_orca_opencosmo_firstpass_audited_8065.csv` — pre-split census the audit reproduced

If you want a second file with no MW cut, that is the 8,961-row set above. Do not submit the Nature 11,500 without the metal/salt/charge filters — ORCA will die or the 24a parameters will be meaningless.

---

## 7. How long would this take (not local)

### Measured wall times on the actual DISSOLVE recipe

Serial ORCA, no `%pal`, same input as `run_orca_stage.py`. From `~/cosmo-artifacts/`:

| Molecule | atoms | basis fn | opt | COSMORS | **opt+SP** |
|---|---:|---:|---|---|---|
| water | 3 | 36 | 2 s | 2 s | ~4 s |
| methanol | 6 | 72 | 5 s | 4 s | ~9 s |
| DCM | 5 | 96 | 8 s | 5 s | ~13 s |
| hexane | 20 | 228 | 24 s | 31 s | ~1 min |
| cyclohexanol | 19 | 240 | 1.0 min | 39 s | ~1.7 min |
| **DEP** | 30 | 468 | 5.0 min | 3.0 min | **~8 min** |
| PE oligomer | 38 | 444 | 2.8 min | 1.9 min | ~4.7 min |
| DBP | 42 | 612 | 17.8 min | 5.8 min | **~24 min** |
| PVC 24a configs | 44 | 660 | 6.7–8.9 min | ~3.7 min | ~11 min |
| **BBP** | 43 | 672 | 40.4 min | 6.3 min | **~47 min** |
| **DEHP** | 66 | 900 | 55.5 min | 13.4 min | **~69 min** |

BBP vs DBP is the warning: similar size, 2× wall because SCF/geometry can stall. Alkanes are faster than phthalates. The typical band will be exceeded by some fraction of aromatic esters. The ×1.7 conservative band and the 50–100 molecule pilot are the response. Anchors were measured on this workstation; cluster wall clock scales with its single-core speed, which the pilot calibrates.

Per-molecule estimate used for the campaign: `t_min ≈ 0.0035 × n_atoms^2.4` (fits DEP/DEHP). Conservative band = **×1.7** (reproduces BBP). Unique InChIKeys only. openCOSMO after DFT ignored (seconds). Conformers = 1.

### Submission list (MW ≤ 500, CHNO only, 5,833 unique structures)

| Parallelism | Typical | Conservative (×1.7) |
|---|---|---|
| Serial CPU-hours (1 core/job) | **2,628 h** | **4,467 h** |
| 4-way (local `MAX_CONCURRENT_DFT`) | **27 days** | **47 days** |
| 32-way (~64–80 GB RAM node) | **3.4 days** | **5.8 days** |
| 128-way (small cluster, ~256–320 GB) | **0.9 day** | **1.5 days** |

Si/B-held-out (7,554 unique) was 3,042 / 5,172 CPU-hours. CHNO drops another ~414 typical hours by parking 1,721 unique heteroatom structures. With ~20% for embed/SCF/queue: 32-way is **about five days**, 128-way **about a day and a half**.

The phthalate anchors (DEP/DBP/BBP/DEHP) are all CHNO, so this cut is the one the timing model was actually measured on.

RAM: ~1.5 GB `%maxcore` + ORCA overhead → budget **2–2.5 GB per job**. 32 jobs ≈ 80 GB. Cores: 1 per job. Do not put `%pal` on unless the box has the OpenMPI ORCA was built against (`run_orca_stage.py` documents that failure mode).

### If you drop the MW cut (all 8,661 unique eligible)

Typical 6,432 serial hours — **8.4 days** on 32-way, **2.1 days** on 128-way. The extra ~900 molecules are the expensive tail (p99 MW ~907). MW 500 is the right first pass.

### What would *not* be 8,000 jobs

- **Nature 11,500 organics as a dump:** several thousand would be metals/salts/UVCBs. Do not submit that list.
- **Conformer ensembles:** default is 1. If you DFT 10–20 conformers per molecule, multiply the table by that. The phthalate ladder already showed ensembles largely cancel in ΔlogD; do not pay that until a pilot says otherwise.
- **Every solvent DFT:** unnecessary. One `.orcacosmo` per solute; held solvents cover the five-solvent ΔlogD overlay. New solvents only need DFT if they are not already held.

Pilot recommendation before the full 5,833: 50–100 CHNO molecules spanning 15–80 atoms (include a BBP-like aromatic ester). That calibrates the power-law on *their* cluster, not this workstation.

---

## 8. GitHub PlastChem/DB

https://github.com/PlastChem/DB is the **assembly code**, not the curated table. README: beta from seven datasets → manual curation → v1.00 with the 2024-03-14 report launch (xlsx README says 14.03.2024). License file is in the repo. Two confidential sources have scripts withheld. Use Zenodo / this xlsx as the chemical list; use GitHub only if you need to regenerate provenance of the seven-source merge.

---

## 9. SI spreadsheets in one line each

- **MOESM7:** which of the 7 sources each of 17,932 IDs came from.
- **MOESM8:** 11,331 OPERA structures (Nature: 12,114 unique canonical SMILES → 783 discarded by OPERA → 11,331). Colour is the hazard category in Extended Data Fig. 3, not a chemistry class.
- **MOESM10:** polymer-type matrix (HDPE, LDPE, PE, PS, EPS, HIPS, PP, PVC, PET, PA, PUR, Rubber, ABS, PC, EVA, PMMA, …) plus summaries. Useful later if you want “organics used in PE/PP/PET only”.
- **MOESM11:** function groups. Colorants 3,676; other processing aids 3,031; fillers 1,836; intermediates 1,741; lubricants 1,687; biocides 1,252; plasticizers 883; solvents 83 (82% of concern). NIAS in this *function* table is only 47 — not the 1,788 NIAS in the Nature function pie (different cut).

---

## 10. What this is not

- Not a validated log *D* / partition table. OPERA log *K*OW in MOESM8 is QSAR, not openCOSMO.
- Not the Zhou 34. Overlap exists (DEP/DBP/DEHP are in both) and is not the point of this CSV.
- Not an instruction to start ORCA on this workstation. The ask was remote.

---

## 11. Owner decisions still open

1. Keep MW ≤ 500, or also emit the 8,961-row no-cut file?
2. Deduplicate on InChIKey before submit (recommended: **5,833** CHNO jobs)?
3. Drop very small organics (methane, ethylene, formaldehyde) or keep them? They are in the CSV; DFT is cheap.
4. Organic *salts*: currently dropped. Could neutralize the organic fragment and DFT the anion/acid. That is extra chemistry, not first pass.
5. Cluster target (32-way node vs 128-way) so the wall-clock row can be turned into a queue script.
6. Expansion after CHNO proves out: Cl/Br → S → F/PFAS → P → I → Si → B. Files already split.
