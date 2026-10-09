# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) behind the partition and miscibility data DISSOLVE serves: the
2026-09-12/24 CHNO campaign, the halogen tier of amendment A-11 (2026-10-06/07) and the coverage campaign of amendment
A-13 (2026-10-08 onward), 8,532 contaminants: 5,830 from the promotion-v1 cohort, 219 tier-2 CHNO
structures (500-700 g/mol) that converged after it, 1,234 from the halogen tier (C, H, N and O with F, Cl, Br or I,
up to 700 g/mol) and 1,249 from the coverage campaign (the simulable PlastChem structures not yet served, lightest
first, sulfur, phosphorus and silicon included). Also 17
surfaces computed for the paper's compounds (amendment A-12, see section 1), which the release has served since
promotion-v3 (2026-10-08). How much of PlastChem this covers, and what cannot or should not be computed, is in
[Coverage of PlastChem](#coverage-of-plastchem).

## Layout

- `contaminants/<folder>/<name>.orcacosmo`: one surface per contaminant, in exactly one folder: the first of the five
  below that applies to it.
  1. `publication-sets/` (38): `PFAS/`, `BFR/` and `Phthalates/` hold exactly the compounds of
     Zhou et al., *Solvent-Mediated Contaminant Removal from Plastic Waste Using Thermodynamic Modeling*, Green Chem.
     2026 (d5gc06059a), one file each and nothing else, so they can be used to validate against the paper: 26 PFAS,
     4 brominated flame retardants and 8 phthalates.
     The other members of the same families are in the folders below (other phthalates in
     `agent-families/Phthalates/`, other PFAS in `plastchem-groups/PFASs/`, other PBDEs in
     `structure-classes/polybrominated_diphenyl_ethers/`).
  2. `agent-families/` (886): the contaminant families DISSOLVE's agent uses when a question names
     a family ("phthalates", "antioxidants"). A compound in two families is filed in the smaller one.
  3. `plastchem-groups/` (1826): the structural groups of the PlastChem database (Wagner et al.
     2024, v1.0), for compounds in no agent family. A compound in several is filed in the most specific (PFAS,
     PBDEs, PCBs and dioxins first; alkanes and alkenes last).
  4. `structure-classes/` (5563): the rest, by structure: the PBDE congeners, else the
     halogen a compound carries, else its first functional group in DISSOLVE's structure search (in the order of
     the table below), else aliphatic hydrocarbons.
  5. `no-family/` (236): none of the above: mostly organic peroxides, nitrosamines, hydrazines and
     five-membered heteroaromatics such as furans and imidazoles.
- `CLASSIFICATION.tsv`: one row per contaminant: InChIKey, name, file, folder, why it is there, and every grouping it
  belongs to (publication set, agent families, PlastChem groups, PlastChem functions, halogens, functional groups),
  not only the one that chose its folder. Filter it to find, say, every flame retardant or every chlorinated phthalate.
- `PUBLICATION_SETS.tsv`: each compound of the three publication sets, with its file(s) or why it has none.
- `MANIFEST.tsv`: every file, with its InChIKey (column `identity`), name, SHA-256 and campaign source.
- Names: a contaminant's own name, kept as written except where a file system needs otherwise: `/ \ : * ? " < > |`
  become `-`, spaces become `_`, and names longer than 100 characters are cut at a word boundary. Names equal apart
  from case carry `_[first InChIKey block]`, so every file is unique on case-insensitive systems. For example
  `contaminants/publication-sets/Phthalates/Bis(2-ethylhexyl)_phthalate.orcacosmo` is Bis(2-ethylhexyl) phthalate.
- `polymers/<polymer>/<polymer>__<conformer>.orcacosmo`: 274 oligomer conformer
  surfaces for 13 polymers. The 10 complete ensembles are used in the data: EVOH, nylon 6, nylon 6,6, PC,
  PE, PET, PP, PS, PVC, PVDF.
- `solvents/panel-32/<name>.orcacosmo`: the 32 solvent surfaces used for partitioning and miscibility.
- `solvents/common-69/<name>.orcacosmo`: DISSOLVE's 69 common solvents, computed separately.

## 1. Publication sets

