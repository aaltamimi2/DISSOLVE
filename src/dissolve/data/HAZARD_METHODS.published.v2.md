# Hazard Analysis, v2: the PubChem safety snapshot (published Methods)

Snapshot `pubchem_safety_snapshot.v3.duckdb`, pinned in `safety._SNAPSHOT_SHA256`, with its content digest recorded
beside the pin. Built on 2026-09-25, and rebuilt on 2026-09-26 with the EU classification v2 and the closed-cup flash
point rule, by `python -m dissolve.safety_snapshot build` from the PubChem pull of 987 solvents taken 2026-08-28 (`_pull_checkpoint.v2.jsonl`, 38 MB, kept outside the repository). This document supersedes
v1's statements about PubChem fields: they are served from this pinned snapshot with no network call, and the fields
are read as described below. v1's G-score and green-screen sections still hold.

## Why v2

An audit on 2026-09-25 re-ran the v1/v2 readers on the raw pull and reproduced every stored value, 987 of 987. That
showed each error came from a line of code, not from the data:
- **Flash point:** a hyphen between two numbers was read as a minus sign ("95-96 °F" became −96 °F, so nitromethane
  was stored at −71 °C against the true 35 °C). The lowest value of any one source was kept, even when only one
  source said it (MTBE −80 °C against −28 °C).
- **Autoignition:** the same bug; diethyl ether was stored at −180 °C.
- **Vapour pressure:** 343 values present in PubChem were never read ("mm Hg", "[mmHg]", "4.9X10-2", ICSC formats).
  The first match was kept, not the value near 25 °C.
- **Hazard statements:** EU harmonized entries for other substances attached to a compound were merged in.
  Isopropanol gained aquatic toxicity from a "reaction mass" entry, and butane gained carcinogenicity from an entry for
  butane containing butadiene. The signal word and pictograms came from every line of the sources, not from the
  statements kept.
- **Exposure limits:** only the first string was read, so benzene lost its 0.1 ppm NIOSH limit.
- **LD50 and LC50:** only the first three values were kept.

The same readers served live PubChem cards, so they had the same errors.

## How each field is read

`safety._pubchem_fields` reads the eight PubChem headings for the snapshot and for live cards alike. Each string keeps
the source PubChem names for it.

