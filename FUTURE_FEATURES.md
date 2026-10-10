# Future features

A running list of work we have agreed to come back to. Each entry says what is missing or wrong, the evidence,
and the proposed fix, so it can be picked up without the conversation that found it. Add new entries under the
right heading. When an entry lands, delete it and name the commit in the commit message.

Current priority (2026-09-25): validating solubility queries, safety queries and safety-based reranking, and
rebuilding the PubChem safety snapshot the validation found broken. The entries below wait until that is done.

## Contaminants

### Define the contaminant families by structure (added 2026-09-25)

**Problem.** Nine of the eleven families are PlastChem's own group columns, and those are narrower than the plain
names suggest. A user who asks for "slip agents" gets 8 compounds and is told that is all of them.

**Evidence.** The families were checked against the served release, which holds 5,830 computed contaminants:

| Family | In the family | Missing from it, although computed |
|---|---|---|
| Slip agents | 8 (group `aliphatic_primary_amides`, at least 12 carbons) | More plain fatty amides: pentadecanamide, hexadecenamide, linoleamide, cis-11-eicosenamide, tetracosanamide and 12-hydroxystearamide. Also 9-octadecenamide and 13-docosenamide, which are the oleamide and erucamide entries with the double bond unspecified or trans. Two-chain and bis-amides: N-erucylstearamide, N-octadec-9-enylhexadecanamide, N,N'-ethylenebismyristamide and N,N'-hexane-1,6-diyldistearamide |
| Phthalates | 92 since 15c5155a (2026-10-07: PlastChem's group plus every ortho-phthalate diester; 89 computed) | Still missing: the computed monoesters MEHP, monoethyl phthalate and monobenzyl phthalate |
| Bisphenols | 25 | Bisphenol E (1,1-bis(4-hydroxyphenyl)ethane) and bisphenol C (2,2-bis(4-hydroxy-3-methylphenyl)propane) |
| Parabens | 7 | Octylparaben (octyl 4-hydroxybenzoate) |
| Aromatic amines | 17 | PlastChem's group is REACH Annex XVII's list of 22 restricted carcinogenic amines (17 computed, 5 outside the release). Aniline and p-toluidine are not members |

- Antioxidants and UV stabilizers are defined by structure (SMARTS patterns in `plastchem_release._SMARTS`) and do not have this problem.
- Some of the best-known slip agents are computed since promotion-v2 (the 500–700 g/mol tier, amendment A-11) but
  are not members, because PlastChem's group holds primary amides only:
  - EBS, ethylene bis-stearamide (110-30-5);
  - stearyl erucamide (10094-45-8);
  - ethylene bis-oleamide (110-31-6).

**Proposed fix (no new calculations).**
- Membership becomes the PlastChem group plus a structural pattern for the family. For slip agents, that means every fatty amide: plain, two-chain and bis-amides. Surfactant ethanolamides, betaines and sarcosines are excluded.
- Rebuild `data/plastchem_families.json` with `python -m dissolve.plastchem_release --families <workbook>`. The workbook is `plastchem_db_v1.0.xlsx` (sha256 df4ccad3…), which is not in the repo. Members that were not computed go to `outside_release`, so answers name them.
- Owner decision: should "aromatic amines" mean all primary aromatic amines (recommended), or stay the REACH list under a clearer name?
- Tests: one counterfactual per family, such as bisphenol E resolving under "bisphenols". Live check: a question about slip agents names EBS as not computed.

### Solid contaminants: miscibility is overstated below the melting point (added 2026-09-24, seen again 2026-09-25)

**Problem.** The openCOSMO miscibility check treats every contaminant as a liquid; there is no fusion term. For a
contaminant that is solid at the stated temperature, "miscible at 15 wt%" describes the melted compound, and the
real solid dissolves less. The log P values are not affected, because partitioning compares two dissolved states.

**Evidence.**
- All 8 slip agents are reported miscible with o-xylene at 15 wt% at 25 °C, but they are solids there. PubChem melting points: erucamide 75–80 °C, oleamide 76 °C, hexadecanamide 107 °C, octadecanamide 107–109 °C, docosanamide 110–113 °C.
- At 120 °C they are all melted, so that result stands.
- Earlier cases: BPA, BHT, dyes such as 1,4-diaminoanthraquinone (about 265 °C), and whole-set answers led by high-melting solids such as citric acid and guanine.

**Proposed fix.**
- Quick: the tool and the answer rules say the check is liquid–liquid, and answers warn when the contaminant may be solid at the stated temperature.
- Proper: pull PubChem experimental melting points for the 5,830 contaminants. Mark each miscibility result below the melting point as an upper bound, or correct it with the ideal-solubility equation. The correction needs the enthalpy of fusion, which PubChem rarely has.
- Owner decision pending between the two.

### Promote the 39-solvent extension (ext39)

- Promote `promotion-ext39-v1` when the lane delivers it, with `promote_opencosmo_release(base, extensions=[ext39])`.
- The served concentration basis needs a vetted liquid density for each new solvent. Until then those values stay empty rather than guessed.
- Rebuild the families file after any re-promotion.
- This adds mixed xylenes, p-xylene and 37 more solvents. Today "xylene" falls back to o-xylene, the only xylene in the 32-solvent panel.

### Heavier and non-CHNO contaminants (updated 2026-10-07)

- **Done in promotion-v2 (branch `contaminant-always-on`):** the 500–700 g/mol CHNO tier and the halogen tier
  (F, Cl, Br, I up to 700 g/mol), 7,160 computed in all.
- **Left from that release:**
  - 117 structures whose surfaces converged after the freeze and wait for thermodynamics;
  - 9 still in ORCA at the freeze;
  - 58 failed, among them 3 with no ETKDG geometry and 10 azo pigments rejected by the identity check.
- **Still outside the campaign:**
  - structures with S, P, Si or B;
  - anything above 700 g/mol;
  - ions and salts.
  For the paper's publication sets, amendment A-12 computed the PFAS acids the release lacks (the sulfonic acids
  among them), DECA and TBBPA-dbP, with partition and miscibility rows, outside the release
  (`campaigns/plastchem-hpc/publication-sets/`). PFAS questions still go to the workbook screen.