One file per compound of the paper's ESI, in the species the paper modelled. The same files are also on the branch
as plain files, with each one's ORCA calculation files and its openCOSMO-RS partition and miscibility tables, in
`../publication-sets/`.

- PFAS (Table S7) are neutral acids, as the paper modelled them: across the paper's 32 solvents its values follow our
  neutral acids (r = 0.95-0.97) and not the anions (|r| below 0.1). A salt is its parent acid: NH4TetraFPt is TFPA's
  surface under its own label, and the two ADONA salts (NaDoDFNt, NH4PFNt) are one acid. Ten acids are the release's
  surfaces; the other 14 (the sulfonic acids, PFNS, the F-53B and ADONA acids, PFTriDA and PFTetraDA) were computed
  for this set (amendment A-12). A file named for a salt or an anion is named as the acid, with the paper's label,
  e.g. `Perfluorononanesulfonic_acid_(PFNS).orcacosmo`.
- Brominated flame retardants (Case Study 1): tri-PBDE is the ESI's 2,4,4'-tribromodiphenyl ether (BDE-28). The
  paper reports one HBCD value without naming the stereoisomer; the set holds gamma-HBCD, the main component of
  technical HBCD (the release's 9 other 1,2,5,6,9,10-HBCD stereoisomers are in
  `structure-classes/brominated/`). DECA and TBBPA-dbP are heavier than the release's 700 g/mol limit and were
  computed for this set (A-12).
- Phthalates (Table S5): from the release, by CAS number, and by InChIKey where PlastChem files a phthalate under
  another CAS (DiNP, DnHP, DnOP).

### `publication-sets/PFAS/`: PFAS (ESI Table S7), 26 files for 26 of its 26 compounds

| Compound | CAS | Species, computed in | File, or why there is none |
|---|---|---|---|
| PFBA: Perfluorobutanoic acid | 375-22-4 | neutral, release | `Heptafluorobutyric_acid.orcacosmo` |
| PFPA: Perfluoropentanoic acid | 2706-90-3 | neutral, release | `Perfluorovaleric_acid.orcacosmo` |
| PFHxA: Perfluorohexanoic acid | 307-24-4 | neutral, release | `Perfluorohexanoic_acid.orcacosmo` |
| PFHA: Perfluoroheptanoic acid | 375-85-9 | neutral, release | `Perfluoroheptanoic_acid.orcacosmo` |
| PFOA: Perfluorooctanoic acid | 335-67-1 | neutral, release | `Perfluorooctanoic_acid.orcacosmo` |
| PFNA: Perfluorononanoic acid | 375-95-1 | neutral, release | `Perfluorononanoic_acid.orcacosmo` |
| PFDA: Perfluorodecanoic acid | 335-76-2 | neutral, release | `Perfluorodecanoic_acid.orcacosmo` |
| TFPA: 2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic acid | 13252-13-6 | neutral, release | `2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic_acid.orcacosmo` |
| PFTriDA: Perfluorotridecanoic acid | 72629-94-8 | neutral, A-12 | `Perfluorotridecanoic_acid_[LVDGGZAZAYHXEY-UHFFFAOYSA-N#PFTriDA].orcacosmo` |
| PFUnDA: Perfluoroundecanoic acid | 2058-94-8 | neutral, release | `Perfluoroundecanoic_acid.orcacosmo` |
| PFDoDA: Perfluorododecanoic acid | 307-55-1 | neutral, release | `Perfluorododecanoic_acid.orcacosmo` |
| PFTetraDA: Perfluorotetradecanoic acid | 376-06-7 | neutral, A-12 | `Perfluorotetradecanoic_acid.orcacosmo` |
| PFBS: Perfluorobutanesulfonic acid | 375-73-5 | neutral, A-12 | `Perfluorobutanesulfonic_acid.orcacosmo` |
| PFPS: Perfluoropentanesulfonic acid | 2706-91-4 | neutral, A-12 | `Perfluoropentanesulfonic_acid.orcacosmo` |
| PFHxS: Perfluorohexanesulfonic acid | 355-46-4 | neutral, A-12 | `Perfluorohexanesulfonic_acid.orcacosmo` |
| PFHS: Perfluoroheptanesulfonic acid | 375-92-8 | neutral, A-12 | `Perfluoroheptanesulfonic_acid.orcacosmo` |
| PFOS: Perfluorooctanesulfonic acid | 1763-23-1 | neutral, A-12 | `Perfluorooctanesulfonic_acid.orcacosmo` |
| PFUnDS: Perfluoroundecanesulfonic acid | 749786-16-1 | neutral, A-12 | `Perfluoroundecanesulfonic_acid.orcacosmo` |
| PFTriDS: Perfluorotridecanesulfonic acid | 791563-89-8 | neutral, A-12 | `Perfluorotridecanesulfonic_acid.orcacosmo` |
| PFDS: Perfluorodecanesulfonic acid | 335-77-3 | neutral, A-12 | `Perfluorodecanesulfonic_acid.orcacosmo` |
| PFDoDS: Perfluorododecanesulfonic acid | 79780-39-5 | neutral, A-12 | `Perfluorododecanesulfonic_acid.orcacosmo` |
| KClHxDFS: Potassium 9-chlorohexadecafluoro-3-oxanonane-1-sulfonate | 73606-19-6 | neutral, A-12 | `9-Chlorohexadecafluoro-3-oxanonane-1-sulfonic_acid_(KClHxDFS).orcacosmo` |
| PFNS: Perfluorononanesulfonate | 474511-07-4 | neutral, A-12 | `Perfluorononanesulfonic_acid_(PFNS).orcacosmo` |
| NaDoDFNt: Sodium dodecafluoro-3H-4,8-dioxanonanoate | 2250081-67-3 | neutral, A-12 | `Dodecafluoro-3H-4,8-dioxanonanoic_acid_(NaDoDFNt).orcacosmo` |
| NH4TetraFPt: Ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate | 62037-80-3 | neutral, release | `2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic_acid_(NH4TetraFPt).orcacosmo` |
| NH4PFNt: Ammonium 4,8-dioxa-3H-perfluorononanoate | 958445-44-8 | neutral, A-12 | `4,8-Dioxa-3H-perfluorononanoic_acid_(NH4PFNt).orcacosmo` |

