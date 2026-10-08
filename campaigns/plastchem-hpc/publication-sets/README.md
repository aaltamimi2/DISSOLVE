# Publication sets of Zhou et al., Green Chem. 2026

The ORCA/openCOSMO surfaces (`.orcacosmo`) of every compound of Zhou et al., *Solvent-Mediated Contaminant Removal
from Plastic Waste Using Thermodynamic Modeling*, Green Chem. 2026 (d5gc06059a), one per compound, as plain files for
validation against the paper, with their openCOSMO-RS partition and miscibility results. The same surfaces appear in
the campaign archives (`../opencosmo-outputs/` and `../orca-calculation-files/`, folder
`contaminants/publication-sets/`) when those are rebuilt.

- `PFAS/`: 20 of the paper's 26 PFAS (ESI Table S7).
- `BFR/`: 4 of its 4 brominated flame retardants (Case Study 1).
- `Phthalates/`: 8 of its 8 phthalates (ESI Table S5).
- `<set>/orca-calculation-files/<name>/`: each surface's ORCA inputs, optimised geometry, run record and COSMO-step
  log; for the runs of amendment A-12 also `identity.json`, the identity check.
- `PUBLICATION_SETS.tsv`: every compound with where it was computed, its file and its SHA-256.
- `THERMODYNAMICS_partition.tsv` and `THERMODYNAMICS_miscibility.tsv`: see below.

## Species

Every compound is neutral, as the paper modelled them. For the PFAS this was checked against the paper's own values
(d5gc06059a2_suppl.xlsx, sheet PFAS_log_D, 32 solvents): for the ten PFAS acids in both forms, the paper's values
follow the neutral acids (r = 0.95-0.97, RMSE 0.6-0.9 log units) and not the anions (|r| below 0.1). Salts are their
parent acids: the paper's values for NH4TetraFPt equal TFPA's, so `PFAS/` holds TFPA's surface under both labels,
and its two ADONA salts (NaDoDFNt, NH4PFNt) are one acid. A file named for a salt or an anion is named as the acid
computed, with the paper's label, e.g. `Perfluorononanesulfonic_acid_(PFNS).orcacosmo`.

Tri-PBDE is the ESI's 2,4,4'-tribromodiphenyl ether (BDE-28). The paper reports one HBCD value without naming the
stereoisomer; this set holds gamma-HBCD, the main component of technical HBCD.

## Thermodynamics

openCOSMO-RS 24a at 298.15 K, solute at infinite dilution, with the same 32 solvents, 10 polymer conformer ensembles
and workers as the PlastChem release: release compounds carry the release's own rows, and the A-12 structures were
computed the same way on Euler (batches p00, p01, p02). Partition rows are in for 23 of the 32
files here (waiting: PFBS, PFPS, PFHxS, PFHS, PFOS, KClHxDFS, PFNS, NaDoDFNt, NH4PFNt).

- `THERMODYNAMICS_partition.tsv`: log10 P(solvent/polymer) per file, polymer (EVOH, nylon 6, nylon 6,6, PC, PE,
  PET, PP, PS, PVC, PVDF), solvent and convention; positive favours the solvent. `logP_concentration` is on the
  mol/L basis, `logP_x` on the mole-fraction basis. `normalized` is the convention DISSOLVE serves; `existing`
  weights the polymer's conformers as the earlier route did. The paper used PVC for its phthalates (ESI Table S6)
  and PS, PET, PE and PP for its BFRs (Tables S1-S4).
- `THERMODYNAMICS_miscibility.tsv`: binary liquid-liquid equilibrium of each compound with each solvent at room
  temperature and at the solvent's high temperature: phase status, solubility in wt%, and whether it is miscible at
  15 wt%.

## How they were made