### Charge state: strong acids, zwitterions and strong bases are modelled neutral (added 2026-10-07)

**Problem.** Every release compound is one neutral molecule, so acids keep their proton. Zhou et al. (Green Chem.
2026) did the same for their PFAS: across 32 solvents the paper's values follow our neutral acids (r = 0.95–0.97),
not the anions. Some release compounds, though, would be charged wherever ionization happens, as in aqueous washes.

**Evidence.** The charge audit (`campaigns/plastchem-hpc/reports/charge-audit-2026-10-07/`: script, full table and
write-up) flags 420 of the 7,160 computed compounds:
- 40 fluorinated carboxylic acids with fluorine on the alpha carbon, pKa about 0–1. 10 are the paper's PFAS; the
  other 30 are trifluoroacetic acid, 11H-perfluoroundecanoic acid and 28 branched PFOA isomers.
- 9 amino acids and aminopolycarboxylates (zwitterions or polyanions in water).
- 13 strong bases: guanidines such as DPG, amidines such as DBU, and imidazolines.
- 4 diacids with a low first pKa (oxalic, maleic and others).
- 3 polynitrophenols.
- 351 ordinary carboxylic acids.

DISSOLVE's data are organic solvent–polymer partitioning, where neutral is a defensible default, and the
fluorinated acids are the clearest exception: strong acids that are anions in water and in protic solvents.

**Proposed fix.**
- Anion surfaces for the 40 fluorinated acids, beside the neutral ones, with the A-12 machinery. Amendment A-12
  already computed 24 PFAS anions, with partition rows, before the paper's species was settled; they are kept on
  Euler and R:, not in the repo.
- A species convention per medium, decided with the collaborator: neutral for solvent–polymer data, and ionized or
  pH-specific log D for aqueous washes.
- Zwitterion tautomers for the amino acids if aqueous screens come.
- Answers flag compounds that would be ionized in water.
- Owner decision pending on all four.

## Solubility and separation

### Glassy polymers look too insoluble near room temperature (grid investigation, 2026-09-25)

**Owner decision, 2026-09-25:** keep the grid as it is for now. The investigation below is kept here in full.