### `publication-sets/BFR/`: Brominated flame retardants (Case Study 1), 4 files for 4 of its 4 compounds

| Compound | CAS | Species, computed in | File, or why there is none |
|---|---|---|---|
| Tri-PBDE: 2,4,4'-Tribromodiphenyl ether (BDE-28) | 41318-75-6 | neutral, release | `2,4-Dibromo-1-(4-bromophenoxy)benzene.orcacosmo` |
| HBCD: Hexabromocyclododecane (gamma-HBCD; the paper does not name the stereoisomer) | 134237-52-8 | neutral, release | `gamma-Hexabromocyclododecane.orcacosmo` |
| DECA: Decabromodiphenyl ether (BDE-209) | 1163-19-5 | neutral, A-12 | `Decabromodiphenyl_ether_(BDE-209).orcacosmo` |
| TBBPA-dbp: Tetrabromobisphenol A bis(2,3-dibromopropyl ether) | 21850-44-2 | neutral, A-12 | `Tetrabromobisphenol_A_bis(2,3-dibromopropyl_ether).orcacosmo` |

### `publication-sets/Phthalates/`: Phthalates (ESI Table S5), 8 files for 8 of its 8 compounds

| Compound | CAS | Species, computed in | File, or why there is none |
|---|---|---|---|
| P1 BBP: Butyl benzyl phthalate | 85-68-7 | neutral, release | `Benzyl_butyl_phthalate.orcacosmo` |
| P2 DBP: Di-n-butyl phthalate | 84-74-2 | neutral, release | `Dibutyl_Phthalate.orcacosmo` |
| P3 DEHP: Di-(2-ethylhexyl) phthalate | 117-81-7 | neutral, release | `Bis(2-ethylhexyl)_phthalate.orcacosmo` |
| P4 DEP: Diethyl phthalate | 84-66-2 | neutral, release | `Diethyl_Phthalate.orcacosmo` |
| P5 DiDP: Di-isodecyl phthalate | 26761-40-0, 68515-49-1 | neutral, release | `Diisodecyl_phthalate.orcacosmo` |
| P6 DiNP: Di-isononyl phthalate | 28553-12-0, 68515-48-0 | neutral, release | `Diisononyl_phthalate.orcacosmo` |
| P7 DnHP: Di-n-hexyl phthalate | 84-75-3 | neutral, release | `Dihexyl_phthalate.orcacosmo` |
| P8 DnOP: Di-n-octyl phthalate | 117-84-0 | neutral, release | `Dioctyl_phthalate.orcacosmo` |