ORCA 6.1.1: RDKit ETKDGv3 (seed 12345, 300 conformers) and MMFF94 pick one conformer; a gas-phase `OPT BP86
def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`, whose solute step writes the surface (BP86/def2-TZVPD).
The phthalates, BDE-28, gamma-HBCD and ten of the PFAS acids are the PlastChem campaign's surfaces (release
promotion-v2). The other PFAS acids, DECA and TBBPA-dbP were computed for this set under amendment A-12 with the same
recipe (structures from PubChem by CAS, checked against PlastChem; PFTriDS, PFTetraDA, DECA and TBBPA-dbP are above
the campaign's 700 g/mol limit, and the sulfonic acids contain sulfur, which the campaign otherwise excludes).
Identity: the optimised geometry's connectivity must match the input.

## Compounds

| Label | Compound (as in the paper) | CAS | Computed in | File |
|---|---|---|---|---|
| PFBA | Perfluorobutanoic acid | 375-22-4 | release (promotion-v2) | `PFAS/Heptafluorobutyric_acid.orcacosmo` |
| PFPA | Perfluoropentanoic acid | 2706-90-3 | release (promotion-v2) | `PFAS/Perfluorovaleric_acid.orcacosmo` |
| PFHxA | Perfluorohexanoic acid | 307-24-4 | release (promotion-v2) | `PFAS/Perfluorohexanoic_acid.orcacosmo` |
| PFHA | Perfluoroheptanoic acid | 375-85-9 | release (promotion-v2) | `PFAS/Perfluoroheptanoic_acid.orcacosmo` |
| PFOA | Perfluorooctanoic acid | 335-67-1 | release (promotion-v2) | `PFAS/Perfluorooctanoic_acid.orcacosmo` |
| PFNA | Perfluorononanoic acid | 375-95-1 | release (promotion-v2) | `PFAS/Perfluorononanoic_acid.orcacosmo` |
| PFDA | Perfluorodecanoic acid | 335-76-2 | release (promotion-v2) | `PFAS/Perfluorodecanoic_acid.orcacosmo` |
| TFPA | 2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic acid | 13252-13-6 | release (promotion-v2) | `PFAS/2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic_acid.orcacosmo` |
| PFTriDA | Perfluorotridecanoic acid | 72629-94-8 | publication tier (A-12) | running on Euler |
| PFUnDA | Perfluoroundecanoic acid | 2058-94-8 | release (promotion-v2) | `PFAS/Perfluoroundecanoic_acid.orcacosmo` |
| PFDoDA | Perfluorododecanoic acid | 307-55-1 | release (promotion-v2) | `PFAS/Perfluorododecanoic_acid.orcacosmo` |
| PFTetraDA | Perfluorotetradecanoic acid | 376-06-7 | publication tier (A-12) | running on Euler |
| PFBS | Perfluorobutanesulfonic acid | 375-73-5 | publication tier (A-12) | `PFAS/Perfluorobutanesulfonic_acid.orcacosmo` |
| PFPS | Perfluoropentanesulfonic acid | 2706-91-4 | publication tier (A-12) | `PFAS/Perfluoropentanesulfonic_acid.orcacosmo` |
| PFHxS | Perfluorohexanesulfonic acid | 355-46-4 | publication tier (A-12) | `PFAS/Perfluorohexanesulfonic_acid.orcacosmo` |
| PFHS | Perfluoroheptanesulfonic acid | 375-92-8 | publication tier (A-12) | `PFAS/Perfluoroheptanesulfonic_acid.orcacosmo` |
| PFOS | Perfluorooctanesulfonic acid | 1763-23-1 | publication tier (A-12) | `PFAS/Perfluorooctanesulfonic_acid.orcacosmo` |
| PFUnDS | Perfluoroundecanesulfonic acid | 749786-16-1 | publication tier (A-12) | running on Euler |
| PFTriDS | Perfluorotridecanesulfonic acid | 791563-89-8 | publication tier (A-12) | running on Euler |
| PFDS | Perfluorodecanesulfonic acid | 335-77-3 | publication tier (A-12) | running on Euler |
| PFDoDS | Perfluorododecanesulfonic acid | 79780-39-5 | publication tier (A-12) | running on Euler |
| KClHxDFS | Potassium 9-chlorohexadecafluoro-3-oxanonane-1-sulfonate | 73606-19-6 | publication tier (A-12) | `PFAS/9-Chlorohexadecafluoro-3-oxanonane-1-sulfonic_acid_(KClHxDFS).orcacosmo` |
| PFNS | Perfluorononanesulfonate | 474511-07-4 | publication tier (A-12) | `PFAS/Perfluorononanesulfonic_acid_(PFNS).orcacosmo` |
| NaDoDFNt | Sodium dodecafluoro-3H-4,8-dioxanonanoate | 2250081-67-3 | publication tier (A-12) | `PFAS/Dodecafluoro-3H-4,8-dioxanonanoic_acid_(NaDoDFNt).orcacosmo` |
| NH4TetraFPt | Ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate | 62037-80-3 | release (promotion-v2), the surface of TFPA | `PFAS/2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic_acid_(NH4TetraFPt).orcacosmo` |
| NH4PFNt | Ammonium 4,8-dioxa-3H-perfluorononanoate | 958445-44-8 | publication tier (A-12) | `PFAS/4,8-Dioxa-3H-perfluorononanoic_acid_(NH4PFNt).orcacosmo` |
| Tri-PBDE | 2,4,4'-Tribromodiphenyl ether (BDE-28) | 41318-75-6 | release (promotion-v2) | `BFR/2,4-Dibromo-1-(4-bromophenoxy)benzene.orcacosmo` |
| HBCD | Hexabromocyclododecane (gamma-HBCD; the paper does not name the stereoisomer) | 134237-52-8 | release (promotion-v2) | `BFR/gamma-Hexabromocyclododecane.orcacosmo` |
| DECA | Decabromodiphenyl ether (BDE-209) | 1163-19-5 | publication tier (A-12) | `BFR/Decabromodiphenyl_ether_(BDE-209).orcacosmo` |
| TBBPA-dbp | Tetrabromobisphenol A bis(2,3-dibromopropyl ether) | 21850-44-2 | publication tier (A-12) | `BFR/Tetrabromobisphenol_A_bis(2,3-dibromopropyl_ether).orcacosmo` |
| P1 BBP | Butyl benzyl phthalate | 85-68-7 | release (promotion-v2) | `Phthalates/Benzyl_butyl_phthalate.orcacosmo` |
| P2 DBP | Di-n-butyl phthalate | 84-74-2 | release (promotion-v2) | `Phthalates/Dibutyl_Phthalate.orcacosmo` |
| P3 DEHP | Di-(2-ethylhexyl) phthalate | 117-81-7 | release (promotion-v2) | `Phthalates/Bis(2-ethylhexyl)_phthalate.orcacosmo` |
| P4 DEP | Diethyl phthalate | 84-66-2 | release (promotion-v2) | `Phthalates/Diethyl_Phthalate.orcacosmo` |
| P5 DiDP | Di-isodecyl phthalate | 26761-40-0, 68515-49-1 | release (promotion-v2) | `Phthalates/Diisodecyl_phthalate.orcacosmo` |
| P6 DiNP | Di-isononyl phthalate | 28553-12-0, 68515-48-0 | release (promotion-v2) | `Phthalates/Diisononyl_phthalate.orcacosmo` |
| P7 DnHP | Di-n-hexyl phthalate | 84-75-3 | release (promotion-v2) | `Phthalates/Dihexyl_phthalate.orcacosmo` |
| P8 DnOP | Di-n-octyl phthalate | 117-84-0 | release (promotion-v2) | `Phthalates/Dioctyl_phthalate.orcacosmo` |
