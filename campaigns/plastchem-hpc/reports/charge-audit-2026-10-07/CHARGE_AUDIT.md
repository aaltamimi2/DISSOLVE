# Charge audit of the PlastChem release (2026-10-07)

**Question (owner, 2026-10-07):** "are there any other similar compounds which should actually be charged but that
we treat as alcohols?"

**Context.** Every compound in the PlastChem release (promotion-v2, 7,160 computed) is modelled as one neutral
molecule. That is the campaign's rule: salts and permanent ions were excluded, and acids keep their acidic proton
(–COOH, not –COO⁻). Zhou et al., Green Chem. 2026 did the same for their PFAS: across 32 solvents their values
follow our neutral acids (r = 0.95–0.97) and not the anions (|r| below 0.1), and they modelled each salt as its
parent acid. Amendment A-12 first computed the paper's PFAS as anions, on the understanding that the paper had, found
this mismatch, and recomputed them neutral. The anion runs are kept in the campaign's storage, not in the repo.
This audit asks which computed compounds would carry a charge where ionization happens.

## Method

`charge_audit.py` (this folder) matches each computed compound's SMILES against the classes below, in order. The
first match wins. `charge-audit.tsv` lists every flagged compound with its class, CAS number, InChIKey and SMILES.
The pKa ranges are typical aqueous literature values for each class, for orientation only. No pKa was computed.

## Result

420 of the 7,160 computed compounds carry an ionizable group:

| Class | Count | Examples | Typical aqueous pKa | Ionized form |
|---|---:|---|---|---|
| Fluorinated carboxylic acids, fluorine on the alpha carbon (the paper's PFAS chemistry) | 40 (10 are the paper's PFAS) | trifluoroacetic acid; 11H-perfluoroundecanoic acid; 28 branched PFOA isomers (C8) | about 0–1 | anion in water and protic solvents |
| Amino acids and aminopolycarboxylates | 9 | glycine, asparagine, tricine; EDTA, NTA, DTPA, HEDTA, GLDA; a glycine surfactant | about 2 and 9–10 | zwitterion in water; the chelators are polyanions |
| Strong bases: guanidines, amidines, imidazolines | 13 | 1,3-diphenylguanidine (DPG, a rubber accelerator), di-o-tolylguanidine, triphenylguanidine, tetramethylguanidine, DBU, guanidine, guazatine, fatty imidazolines | about 10–14 (conjugate acid) | cation in water |
| Diacids with conjugated or vicinal COOH | 4 | oxalic, maleic, fumaric, cis-aconitic acid | about 1.2–3 (first proton) | mono-anion in water |
| Polynitrophenols | 3 | 2,4-dinitrophenol, DNOC, dinoseb | about 4–4.6 | phenolate at pH 7 |
| Other carboxylic acids | 351 | fatty acids, aspirin, phthalate monoesters (MEHP, monoethyl and monobenzyl phthalate) | about 3–5 | anion in water near neutral or basic pH |

Absent from the release: sulfonic and phosphoric acids, and permanent ions. The campaign holds no sulfur or
phosphorus, and it excluded ions and salts.

## Reading

- **Whether "charged" is right depends on the medium.** DISSOLVE's partition and miscibility data are for a polymer
  and an organic solvent. Ionization needs a polar, usually aqueous, medium. In most organic solvents and in polymers,
  even strong acids stay largely undissociated or ion-paired. So neutral is a defensible default for that data, and
  it is the right one for the weak acids and bases.
- **The fluorinated acids are the clearest case.** They are strong acids (pKa about 0–1), so they are anions in water
  and in protic solvents. Neither the release nor the paper models them that way; for aqueous washes that matters.
- **Zwitterions are the second weak point.** The amino acids and chelators are modelled as uncharged non-zwitterionic
  tautomers, which do not exist in polar media.
- **Strong bases matter for aqueous or acidic washes.** DPG and the imidazolines are relevant plastic additives, and
  they are cations in water.

## Proposed next steps (future direction; not started)

1. **Anion surfaces for the 40 fluorinated acids**, beside the neutral ones, with the A-12 machinery
   (`build_publication_inputs.py` pattern, charge from the manifest, charge-aware identity check). A-12 already
   computed 24 PFAS anions (the paper's set) with partition rows, kept on Euler and R: (state/publication-v1,
   newcontam-thermo-v1 batches p00 and p01). Partition and LLE for ions need a decision on how openCOSMO-RS treats
   charged species.
2. **A species convention per medium**, decided with the collaborator:
   - neutral for organic solvent–polymer partitioning (today's data);
   - ionized, or speciation-weighted log D at a stated pH, for aqueous washes.
   The answer rules would then say which species a number refers to.
3. **Zwitterion tautomers** for the 9 amino acids and chelators, if aqueous screens are added.
4. **Flags in answers.** A screen of a flagged compound says it is modelled neutral and would be ionized in water
   (strong acids, strong bases, zwitterions), the way solid contaminants carry the liquid–liquid caveat.

## Files

- `charge_audit.py`: the screen (`python3 charge_audit.py <release dir> charge-audit.tsv`; needs RDKit).
- `charge-audit.tsv`: the 420 flagged compounds.
- The paper's PFAS, neutral, with their partition and miscibility rows: `../../publication-sets/PFAS/`.