## 2. Agent families

DISSOLVE's definitions, from `src/dissolve/data/plastchem_families.json`. "Family members computed" counts every
member with a surface, including those filed in a publication set or a smaller family.

| Folder | Files here | Family members computed | Family (DISSOLVE's definition) |
|---|---:|---:|---|
| `Alkylphenols/` | 40 | 40 | butyl-, octyl- and nonylphenols and their ethoxylates; PlastChem group alkylphenols |
| `Antioxidants/` | 126 | 162 | hindered-phenol, aminic, phosphite and thioester antioxidants, gallates and tocopherols; any of hindered phenol, cycloalkyl phenol, diarylamine, phenylenediamine, dihydroquinoline, gallate, chromanol, benzofuranone, phosphite, thiodipropionate; excluding anthraquinone, ring aryl ketone, azo, nitro, nitroso, aryl acid, dimethylaminoaryl, benzotriazole uva, triazine uva |
| `Aromatic_amines/` | 22 | 22 | primary aromatic amines; PlastChem group aromatic_amines |
| `Azo_dyes/` | 20 | 47 | azo dyes and pigments; PlastChem group azodyes; with an azo linkage (C-N=N-C) |
| `Benzophenones/` | 27 | 27 | benzophenone photoinitiators and UV absorbers; PlastChem group acetophenones_benzophenones; with a benzophenone core |
| `Benzothiazoles/` | 9 | 15 | benzothiazole rubber accelerators and their relatives; PlastChem group benzothiazole; with a benzothiazole core |
| `Bisphenols/` | 31 | 33 | bisphenol A and its analogues; PlastChem group bisphenols |
| `Organophosphates/` | 170 | 175 | phosphate, phosphonate, phosphinate and phosphite esters and acids, phosphine oxides, phosphines and phosphoramides; PlastChem group organophosphates; containing phosphorus |
| `Parabens/` | 7 | 7 | 4-hydroxybenzoate esters; PlastChem group parabens |
| `Phthalates/` | 82 | 90 | ortho-phthalate plasticizers; PlastChem group orthophthalates, or outside it the structure ortho phthalate diester |
| `Salicylates/` | 11 | 11 | salicylate esters; PlastChem group salicylate_esters |
| `Siloxanes_and_silanes/` | 228 | 229 | silanes, siloxanes and silicone building blocks; PlastChem group silanes_siloxanes_silicones; containing silicon |
| `Slip_agents/` | 8 | 8 | fatty acid amides; PlastChem group aliphatic_primary_amides; at least 12 carbons |
| `Terephthalates/` | 21 | 21 | terephthalate, isophthalate and trimellitate esters; PlastChem group isophthalates_terephthalates_trimellitates |
| `UV_stabilizers/` | 84 | 98 | benzotriazole, triazine and benzophenone UV absorbers and hindered-amine light stabilizers; any of benzotriazole uva, triazine uva, hydroxybenzophenone, hals, cyanoacrylate, oxanilide, aryl salicylate, benzylidene malonate |

The Phthalates family is PlastChem's `orthophthalates` group plus every ortho-phthalate diester outside it (a
benzene-1,2-dicarboxylate diester whose ring carries nothing else): PlastChem's group alone leaves out diesters such as
bis(2-ethylbutyl) phthalate and butyl cyclohexyl phthalate. Ring-halogenated phthalates (tetrabromo- and
tetrachlorophthalates) are not in it; they are in the halogen folders of `structure-classes/`.

## 3. PlastChem groups

Folder names are PlastChem's column names, and a group holds what PlastChem put in it: `alkenes` includes aromatic
hydrocarbons such as pyrene, `aldehydes_simple` includes simple ketones such as camphor, and `PFASs` includes
compounds with a single CF3 or CF2 group, such as bifenthrin. PlastChem's `PBDEs` column flags brominated
biphenyls (5 computed here), not diphenyl ethers: PlastChem marks the PBDE congeners as
grouped but sets none of its group columns for them, so they are in
`structure-classes/polybrominated_diphenyl_ethers/` (204; the
congener of the paper, BDE-28, is in `publication-sets/BFR/`).