**Problem.** Near room temperature the COSMO-RS grid makes glassy (amorphous) polymers look far less soluble than they are:
- Polystyrene in toluene is 3.64 wt% at 25 °C, chloroform 2.27 and dichloromethane 4.62. All three are textbook polystyrene solvents, but none reaches the 5 wt% level the screens use. "Which solvents dissolve polystyrene at room temperature?" therefore leaves them out.
- Polycarbonate has no solvent at 5 wt% or more among the 69 common solvents at 25 °C: the best is DMF at 4.48, then THF 3.91, dichloromethane 3.19 and chloroform 1.01. Yet THF and chloroform are its standard solvents. The answer is "no solvent dissolves polycarbonate at room temperature".

**Cause.** The grid is a crystal-melting calculation, and glassy polymers have no crystals to melt.
- The group's method paper is in DISSOLVE's literature corpus (document `D9adf3fb563593960`). It computes each polymer's solubility with COSMOtherm 19 (BP_TZVP_19) as a solid–liquid equilibrium.
- In the paper's words, "this calculation requires the polymer melting temperature and an experimentally measured solubility as reference input". The fusion free energy is fixed from that one reference point (the equation is in document `De87b2e2cef20b2c0`), and every other value, including every 25 °C value, is extrapolated from it.
- The HDPE anchor is in document `D1af857ee2e8299d6`; the LDPE and EVOH melting temperatures (113 °C and 177.47 °C) are in `D74ee0776b61134af`.
- The grid reproduces every anchor that can be checked exactly:

| Polymer | Anchor solvent | T (°C) | Anchor (wt%) | Grid (wt%) |
|---|---|---|---|---|
| EVOH | DMSO | 95 | 19.4 | 19.4 |
| LDPE (PE model) | toluene | 110 | 23.1 | 23.1 |
| HDPE (PE model) | dodecane | 120 | 16.2 | 16.2 |
| PP | toluene | 110 | 31.2 | 31.2 |
| PS | toluene | 110 | 41.2 | 41.2 |
| PET | DMSO | 135 | 13.3 | 13.3 |
| PVC | THF | 50 | 14.9 | 14.9 |
| Nylon 66 | DMSO | 135 | 3.1 | 3.1 |
| Nylon 6 | acetic acid | 90 | 10.8 | not in the grid |

- A melting term suits semicrystalline polymers. For glassy polymers it pulls room-temperature values down along a steep curve, much as for polypropylene, which really does melt:

| Pair | 25 °C | 50 °C | 110 °C | 110/25 ratio | Apparent ΔH, 25→50 °C (kJ/mol) |
|---|---|---|---|---|---|
| PS / toluene | 3.64 | 10.2 | 41.2 | 11.3 | 33.1 |
| PS / THF | 14.0 | 24.6 | 53.7 | 3.85 | 18.2 |
| PS / chloroform | 2.27 | 6.77 | 33.7 | 14.9 | 35.0 |
| PS / dichloromethane | 4.62 | 12.6 | 45.3 | 9.79 | 32.1 |
| PC / dichloromethane | 3.19 | 9.02 | 33.4 | 10.5 | 33.3 |
| PC / THF | 3.91 | 10.8 | 37.4 | 9.56 | 32.6 |
| PC / chloroform | 1.01 | 3.03 | 18.7 | 18.5 | 35.2 |
| PVC / THF | 8.90 | 14.9 | 36.5 | 4.10 | 16.5 |
| PVC / cyclohexanone | 5.18 | 9.61 | 28.2 | 5.43 | 19.8 |
| PP / toluene (semicrystalline) | 1.06 | 3.47 | 31.2 | 29.5 | 38.1 |
| LDPE / toluene (semicrystalline) | 0.054 | 0.432 | 23.1 | 428 | 66.6 |

- PVC suffers least because its anchor is at 50 °C, so less is extrapolated. Polycarbonate's anchor is not documented anywhere reachable.

