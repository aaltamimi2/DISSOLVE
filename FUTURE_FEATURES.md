# Future features

A running list of work we have agreed to come back to. Each entry says what is missing or wrong, the evidence,
and the proposed fix, so it can be picked up without the conversation that found it. Add new entries under the
right heading. When an entry lands, delete it and name the commit in the commit message.

Current priority (2026-09-25): validating solubility queries, safety queries and safety-based reranking. The
entries below wait until that is done.

## Contaminants

### Define the contaminant families by structure (added 2026-09-25)

**Problem.** Nine of the eleven families are PlastChem's own group columns, and those are narrower than the plain
names suggest. A user who asks for "slip agents" gets 8 compounds and is told that is all of them.

**Evidence.** The families were checked against the served release, which holds 5,830 computed contaminants:

| Family | In the family | Missing from it, although computed |
|---|---|---|
| Slip agents | 8 (group `aliphatic_primary_amides`, at least 12 carbons) | More plain fatty amides: pentadecanamide, hexadecenamide, linoleamide, cis-11-eicosenamide, tetracosanamide and 12-hydroxystearamide. Also 9-octadecenamide and 13-docosenamide, which are the oleamide and erucamide entries with the double bond unspecified or trans. Two-chain and bis-amides: N-erucylstearamide, N-octadec-9-enylhexadecanamide, N,N'-ethylenebismyristamide and N,N'-hexane-1,6-diyldistearamide |
| Phthalates | 42 | The release has 89 computed ortho-phthalate esters. Missing include MEHP, monoethyl phthalate, monobenzyl phthalate and di-sec-butyl phthalate |
| Bisphenols | 25 | Bisphenol E (1,1-bis(4-hydroxyphenyl)ethane) and bisphenol C (2,2-bis(4-hydroxy-3-methylphenyl)propane) |
| Parabens | 7 | Octylparaben (octyl 4-hydroxybenzoate) |
| Aromatic amines | 17 | PlastChem's group is REACH Annex XVII's list of 22 restricted carcinogenic amines (17 computed, 5 outside the release). Aniline and p-toluidine are not members |

- Antioxidants and UV stabilizers are defined by structure (SMARTS patterns in `plastchem_release._SMARTS`) and do not have this problem.
- Some of the best-known slip agents are in PlastChem but were never computed, because they lie outside the campaign's calculation set (about 590 g/mol):
  - EBS, ethylene bis-stearamide (110-30-5);
  - stearyl erucamide (10094-45-8);
  - ethylene bis-oleamide (110-31-6).
- A family answer should name these as not computed. Today it cannot, because the family does not list them.

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

### Heavier and non-CHNO contaminants

- **Heavier structures:** 236 structures of 500–700 g/mol have not been run, projected at about 1,800 CPU-h and on hold. This set includes the EBS-class slip agents.
- **Other elements:** about 1,960 PlastChem structures contain S, P, halogens, Si or B, including all PFAS, and are outside the openCOSMO campaign. PFAS questions go to the workbook screen.

## Solubility and separation

### Polyolefin–glycol values in the COSMO-RS grid (added 2026-09-24)

**Problem.** The grid says HDPE and LDPE dissolve at 100 wt% in propylene glycol at 160 °C, and LDPE at about
7 wt% in diethylene glycol. These values are not flagged as capped.
- The separation planner leads two served examples with "dissolve the polyolefin in propylene glycol at 160 °C" (HDPE/PP/PS and PE/PS/PET).
- The Hansen check strongly disagrees: a distance ratio (RED) of 11.7 for HDPE in propylene glycol, where below 1 means likely to dissolve. Practical experience agrees with Hansen.
- The answers state the disagreement, but still lead with the route.

**Proposed fix.**
- A Hansen sanity filter: do not use a solvent for a step when the Hansen check puts it far outside the polymer's sphere (for example RED above 3).
- A review of the grid's polyolefin–glycol pairs.

This falls within the current solubility validation, so it may be settled there.

### Link more solvents to their Hansen parameters

- `hansen.duckdb` has about 1,180 solvents with Hansen data, but only 380 are linked to the grid's solvent names (`solvent_identity_map`).
- Many answers therefore say "no Hansen record" for a solvent that has one.
- Fix: link the rest through CAS numbers and PubChem CIDs.

### Model scope worth revisiting

- **Crystallinity:** partition log P treats every polymer as an amorphous liquid. Crystallinity would shift the real solvent-over-polymer ratio toward the solvent for PE, PP, PET and the nylons.
- **Neutral species only:** log P covers only neutral molecules, with no pH or ionization. Acids and amines in water are the cases this affects.

## Agent

### Cut the per-query token use

- The Muse window was raised to 256k (`session._FAMILY_WINDOWS`) as a stopgap, with the owner's note: "once agent is finalized we'll want to clear this up".
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