| Folder | Files | Shortest names in it |
|---|---:|---|
| `PFASs/` | 243 | Fipronil; Bifenthrin; Perflutren |
| `PBDEs/` | 5 | 2-Bromobiphenyl; 3-Bromobiphenyl; 4,4'-Dibromobiphenyl |
| `PCBs/` | 138 | 2-Chlorobiphenyl; 3-Chlorobiphenyl; 4-Chlorobiphenyl |
| `polychlorinated_naphthalenes/` | 2 | 1-Chloronaphthalene; Octachloronaphthalene |
| `PBDD_PBDF_PCDD_PCDF/` | 44 | 1-Bromodibenzofuran; 1,2-Dibromooxanthrene; 1-Bromodibenzo-p-dioxin |
| `DDT_DDE_DDD/` | 5 | O,P'-Dde; O,P'-Ddt; p,p'-DDD |
| `chlorinated_paraffins/` | 1 | Cereclor |
| `organophosphates/` | 1 | 2-[4-[4-(4-chlorophenyl)-4,5-dihydro-1H-pyrazol-3-yl]phenyl]sulfonyl-N,N-dimethylethanamine |
| `azodyes/` | 27 | Sudan I; Para Red; Sudan II |
| `benzotriazoles/` | 7 | 1H-Benzotriazole; 5-Chlorobenzotriazole; 5-Methyl-1H-benzotriazole |
| `benzothiazole/` | 7 | Benzothiazole; 1,2-Benzisothiazole; 2-Phenylbenzothiazole |
| `phenolic_antioxidants/` | 14 | Cresol red; Masoprocol; Phenol red |
| `acetophenones_benzophenones/` | 20 | Acetophenone; Acetosyringone; 1,4-Benzoquinone |
| `salicyclic_acid/` | 1 | Salicylic Acid |
| `aromatic_ethers/` | 10 | Anisole; Dibenzyl ether; Diphenyl ether |
| `aralkyladehydes/` | 8 | Lilial; 2-Phenylpropanal; Phenylacetaldehyde |
| `dibenzoyl_peroxide_derivatives/` | 5 | Benzoyl Peroxide; Bis(4-methylbenzoyl)peroxide; Bis(4-chlorobenzoyl) peroxide |
| `dihydropurinediones/` | 1 | Caffeine |
| `pyrazoles/` | 1 | 3,5-Dimethylpyrazole |
| `aliphatic_primary_amides/` | 6 | Acetamide; Formamide; Butyramide |
| `alkyl_nitrates/` | 1 | 2-Ethylhexyl nitrate |
| `carboxylic_acids_salts/` | 51 | Acetic Acid; Capric Acid; Lauric Acid |
| `ethanediols/` | 9 | 1,2-Butanediol; 1,2-Decanediol; 1,2-Hexanediol |
| `cyclic_acetals/` | 4 | Paraldehyde; 1,3-Dioxepane; 1,3-Dioxolane |
| `cyclic_ethers/` | 7 | Dioxane; (-)-Ambroxide; Tetrahydrofuran |
| `dialiphatic_ethers_excluding_unsatured/` | 3 | Diisopropyl ether; Methyl tert-butyl ether; 1-(1,1-Dimethylethoxy)-2-methylpropane |
| `alkane_ethers/` | 18 | Cedramber; Diglycerol; Decyl ether |
| `aliphatic_ketones/` | 15 | Acetone; 2-Hexanone; 2-Nonanone |
| `ketones_simple/` | 23 | 3-Eicosanone; 6-Dodecanone; 2-Hexacosanone |
| `aldehydes_simple/` | 102 | Camphor; Decanal; Hexanal |
| `alkynes/` | 18 | 1-Octyne; 1-Heptyne; Acetylene |
| `alkenes/` | 670 | Indan; Cumene; Indene |
| `alkanes/` | 359 | Butane; Carane; Decane |

## 4. Structure classes

Tried in the order of this table: a compound goes in the first class that matches. The functional groups are
DISSOLVE's structure-search definitions (`src/dissolve/contaminant_search.py`, RDKit SMARTS on each compound's
SMILES); the PBDE class is defined by formula in `classify_contaminants.py`.