| Field | Rule |
|---|---|
| Flash point | The lowest value a second, different source confirms within 3 °C; else the median of the sources. A lower closed-cup value then replaces that reading, never a higher one, when ICSC or NIOSH gives it or a second source confirms it (open-cup values run high: acetonitrile read 5.6 °C from four open-cup sources against ICSC's 2 °C closed cup; 31 solvents move down, none up). A range gives its lower end, a hyphen between numbers is a range, and a "±" tolerance is dropped. Bounds ("above 200 °F") count only when nothing else exists, and class-definition text is set aside. With no plain value, text saying the solvent does not burn marks it non-flammable. Flammable gases and explosives are marked as such |
| Autoignition | The lowest stated value (the conservative choice), with the spread across sources |
| Vapour pressure | The value whose stated temperature is nearest 25 °C, within 15–35 °C; else a value with no stated temperature (PubChem's bare "[mmHg]" values are room-temperature ones); else the value measured nearest 25 °C. The basis is stored |
| Hazard statements | The solvent's own EU classification (`eu_classification.v2.json`): harmonised hazard classes from CLP Annex VI, the other classes from the lead REACH registrant's own classification of the substance, else from the classification at least half of at least 10 notifying companies give. Every GHS record of the lead dossier counts when it covers the substance's own compositions; a record for a special grade (an impurity at or above a threshold, a technical, commercial, raw or crude grade, a solution or a reaction mass) is left out, and within one hazard class the most severe code is kept. Version 1 read only the first record, which in 76 dossiers is the Annex VI entry alone: dichloromethane was served as "suspected of causing cancer" only, without its skin and eye irritation and drowsiness. Each row lists the records used and left out. The entries are found by the EC and index numbers ECHA assigns to the CAS number; entries for another composition (a "reaction mass", a mixture, "containing ≥ 0.1 % butadiene") are left out. A majority "not classified" means not classified. No national list is used, and "no EU data" is never read as safe. Each code takes PubChem's most common wording. Every PubChem line is kept as provenance, and severe codes PubChem lists that the classification does not are surfaced |
| Signal word, pictograms | From the kept statements: Danger if any kept statement carries Danger. Pictograms follow the CLP precedence rules |
| Exposure limits | Every current limit an authority states, labelled 8- or 10-hour TWA, STEL or ceiling. Vacated or proposed limits and notes without a value are left out |
| LD50, LC50 | Up to five each: oral first, then dermal, then inhalation; rat, then mouse, then rabbit |

## Peroxide formers

`peroxide_formers.v1.json` replaces the earlier five-row hand-typed table. It holds 189 compounds from:
- the VUMC list DISSOLVE cited before, a reproduction of Kelly 1996 classes A–D;
- Prudent Practices in the Laboratory (2011), Table 4.8;
- the Illinois DRS list;
- the EU harmonized entries carrying EUH019 (CLP Annex VI, consolidated 2026-07-01).

Each source's URL and checksum is recorded. A class is chosen by votes. A list naming the compound is one vote, and a
class entry matched by structure is half a vote. Ties go to Prudent Practices or Illinois, and a class only VUMC gives
is "disputed". No list publishes negatives, so an unlisted compound is "not listed", never "does not form peroxides".
Ethers with hydrogens next to the oxygen that no list names are kept as a structural alert.

## CHEM21 scores

The recipe follows Prat et al., *Green Chem.* 2016, 18, 288 (`safety._chem21_score_from_inputs`).
- **Stored, not recomputed:** the build computes every solvent's scores once and stores them (`chem21_scores`).
  Answers read the stored scores.
- **Safety:** from the flash-point band.
  - A non-flammable solvent scores 1. "Non-flammable" means its text says it does not burn, or it has hazard
    statements and none is a flammability statement (dichloromethane, water).
  - +1 when the autoignition temperature is below 200 °C.
  - +1 for an EU EUH019 peroxide former. An ether listed only by the lab lists is "peroxide point not assessed",
    not a point.
- **Health and Environment:** from the hazard statements and the boiling point.
  - The guide scores a solvent without such statements as 1 (Health) or its boiling-point band (Environment) only
    when it is fully registered under REACH, and as 5 otherwise.
  - REACH status comes from ECHA CHEM: 703 of the 987 solvents are registered, 267 are not, and 17 are not in ECHA.
    For a solvent in ECHA without a usable classification the status is withheld, so the untested default
    applies.

**Not assessed:** the guide's static-charge point (resistivity above 10⁸ Ω·m) and its automatic 10 for high
decomposition energy (nitromethane). DISSOLVE holds no data for either.

## Checks and known answers

`python -m dissolve.safety_snapshot check` fails the build on any of these:
- a flash point below −150 °C for a liquid boiling above −100 °C;
- a negative autoignition temperature;
- a vapour pressure without a basis;
- a signal word or pictogram the kept statements do not support;
- a statement from another substance's EU entry;
- a solvent without stored CHEM21 scores;
- fewer than 59 of the CHEM21 guide's 62 published flash points in the right Safety band.

The guide's published values are `chem21_guide.v1.json` (the ACS GCI Pharmaceutical Roundtable reproduction of the
guide's tables, with its checksum). They are known answers only, never an input. Against the guide, matched by CAS
number:

| | v2 snapshot | v3, corrected readers | v3, plus EU classification and REACH | v3, plus every lead record and closed cup (2026-09-26) |
|---|---|---|---|---|
| Flash points in the right Safety band (of 62) | 54 | 59 | 59 | 59 |
| All three scores equal (of 66) | 13 | 17 | 37 | 36 |
| Default ranking equal (of 66) | 34 | 42 | 57 | 56 |

The last column agrees with the 2016 guide once less on each count: cyclohexanone's current registrant classification
adds serious eye damage (Health 2 → 4, problematic), and toluene's and xylene's add a chronic aquatic hazard
(Environment 3 → 5).

The three flash-point misses:
- CPME and 2-MeTHF have no PubChem flash point.
- Chlorobenzene is 23.9 °C against the guide's 29 °C, on a band edge.

The 10 remaining ranking differences:
- **EU classifications newer than the 2016 guide (4):** MIBK and 1,4-dioxane (carcinogenicity), isoamyl alcohol, and
  cyclohexanone (its registrant's serious eye damage).
- **Adjustments DISSOLVE cannot assess (5):**
  - the peroxide or static-charge point for MTBE and anisole;
  - no PubChem flash point for CPME and 2-MeTHF;
  - nitromethane's decomposition rule.
- **One data gap:** glycerol has no boiling point in the solvent table, so its Environment score falls back to 5
  where the guide gives 7.

## Rebuilding

```
python -m dissolve.safety_snapshot build <raw pull .jsonl> src/dissolve/data/pubchem_safety_snapshot.v3.duckdb \
    --fetched-at 2026-08-28T21:29:23.217777+00:00
```

The content digest is stable across builds. The file checksum is not (DuckDB's page layout varies), so the pin in
`safety.py` is updated with each rebuild.
