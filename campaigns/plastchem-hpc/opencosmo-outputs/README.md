# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) behind the partition and miscibility data DISSOLVE serves: the
2026-09-12/24 CHNO campaign and the halogen tier of amendment A-11 (2026-10-06/07). 7,160 contaminants:
5,830 from the promotion-v1 cohort, 219 tier-2 CHNO structures (500-700 g/mol) that converged after it and
1,111 from the halogen tier (C, H, N and O with F, Cl, Br or I, up to 700 g/mol).

## Layout

- `contaminants/<folder>/<name>.orcacosmo`: one surface per contaminant, in exactly one folder: the first of the five
  below that applies to it.
  1. `publication-sets/` (29): the PFAS, brominated flame retardants and phthalates of Zhou et
     al., *Solvent-Mediated Contaminant Removal from Plastic Waste Using Thermodynamic Modeling*, Green Chem. 2026
     (d5gc06059a), from its ESI, and nothing else, so these folders can be used to validate against the paper.
     The other members of the same families are in the folders below (other phthalates in
     `agent-families/Phthalates/`, other PFAS in `plastchem-groups/PFASs/`, other PBDEs in
     `structure-classes/polybrominated_diphenyl_ethers/`).
  2. `agent-families/` (435): the contaminant families DISSOLVE's agent uses when a question names
     a family ("phthalates", "antioxidants"). A compound in two families is filed in the smaller one.
  3. `plastchem-groups/` (1738): the structural groups of the PlastChem database (Wagner et al.
     2024, v1.0), for compounds in no agent family. A compound in several is filed in the most specific (PFAS,
     PBDEs, PCBs and dioxins first; alkanes and alkenes last).
  4. `structure-classes/` (4823): the rest, by structure: the PBDE congeners, else the
     halogen a compound carries, else its first functional group in DISSOLVE's structure search (in the order of
     the table below), else aliphatic hydrocarbons.
  5. `no-family/` (135): none of the above: mostly organic peroxides, nitrosamines, hydrazines and
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

Matched by CAS number, and by InChIKey where PlastChem files a compound under another CAS (DiNP, DnHP, DnOP). HBCD is
matched by the connectivity of 1,2,5,6,9,10-hexabromocyclododecane, so every computed stereoisomer is in `BFR/`;
PlastChem files the generic HBCD CAS 25637-99-4 under 1,1,2,2,3,3-hexabromocyclododecane, a
different compound, which is in `structure-classes/brominated/` instead. The
campaign holds neutral molecules of C, H, N, O, F, Cl, Br and I up to 700 g/mol taken from PlastChem, which is why the
sulfonic acids, the salts and the heaviest compounds have no file.

### `publication-sets/PFAS/`: PFAS (ESI Table S7), 10 files for 10 of its 26 compounds