| Folder | Files | What it holds | Shortest names in it |
|---|---:|---|---|
| `polybrominated_diphenyl_ethers/` | 204 | PBDE congeners: C12H(10-n)Br(n)O, two benzene rings joined by an ether oxygen, with nothing but bromine on them | Tribromodiphenyl ether; Octabromodiphenyl ether; 1-Bromo-2-phenoxybenzene |
| `fluorinated/` | 46 | carries fluorine and no other halogen | Etoxazole; Fluorobenzene; Vinyl fluoride |
| `chlorinated/` | 449 | carries chlorine and no other halogen | Mirex; Captan; Diuron |
| `brominated/` | 148 | carries bromine and no other halogen | Bronopol; Bromoform; kappa-HBCD |
| `iodinated/` | 15 | carries iodine and no other halogen | Iodoform; Iodoethane; Erythrosine |
| `several_halogens/` | 40 | carries two or more of F, Cl, Br and I | Cyfluthrin; Flufenoxuron; Tolylfluanid |
| `isocyanate/` | 49 | N=C=O | Tosyl isocyanate; Methyl Isocyanate; Phenyl isocyanate |
| `epoxide/` | 106 | three-membered C-O-C ring | Glycidol; Picrotoxinin; Resibufogenin |
| `acrylate/` | 241 | acrylic or methacrylic ester or acid, CH2=C(H or CH3)-C(=O)O; not maleates, crotonates or cinnamates | Silux; Acrylic acid; Allyl acrylate |
| `anhydride/` | 61 | C(=O)OC(=O) | Dicarbonic acid; Acetic Anhydride; Carbic anhydride |
| `azo/` | 123 | C-N=N-C | Acid Red 57; Cloth Red B; Sudan Red B |
| `nitro/` | 57 | NO2 | Dinoseb; Parathion; Fenitrothion |
| `nitrile/` | 89 | C#N | Ocrilate; Bucrilate; Enbucrilate |
| `quinone/` | 67 | para-quinone ring | Juglone; Quinizarin; Indanthrene |
| `benzotriazole/` | 4 | benzotriazole ring (UV absorbers) | Stilbene naphthotriazole; 4-Methyl-1H-benzotriazole; 2,2'-[[(4-Methyl-1H-benzotriazol-1-YL)methyl]imino]bisethanol |
| `benzophenone/` | 9 | two aryl rings on an open-chain C=O; not fluorenones or anthraquinones | Ethyl 2-benzoylbenzoate; Methyl 2-benzoylbenzoate; 2,4,5-Triethoxybenzophenone |
| `imide/` | 62 | C(=O)NC(=O) | Adipimide; Allantoin; Phenytoin |
| `urea_or_carbamate/` | 82 | N-C(=O)-N or N-C(=O)-O | Urea; Biotin; Fenuron |
| `carbonate_ester/` | 15 | O-C(=O)-O | Diallyl carbonate; Diethyl carbonate; Dimethyl carbonate |
| `lactone/` | 123 | cyclic ester (coumarins too), not a cyclic anhydride | Esculin; Coumarin; Glycolide |
| `phthalate_ester/` | 2 | benzene-1,2-dicarboxylate diester | Diisobutyl Perylenedicarboxylate; 1,2,4-Benzenetricarboxylic Acid 1,2-Bis(2-ethylhexyl) Ester |
| `hindered_phenol/` | 1 | phenol with tertiary alkyl at both ortho positions | 3,5-Di-tert-butyl-4-hydroxybenzoic acid |
| `phenol/` | 195 | OH on an aromatic ring | Indigo; Phenol; Thymol |
| `aromatic_amine/` | 169 | NH2 or NH on an aromatic ring | Ametryn; Aniline; Dapsone |
| `amide/` | 224 | C(=O)N | Adipamide; Benzamide; Phenidone |
| `amine/` | 304 | aliphatic amine | Deanol; Grotan; Metepa |
| `carboxylic_acid/` | 241 | C(=O)OH | Felbinac; Meglutol; Icosapent |
| `ester/` | 836 | carboxylic ester, C(=O)O-C, not an anhydride | Pentol; Texanol; Dilaurin |
| `aldehyde/` | 130 | CH=O | Lyral; Neral; Citral |
| `ketone/` | 228 | C-C(=O)-C | Benzil; Benzoin; Phorone |
| `alcohol/` | 562 | aliphatic OH | Agar; Cedrol; Elemol |
| `ether/` | 181 | C-O-C, not an ester | Proxan; Diglyme; Safrole |
| `fused_aromatic_rings/` | 128 | two aromatic rings sharing a bond, as in naphthalene; not indane or biphenyl | Indole; Skatole; Acridine |
| `aromatic_ring/` | 219 | six-membered aromatic ring | Fonofos; Auramine; Diazinon |
| `long_alkyl_chain/` | 64 | seven or more CH2 in a row | Octhilinone; 9-Nonadecene; 1-Octanethiol |
| `aliphatic_hydrocarbons/` | 89 | carbon and hydrogen only, and none of the classes above (no aromatic ring, no seven-CH2 chain) | 2-Nonene; Isoprene; Valencene |