**Measured values against the grid** (the paper's Table 3, and the STRAP paper, Walker et al., *Sci. Adv.* 2020, Table 1):

| Polymer | Solvent | T (°C) | Measured (wt%) | Grid (wt%) |
|---|---|---|---|---|
| PS | THF | 25 | 24.4 | 14.0 |
| LDPE | dodecane | 120 | 30.1 | 32.5 |
| EVOH | DMF | 120 | 27.3 | 30.8 |
| PET | DMF | 120 | 0.5 | 18.5 (the paper's own miss) |
| LDPE | DMSO | 95 | 0.04 | 1.25 |
| EVOH | DMSO | 95 | 14.05 | 19.4 |
| PET | DMSO | 95 | 0.02 | 3.14 |
| EVOH | toluene | 110 | 0.00 | 0.274 |
| PET | toluene | 110 | 0.00 | 2.65 |

At the 5 wt% level, the dissolves / doesn't call is right for all six STRAP points. The low values run high.

**Hansen compared with COSMO-RS at 25 °C.** Hansen parameters are room-temperature values, so the comparison is made only there. It uses the 69 common solvents through the product's Hansen screen, which resolves 30 of them:

| Polymer | Hansen inside | COSMO-RS ≥ 5 wt% | Agree at 5 wt% | Rank correlation (RED, log wt%) |
|---|---|---|---|---|
| PS | 9 | 8 | 57% | −0.56 |
| PC | 7 | 0 | 77% | −0.74 |
| PVC | 15 | 8 | 63% | −0.64 |
| PET | 6 | 0 | 80% | −0.68 |
| HDPE | 5 | 0 | 83% | −0.78 |
| LDPE | 21 | 0 | 30% | −0.69 |
| PP | 10 | 0 | 67% | −0.83 |
| EVOH | 1 | 1 | 93% | −0.63 |
| Nylon 6 | 3 | 0 | 88% | −0.41 |
| Nylon 66 | 4 | 0 | 87% | −0.65 |

The two methods rank solvents alike but disagree on the level, and almost always the same way: Hansen says a solvent dissolves the polymer, COSMO-RS says it doesn't at 25 °C.

**Which method the references support.** This was checked against room-temperature statements, dissolves or doesn't, from fetched references: Wypych's *Handbook of Polymers* via Wikipedia for PS; Tsuji, Norisuye and Fujita, *Polymer Journal* 1975, for PC; Wikipedia for PVC, PE and PP.

| Class | Statements | Hansen right | COSMO-RS right (≥ 5 wt%) | Where they disagree, Hansen right | Class rule right |
|---|---|---|---|---|---|
| Glassy (PS, PC, PVC) | 18 | 14 of 18 | 12 of 18 | 6 of 10 | 18 of 18 |
| Semicrystalline (HDPE, LDPE, PP) | 23 | 7 of 22 | 23 of 23 | 0 of 15 | 23 of 23 |

- **Semicrystalline polymers:** COSMO-RS is right. Hansen ignores crystallinity, so it puts toluene and the xylenes inside the polyethylene sphere at room temperature.
- **Glassy polymers:** COSMO-RS misses every chlorinated solvent (chloroform and dichloromethane for PS, PC and PVC), plus THF and chloroform for PC. The cause of the chlorinated-solvent pattern is not established. Hansen's own misses are THF, MEK and ethyl acetate for PS, because its PS sphere is small (radius 5.3 MPa^½).
- **The class rule:** near room temperature, a glassy polymer's solvent counts if either method says it dissolves; a semicrystalline polymer's counts only if COSMO-RS says so.
- **Caveat:** 15 of the 18 glassy statements are "dissolves", so the rule's risk of false positives was tested only against water and ethanol. Hansen, for example, puts ethyl acetate inside the PC sphere.

**Options** (costs estimated 2026-09-25):
1. **Flag it in answers** (hours): for a glassy polymer below its anchor temperature, the value is a lower bound, and the Hansen check is shown beside it.
2. **The class rule near room temperature** (about a day): apply it at or below about 40 °C. It needs a few non-solvent references for PS and PC before it is served.
3. **Recompute PS, PC and PVC without the melting step** (2–3 days): treat them as liquids with DISSOLVE's openCOSMO-RS setup; their oligomer surfaces already exist from the contaminant campaign. It replaces pinned grid data and would mix two COSMO-RS parameterizations in one grid.
4. **Re-anchor PS at room temperature:** this needs COSMOtherm and the original pipeline.
5. **Use a larger PS Hansen sphere** from the handbook.

**Also found:**
- **Nylon 6:** the grid covers only 32 solvents, and none of the solvents known to dissolve it: formic acid, m-cresol, HFIP, or even its own anchor solvent, acetic acid. Answers say "no solvent dissolves nylon 6" without saying how thin the data are.
- **PET:** the paper's one outright miss is PET in DMF at 120 °C (18.5 wt% predicted, 0.5 measured).

**Pending:**
- The melting temperature used for each polymer is in the method paper's supplementary information, which is not in the corpus. Atactic PS has no true melting point.
- Polycarbonate's anchor (solvent, temperature, value).
- Measured values for PS in toluene and PC in dichloromethane at room temperature. The references are qualitative.
- Non-solvent references for the glassy polymers beyond water and ethanol.

**How the numbers were made:**
- Grid values are `thermodynamics.duckdb` `solubility_grid` rows with `is_valid`.
- Hansen values are the product's `screen_hansen_compatibility` on the 69 common solvents, plus `hansen.duckdb` `red_grid` through `polymer_identity_map` and `solvent_identity_map` for the all-linked view.
- The apparent ΔH is from the 25 and 50 °C values: −R·ln(ratio)/(1/T₂ − 1/T₁).
- Reference statements were fetched on 2026-09-25, each with its URL and checksum.

### Polyolefin–glycol values in the COSMO-RS grid (added 2026-09-24)

**Problem.** The grid says HDPE and LDPE dissolve at 100 wt% in propylene glycol at 160 °C, and LDPE at about
7 wt% in diethylene glycol. These values are not flagged as capped.
- The separation planner leads two served examples with "dissolve the polyolefin in propylene glycol at 160 °C" (HDPE/PP/PS and PE/PS/PET).
- The Hansen check strongly disagrees: a distance ratio (RED) of 11.7 for HDPE in propylene glycol, where below 1 means likely to dissolve. Practical experience agrees with Hansen.
- The answers state the disagreement, but still lead with the route.

- The validation on 2026-09-25 found the same pattern more broadly.
  - HDPE in diethylene, triethylene and propylene glycol comes out near 100 wt% at 140–160 °C.
  - The polypropylene (130 °C) and LDPE (110 °C) solvent lists include ketones and alcohols, although only non-polar solvents dissolve polyolefins in practice.
  - Two answers led with the propylene glycol route.

**Owner decision, 2026-09-25: no Hansen filter for now.** Hansen parameters are room-temperature values, and these steps run at 140–160 °C, so a Hansen mismatch there is not a real contradiction.

**Still possible later:** a review of the grid's polyolefin pairs with polar solvents. The crystal-melting calculation behind the grid, described in the glassy-polymer entry above, is the place to start.

### Link more solvents to their Hansen parameters

- `hansen.duckdb` has about 1,180 solvents with Hansen data, but only 380 are linked to the grid's solvent names (`solvent_identity_map`).
- Many answers therefore say "no Hansen record" for a solvent that has one.
- Fix: link the rest through CAS numbers and PubChem CIDs.

### Model scope worth revisiting

- **Crystallinity:** partition log P treats every polymer as an amorphous liquid. Crystallinity would shift the real solvent-over-polymer ratio toward the solvent for PE, PP, PET and the nylons.
- **Neutral species only:** log P covers only neutral molecules, with no pH or ionization. Acids and amines in water are the cases this affects.

## Agent

### Cut the per-query token use

- The Muse window was raised to 256k (`session._FAMILY_WINDOWS`) as a stopgap, with the owner's note: "once agent is finalized we'll want to clear this up". The OpenRouter Gemini default has the same 256k window.
- The agent still sometimes reads more result pages than it needs. One CLI example made 3 tool calls where 1 would do.

## Web app and hosting

### Accounts

- **Password reset:** there is no email, so there is no self-service reset. Today the database owner sets `users.password_hash` with `web_accounts.hash_password`. An admin-only reset command would make this safer.
- **Access code:** sign-up asks for an access code, which is the old site password, so strangers cannot spend model credits. Owner decision: keep it or drop it.

### Host the full app

- The site runs the lite profile, with literature and TEA off.
- The full app peaks at about 2.7 GB, so it needs a $24 or $48 droplet instead of the current 1 GB App Platform instance.
- Deferred by the owner: "dont want to get derailed".

## Data publication

### Polymer starting geometries

- The polymer `.mcos`/`.cosmo` starting geometries were derived from licensed COSMObase files, so the GitHub archive withholds them. The archive's `opt.inp` files are withheld too, and each `result.json` is replaced by `result.redacted.json`.
- Owner decision: whether they may be published.