| Compound | CAS | File(s), or why there is none |
|---|---|---|
| PFBA: Perfluorobutanoic acid | 375-22-4 | `Heptafluorobutyric_acid.orcacosmo` |
| PFPA: Perfluoropentanoic acid | 2706-90-3 | `Perfluorovaleric_acid.orcacosmo` |
| PFHxA: Perfluorohexanoic acid | 307-24-4 | `Perfluorohexanoic_acid.orcacosmo` |
| PFHA: Perfluoroheptanoic acid | 375-85-9 | `Perfluoroheptanoic_acid.orcacosmo` |
| PFOA: Perfluorooctanoic acid | 335-67-1 | `Perfluorooctanoic_acid.orcacosmo` |
| PFNA: Perfluorononanoic acid | 375-95-1 | `Perfluorononanoic_acid.orcacosmo` |
| PFDA: Perfluorodecanoic acid | 335-76-2 | `Perfluorodecanoic_acid.orcacosmo` |
| TFPA: 2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic acid | 13252-13-6 | `2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic_acid.orcacosmo` |
| PFTriDA: Perfluorotridecanoic acid | 72629-94-8 | its ORCA surface converged after the release was frozen; its partition and LLE calculations were not yet run |
| PFUnDA: Perfluoroundecanoic acid | 2058-94-8 | `Perfluoroundecanoic_acid.orcacosmo` |
| PFDoDA: Perfluorododecanoic acid | 307-55-1 | `Perfluorododecanoic_acid.orcacosmo` |
| PFTetraDA: Perfluorotetradecanoic acid | 376-06-7 | heavier than 700 g/mol |
| PFBS: Perfluorobutanesulfonic acid | 375-73-5 | contains sulfur |
| PFPS: Perfluoropentanesulfonic acid | 2706-91-4 | contains sulfur |
| PFHxS: Perfluorohexanesulfonic acid | 355-46-4 | contains sulfur |
| PFHS: Perfluoroheptanesulfonic acid | 375-92-8 | contains sulfur |
| PFOS: Perfluorooctanesulfonic acid | 1763-23-1 | contains sulfur |
| PFUnDS: Perfluoroundecanesulfonic acid | 749786-16-1 | not in PlastChem |
| PFTriDS: Perfluorotridecanesulfonic acid | 791563-89-8 | not in PlastChem |
| PFDS: Perfluorodecanesulfonic acid | 335-77-3 | contains sulfur |
| PFDoDS: Perfluorododecanesulfonic acid | 79780-39-5 | contains sulfur |
| KClHxDFS: Potassium 9-chlorohexadecafluoro-3-oxanonane-1-sulfonate | 73606-19-6 | a salt or a mixture of molecules |
| PFNS: Perfluorononanesulfonate | 474511-07-4 | not in PlastChem |
| NaDoDFNt: Sodium dodecafluoro-3H-4,8-dioxanonanoate | 2250081-67-3 | not in PlastChem |
| NH4TetraFPt: Ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate | 62037-80-3 | a salt or a mixture of molecules |
| NH4PFNt: Ammonium 4,8-dioxa-3H-perfluorononanoate | 958445-44-8 | a salt or a mixture of molecules |

### `publication-sets/BFR/`: Brominated flame retardants (Case Study 1), 11 files for 2 of its 4 compounds

| Compound | CAS | File(s), or why there is none |
|---|---|---|
| Tri-PBDE: 2,4,4'-Tribromodiphenyl ether (BDE-28) | 41318-75-6 | `2,4-Dibromo-1-(4-bromophenoxy)benzene.orcacosmo` |
| HBCD: Hexabromocyclododecane: 1,2,5,6,9,10-hexabromocyclododecane and its stereoisomers (alpha, beta, gamma and others) | 3194-55-6, 134237-50-6, 134237-51-7, 134237-52-8 | 10 files: `(+)-alpha-Hexabromocyclododecane.orcacosmo`, `(1R,2R,5S,6S,9S,10R)-1,2,5,6,9,10-hexabromocyclododecane.orcacosmo`, `1,2,5,6,9,10-Hexabromocyclododecane.orcacosmo`, `Cyclododecane,_1,2,5,6,9,10-hexabromo-,_(1R,2S,5R,6S,9S,10S).orcacosmo`, `Cyclododecane,_1,2,5,6,9,10-hexabromo-,_(1R,2S,5S,6S,9S,10R).orcacosmo`, `alpha-Hexabromocyclododecane.orcacosmo`, `beta-Hexabromocyclododecane.orcacosmo`, `delta-Hexabromocyclododecane.orcacosmo`, `gamma-Hexabromocyclododecane.orcacosmo`, `kappa-HBCD.orcacosmo` |
| DECA: Decabromodiphenyl ether (BDE-209) | 1163-19-5 | heavier than 700 g/mol |
| TBBPA-dbp: Tetrabromobisphenol A bis(2,3-dibromopropyl ether) | 21850-44-2 | heavier than 700 g/mol |

### `publication-sets/Phthalates/`: Phthalates (ESI Table S5), 8 files for 8 of its 8 compounds