## Coverage of PlastChem

Every entry of PlastChem v1.0 (17,932 in its full database) is in exactly one row of the first table, by the
first rule that applies in the order `../scripts/plastchem_coverage.py` tries them. Past the PlastChem flags, the
molecule judged is the one that would be computed: the entry's organic molecule in neutral form, so a salt or ion is
its parent acid or base (as Zhou et al. 2026 modelled their PFAS salts) and water of hydration is dropped. Iodine
counts as parameterized: openCOSMO-RS 24a lacks only its dispersion constant, which enters solvation free energies
and not partitioning or miscibility.

| Entries | Count | Rule |
|---|---:|---|
| Served | 9,098 | the release computed it and lists its PlastChem ID (since promotion-v4 also a salt, ion or hydrate, listed with its neutral parent), or its InChIKey if PlastChem does not flag it a UVCB, polymer or mixture |
| Served as its parent | 0 | a salt, ion or hydrate whose neutral parent the release computed but does not list it yet: it needs only an alias |
| To compute | 529 | simulable, not yet computed: 516 structures |
| Should not be computed | 1,942 | below |
| Cannot be computed | 6,363 | below |

| Cannot: no single molecule | Entries | Why |
|---|---:|---|
| no single structure | 3,667 | PlastChem flags it a UVCB, polymer or mixture; a SMILES it carries is usually a monomer or one component (PlastChem's own authors count these entries as not assessable as one structure) |
| no structure | 2,554 | no SMILES |
| several molecules | 135 | its SMILES lists different organic molecules (reaction products, adducts, salts of two organic ions), copies of one neutral molecule, or an organic molecule beside a neutral partner that is not a counter-ion (an ester written as alcohol and acid) |
| unparsable | 7 | its SMILES does not parse |

| Should not: outside openCOSMO-RS 24a or the recipe | Entries | Why |
|---|---:|---|
| inorganic | 991 | PlastChem's inorganic flag, or no organic molecule |
| metal | 667 | PlastChem's organometallic flag, a metal bonded in the molecule, a salt of a metal other than Li, Na, K, Rb and Cs (metal soaps such as zinc stearate), or an organometallic written as ions. openCOSMO-RS 24a has no metal parameters, and a higher level of theory would not supply them: its parameters are fitted to BP86/def2-TZVPD surfaces |
| permanent ion | 117 | still charged once neutralized (quaternary ammonium and the like) |
| large and flexible | 87 | above 700 g/mol with more than 20 rotatable bonds: one conformer cannot represent it |
| no parameters | 52 | an element without openCOSMO-RS 24a parameters: B, Ge, As, Se, Sb or Te |
| radical | 18 | open-shell as written: nitroxide stabilizers such as Tempol, and hydrosilanes whose SMILES lacks the hydrogen on silicon; the recipe computes closed-shell molecules |
| isotope-labelled | 10 | owner decision D-ISO: excluded, never mapped to the unlabelled compound |

That leaves 9,627 simulable entries. The release serves 9,098 of them (94.5%); 95% needs 48 more entries. The 516
structures still to compute are 345 with sulfur, phosphorus or silicon (no tier computed them before the coverage
campaign), 80 halogenated and 91 of C, H, N and O only; 236 are the neutral parents of salts and 276 are above 700
g/mol (rigid enough for one conformer). 98 failed; the other 418 were never run: about 2,000 CPU-hours of ORCA by the
campaign's cost fit (median 81 atoms with hydrogens; 68 above the 107 atoms the fit was made on). The cheapest 44
structures reach 95% for about 20 ORCA CPU-hours.

`../reports/plastchem-coverage-2026-10-09-v10/PLASTCHEM_COVERAGE.tsv` has one row per PlastChem entry: its bucket and
reason, and for the simulable ones the molecule that would be computed (InChIKey, SMILES, g/mol, atoms with hydrogens)
and the release status; `summary.json` has the counts. Both are made by `../scripts/plastchem_coverage.py` from the
census export of the PlastChem workbook and the served release (promotion-v10).

## How they were made

- Quantum chemistry: ORCA 6.1.1. A gas-phase `OPT BP86 def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`,
  whose solute step writes the CPCM surface (BP86/def2-TZVPD); def2 basis sets put an ECP on iodine.
- Conformers: RDKit ETKDGv3 generates up to 300 candidates, MMFF94 minimises them, and the lowest-energy one goes to DFT.
- Identity: the optimised geometry's connectivity must match the input (D-IDENT); structures whose hydrogen moved
  (azo pigments becoming hydrazones) were rejected and have no surface here.
- Thermodynamics: openCOSMO-RS 24a at 298.15 K. The installed 24a class has no iodine dispersion parameter
  (tau_53); it enters only dG_solv, not the activity coefficients used for partitioning and LLE.
- Publication-set surfaces of amendment A-12 (the PFAS acids the release lacked, DECA and TBBPA-dbP): the same
  recipe, structures from PubChem by CAS and checked against PlastChem. The sulfonic acids hold sulfur and four are
  above 700 g/mol, so the campaign's own tiers had left them out. Since promotion-v3 (2026-10-08) DISSOLVE serves
  their partition and miscibility rows with the rest of the release; they are also in `../publication-sets/`, and
  DISSOLVE's workbook screens still hold the paper's own COSMOtherm values for its 26 PFAS and 8 phthalates.
- Folders: `classify_contaminants.py` (inputs: `../inputs/publication_sets_zhou2026.json`,
  `../inputs/census/plastchem_db_v1.0_groups_functions.csv` and DISSOLVE's families and structure search).

## Reassemble

```sh
cat opencosmo-outputs.tar.xz.part* > opencosmo-outputs.tar.xz
cat opencosmo-outputs-promotion-v4.tar.xz.part* > opencosmo-outputs-promotion-v4.tar.xz
cat opencosmo-outputs-promotion-v5.tar.xz.part* > opencosmo-outputs-promotion-v5.tar.xz
cat opencosmo-outputs-promotion-v6.tar.xz.part* > opencosmo-outputs-promotion-v6.tar.xz
cat opencosmo-outputs-promotion-v7.tar.xz.part* > opencosmo-outputs-promotion-v7.tar.xz
cat opencosmo-outputs-promotion-v8.tar.xz.part* > opencosmo-outputs-promotion-v8.tar.xz
cat opencosmo-outputs-promotion-v9.tar.xz.part* > opencosmo-outputs-promotion-v9.tar.xz
cat opencosmo-outputs-promotion-v10.tar.xz.part* > opencosmo-outputs-promotion-v10.tar.xz
sha256sum -c SHA256SUMS
tar -xJf opencosmo-outputs.tar.xz
tar -xJf opencosmo-outputs-promotion-v4.tar.xz
tar -xJf opencosmo-outputs-promotion-v5.tar.xz
tar -xJf opencosmo-outputs-promotion-v6.tar.xz
tar -xJf opencosmo-outputs-promotion-v7.tar.xz
tar -xJf opencosmo-outputs-promotion-v8.tar.xz
tar -xJf opencosmo-outputs-promotion-v9.tar.xz
tar -xJf opencosmo-outputs-promotion-v10.tar.xz
```

The 8 sets unpack into the same `opencosmo-outputs/` tree: the first holds the v2 archive (the CHNO campaign, the halogen tier and the publication sets), each later one the contaminants a release added (amendment A-13; parts are never rewritten).