| Compound | CAS | File(s), or why there is none |
|---|---|---|
| P1 BBP: Butyl benzyl phthalate | 85-68-7 | `Benzyl_butyl_phthalate.orcacosmo` |
| P2 DBP: Di-n-butyl phthalate | 84-74-2 | `Dibutyl_Phthalate.orcacosmo` |
| P3 DEHP: Di-(2-ethylhexyl) phthalate | 117-81-7 | `Bis(2-ethylhexyl)_phthalate.orcacosmo` |
| P4 DEP: Diethyl phthalate | 84-66-2 | `Diethyl_Phthalate.orcacosmo` |
| P5 DiDP: Di-isodecyl phthalate | 26761-40-0, 68515-49-1 | `Diisodecyl_phthalate.orcacosmo` |
| P6 DiNP: Di-isononyl phthalate | 28553-12-0, 68515-48-0 | `Diisononyl_phthalate.orcacosmo` |
| P7 DnHP: Di-n-hexyl phthalate | 84-75-3 | `Dihexyl_phthalate.orcacosmo` |
| P8 DnOP: Di-n-octyl phthalate | 117-84-0 | `Dioctyl_phthalate.orcacosmo` |

## 2. Agent families

DISSOLVE's definitions, from `src/dissolve/data/plastchem_families.json`. "Family members computed" counts every
member with a surface, including those filed in a publication set or a smaller family.

| Folder | Files here | Family members computed | Family (DISSOLVE's definition) |
|---|---:|---:|---|
| `Alkylphenols/` | 39 | 39 | butyl-, octyl- and nonylphenols and their ethoxylates; PlastChem group alkylphenols |
| `Antioxidants/` | 113 | 137 | hindered-phenol, aminic, phosphite and thioester antioxidants, gallates and tocopherols; any of hindered phenol, cycloalkyl phenol, diarylamine, phenylenediamine, dihydroquinoline, gallate, chromanol, benzofuranone, phosphite, thiodipropionate; excluding anthraquinone, ring aryl ketone, azo, nitro, nitroso, aryl acid, dimethylaminoaryl, benzotriazole uva, triazine uva |
| `Aromatic_amines/` | 21 | 21 | primary aromatic amines; PlastChem group aromatic_amines |
| `Benzophenones/` | 26 | 26 | benzophenone photoinitiators and UV absorbers; PlastChem group acetophenones_benzophenones; with a benzophenone core |
| `Bisphenols/` | 26 | 28 | bisphenol A and its analogues; PlastChem group bisphenols |
| `Parabens/` | 7 | 7 | 4-hydroxybenzoate esters; PlastChem group parabens |
| `Phthalates/` | 81 | 89 | ortho-phthalate plasticizers; PlastChem group orthophthalates, or outside it the structure ortho phthalate diester |
| `Salicylates/` | 11 | 11 | salicylate esters; PlastChem group salicylate_esters |
| `Slip_agents/` | 8 | 8 | fatty acid amides; PlastChem group aliphatic_primary_amides; at least 12 carbons |
| `Terephthalates/` | 21 | 21 | terephthalate, isophthalate and trimellitate esters; PlastChem group isophthalates_terephthalates_trimellitates |
| `UV_stabilizers/` | 82 | 95 | benzotriazole, triazine and benzophenone UV absorbers and hindered-amine light stabilizers; any of benzotriazole uva, triazine uva, hydroxybenzophenone, hals, cyanoacrylate, oxanilide, aryl salicylate, benzylidene malonate |

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
`structure-classes/polybrominated_diphenyl_ethers/` (157; the
congener of the paper, BDE-28, is in `publication-sets/BFR/`).

| Folder | Files | Shortest names in it |
|---|---:|---|
| `PFASs/` | 187 | Bifenthrin; Perflutren; Fludioxonil |
| `PBDEs/` | 5 | 2-Bromobiphenyl; 3-Bromobiphenyl; 4,4'-Dibromobiphenyl |
| `PCBs/` | 129 | 2-Chlorobiphenyl; 3-Chlorobiphenyl; 4-Chlorobiphenyl |
| `polychlorinated_naphthalenes/` | 2 | 1-Chloronaphthalene; Octachloronaphthalene |
| `PBDD_PBDF_PCDD_PCDF/` | 40 | 1-Bromodibenzofuran; 1,2-Dibromooxanthrene; 1-Bromodibenzo-p-dioxin |
| `DDT_DDE_DDD/` | 4 | O,P'-Ddt; p,p'-DDD; p,p'-DDE |
| `chlorinated_paraffins/` | 1 | Cereclor |
| `azodyes/` | 24 | Sudan I; Para Red; Sudan II |
| `benzotriazoles/` | 7 | 1H-Benzotriazole; 5-Chlorobenzotriazole; 5-Methyl-1H-benzotriazole |
| `phenolic_antioxidants/` | 9 | Masoprocol; meso-Hexestrol; 4,4'-Dihydroxydiphenyl ether |
| `acetophenones_benzophenones/` | 19 | Acetophenone; Acetosyringone; 1,4-Benzoquinone |
| `salicyclic_acid/` | 1 | Salicylic Acid |
| `aromatic_ethers/` | 10 | Anisole; Dibenzyl ether; Diphenyl ether |
| `aralkyladehydes/` | 8 | Lilial; 2-Phenylpropanal; Phenylacetaldehyde |
| `dibenzoyl_peroxide_derivatives/` | 4 | Benzoyl Peroxide; Bis(4-methylbenzoyl)peroxide; Bis(4-chlorobenzoyl) peroxide |
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
| `polybrominated_diphenyl_ethers/` | 157 | PBDE congeners: C12H(10-n)Br(n)O, two benzene rings joined by an ether oxygen, with nothing but bromine on them | Tribromodiphenyl ether; 1-Bromo-2-phenoxybenzene; 1-Bromo-3-phenoxybenzene |
| `fluorinated/` | 39 | carries fluorine and no other halogen | Etoxazole; Fluorobenzene; Vinyl fluoride |
| `chlorinated/` | 337 | carries chlorine and no other halogen | Mirex; Aldrite; Chloral |
| `brominated/` | 116 | carries bromine and no other halogen | Bronopol; Bromoform; Bromobenzene |
| `iodinated/` | 11 | carries iodine and no other halogen | Iodoform; Iodoethane; Iodobenzene |
| `several_halogens/` | 35 | carries two or more of F, Cl, Br and I | Cyfluthrin; Flufenoxuron; Bromochloromethane |
| `isocyanate/` | 48 | N=C=O | Methyl Isocyanate; Phenyl isocyanate; Dodecyl isocyanate |
| `epoxide/` | 102 | three-membered C-O-C ring | Glycidol; Picrotoxinin; Resibufogenin |
| `acrylate/` | 234 | acrylic or methacrylic ester or acid, CH2=C(H or CH3)-C(=O)O; not maleates, crotonates or cinnamates | Silux; Acrylic acid; Allyl acrylate |
| `anhydride/` | 61 | C(=O)OC(=O) | Dicarbonic acid; Acetic Anhydride; Carbic anhydride |
| `azo/` | 63 | C-N=N-C | Sudan Red B; Toluidine red; Disperse red 1 |
| `nitro/` | 49 | NO2 | Dinoseb; Nitrobenzene; Nitromethane |
| `nitrile/` | 75 | C#N | Ocrilate; Bucrilate; Enbucrilate |
| `quinone/` | 59 | para-quinone ring | Juglone; Quinizarin; Indanthrene |
| `benzotriazole/` | 2 | benzotriazole ring (UV absorbers) | 2,2'-[[(4-Methyl-1H-benzotriazol-1-YL)methyl]imino]bisethanol; 2H-1-Benzopyran-2-one, 7-(2H-naphtho(1,2-d)triazol-2-yl)-3-phenyl- |
| `benzophenone/` | 7 | two aryl rings on an open-chain C=O; not fluorenones or anthraquinones | Ethyl 2-benzoylbenzoate; Methyl 2-benzoylbenzoate; 2,4,5-Triethoxybenzophenone |
| `imide/` | 59 | C(=O)NC(=O) | Adipimide; Allantoin; Phenytoin |
| `urea_or_carbamate/` | 75 | N-C(=O)-N or N-C(=O)-O | Urea; Fenuron; Carbaryl |
| `carbonate_ester/` | 15 | O-C(=O)-O | Diallyl carbonate; Diethyl carbonate; Dimethyl carbonate |
| `lactone/` | 115 | cyclic ester (coumarins too), not a cyclic anhydride | Esculin; Coumarin; Glycolide |
| `phthalate_ester/` | 2 | benzene-1,2-dicarboxylate diester | Diisobutyl Perylenedicarboxylate; 1,2,4-Benzenetricarboxylic Acid 1,2-Bis(2-ethylhexyl) Ester |
| `hindered_phenol/` | 1 | phenol with tertiary alkyl at both ortho positions | 3,5-Di-tert-butyl-4-hydroxybenzoic acid |
| `phenol/` | 183 | OH on an aromatic ring | Indigo; Phenol; Thymol |
| `aromatic_amine/` | 139 | NH2 or NH on an aromatic ring | Aniline; Guanine; Ammelide |
| `amide/` | 207 | C(=O)N | Adipamide; Benzamide; Phenidone |
| `amine/` | 264 | aliphatic amine | Deanol; Grotan; Codeine |
| `carboxylic_acid/` | 209 | C(=O)OH | Felbinac; Meglutol; Icosapent |
| `ester/` | 793 | carboxylic ester, C(=O)O-C, not an anhydride | Pentol; Texanol; Dilaurin |
| `aldehyde/` | 126 | CH=O | Lyral; Neral; Citral |
| `ketone/` | 222 | C-C(=O)-C | Benzil; Benzoin; Phorone |
| `alcohol/` | 551 | aliphatic OH | Agar; Cedrol; Elemol |
| `ether/` | 154 | C-O-C, not an ester | Diglyme; Safrole; 2H-pyran |
| `fused_aromatic_rings/` | 68 | two aromatic rings sharing a bond, as in naphthalene; not indane or biphenyl | Indole; Skatole; Acridine |
| `aromatic_ring/` | 123 | six-membered aromatic ring | Auramine; Pyridine; Perbutyl MA |
| `long_alkyl_chain/` | 33 | seven or more CH2 in a row | 9-Nonadecene; 2-Tetradecene; Octyl formate |
| `aliphatic_hydrocarbons/` | 89 | carbon and hydrogen only, and none of the classes above (no aromatic ring, no seven-CH2 chain) | 2-Nonene; Isoprene; Valencene |

## How they were made

- Quantum chemistry: ORCA 6.1.1. A gas-phase `OPT BP86 def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`,
  whose solute step writes the CPCM surface (BP86/def2-TZVPD); def2 basis sets put an ECP on iodine.
- Conformers: RDKit ETKDGv3 generates up to 300 candidates, MMFF94 minimises them, and the lowest-energy one goes to DFT.
- Identity: the optimised geometry's connectivity must match the input (D-IDENT); structures whose hydrogen moved
  (azo pigments becoming hydrazones) were rejected and have no surface here.
- Thermodynamics: openCOSMO-RS 24a at 298.15 K. The installed 24a class has no iodine dispersion parameter
  (tau_53); it enters only dG_solv, not the activity coefficients used for partitioning and LLE.
- Folders: `classify_contaminants.py` (inputs: `../inputs/publication_sets_zhou2026.json`,
  `../inputs/census/plastchem_db_v1.0_groups_functions.csv` and DISSOLVE's families and structure search).

## Reassemble

```sh
cat opencosmo-outputs.tar.xz.part* > opencosmo-outputs.tar.xz
sha256sum -c SHA256SUMS
tar -xJf opencosmo-outputs.tar.xz
```
