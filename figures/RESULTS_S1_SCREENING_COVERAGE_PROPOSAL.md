# Pre-build proposal: Results §1 thermodynamic screening coverage draft

Status: **submitted to `codex-v12-auditor-4`; no figure may be built until
clause-by-clause `GATE: ADMIT`.** This is a first-draft proposal, not a
publication-figure specification.

## Clause 1 — scope and outputs

Build one new first-draft figure, `results_s1_screening_coverage`, without
modifying any `unified_solubility_query.*` file. The draft has exactly two
panels:

- **A. Stored pair catalog:** one horizontal bar per stored polymer; bar length
  is the count of distinct stored solvents for that polymer. The axis/caption
  says **"stored solubility pairs / 990"**. It never calls a stored pair
  qualified, evaluable, or complete.
- **B. Envelope cell fates:** three non-overlapping circular data marks whose
  areas are proportional to the cell counts for `evaluable`,
  `measurement-refused`, and `not measured`. Exact counts and definitions sit
  beside the marks; the nonlinear-looking diameter is not used as the encoding
  (area is), and that is stated in the panel.

There are no solubility-versus-temperature curves, no interpolated gaps, no
screening or separation calls, and no claim of behavioural diversity. Files
proposed after admission are:

- `figures/results_s1_screening_coverage.py`
- `figures/results_s1_screening_coverage.pdf`
- `figures/results_s1_screening_coverage.png`
- `figures/results_s1_screening_coverage.provenance.json`
- `figures/test_results_s1_screening_coverage.py`

## Clause 2 — numeric sources and digest locks

The generator refuses to measure or render unless both current digests match:

| Source | SHA-256 | Numeric role |
|---|---|---|
| `src/dissolve/data/thermodynamics.duckdb` | `4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc` | All stored polymer, solvent, temperature, raw-valid, and `invalid_reason` counts |
| `src/dissolve/thermodynamics.py` | `2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204` | Runtime rule at `get_connection`: `is_valid OR invalid_reason = 'exact_100_artifact'` |

The cross-lane steer is framing, not a numeric source. Its digest is
`a0b8a7337df049b3abd9bfe2330d16cb6027cd7aabbf80d6ba767bf4b5d4b49a`
at `/home/aaltamimi2/dissolve-v12-audit/RESULTS_S1_COVERAGE_FIGURE_STEER.md`.
Every expected count below is re-derived from the two numeric sources; no value
is accepted because it appears in the steer.

## Clause 3 — panel/source map and assertions

Panel A uses this read-only SQL against the locked DuckDB asset:

```sql
SELECT polymer,
       COUNT(DISTINCT solvent) AS stored_pairs,
       COUNT(*) AS stored_cells,
       COUNT(DISTINCT temperature_c) AS temperature_nodes
FROM solubility_grid
GROUP BY polymer
ORDER BY stored_pairs DESC, polymer;
```

A second grouped query asserts that all 9,052 stored pairs contain exactly 28
stored temperature rows. A distinct-node query asserts the exact stored nodes
are 25–160 °C in 5 °C increments. Drawn bar values must resolve to nine
polymers at 990, PU at 78, NYLON6 at 32, and PES at 32; their sum must be 9,052.
The panel lead says that nine polymers share the 990-solvent, 28-node catalog,
while the three named polymers have ragged solvent catalogs.

Panel B queries raw asset dispositions only:

```sql
SELECT is_valid, COALESCE(invalid_reason, '<NULL>') AS invalid_reason,
       COUNT(*) AS cells
FROM solubility_grid
GROUP BY is_valid, invalid_reason;
```

It then applies the locked runtime rule from `thermodynamics.py` and asserts:

- evaluable = 248,378 raw-valid + 4,096 `exact_100_artifact` served as clipped
  100 wt% = 252,474;
- measurement-refused = 982 `nonpositive` cells only;
- not measured = 79,184 cells;
- the envelope is 12 × 990 × 28 = 332,640 cells and equals the sum of the
  three fates; and
- `79,184 == ((990 - 78) + (990 - 32) + (990 - 32)) * 28`.

The last identity is printed in plain language: the unmeasured cells are whole
missing pairs on PU, NYLON6, and PES, each multiplied by all 28 temperature
nodes. The figure does not show a fill-rate or turn 253,456 / 332,640 into a
quality score. The 5,078 raw flags are never drawn as a Results category.

No SQL used by the figure selects `solubility_pct`.

## Clause 4 — deterministic drawing and provenance

The script exposes `measure()`, `validate()`, `draw()`, `verify_geometry()`,
and `generate(output_dir=...)`. The documented reproducer is:

```text
PYTHONPATH=src:. python figures/results_s1_screening_coverage.py
```

It uses Agg, DejaVu Sans, one 10 pt black text size, fixed 7.25-inch print
width, fixed layout coordinates, fixed palette tokens from the style proposal,
fixed PNG DPI, and fixed PDF metadata. The provenance sidecar records source
paths and expected/actual digests, every SQL statement, a field-to-panel map,
the generator digest, the standards report, the exact command, and output
SHA-256 digests. The figure footer carries compact source names and digest
prefixes; the sidecar carries the full values.

Panel A uses blue bars. Panel B uses green for evaluable, vermillion for
measurement-refused, and neutral gray for not measured. Colour carries state
only; all labels and numerals remain black. No bar track is required, avoiding
intentional bar/track overlap. Panel B circle area, not diameter, is
proportional to count, and the 982-cell mark is sized to remain at least 7 pt
at print scale.

## Clause 5 — accept tests and must-fire sides

The shared `figures/standards_checker.py` is run on this draft and the existing
query schematic. The existing 14 generic must-fire controls remain required.
Coverage-specific tests add these deliberately defective cases:

1. A wrong DuckDB digest must fail before any SQL query or render.
2. Moving two fate circles into overlap must fail with `shape collision`.
3. Moving a numeric label onto a bar must fail with `text/shape collision`.
4. Changing one sparse pair count must fail the 9,052 total and sparse-hole
   identity checks.
5. Reclassifying the 4,096 `exact_100_artifact` cells as refused must fail the
   runtime-disposition assertions.
6. Two clean generations in separate temporary directories must yield
   byte-identical PDF, PNG, and provenance JSON.

The harness prints every must-fire case as an expected failure. A deliberately
accept-all checker substitution must make the harness return nonzero. No claim
that overlap checking passes will be made until these coverage-specific
controls and the generic controls both fire under auditor reproduction.

## Clause 6 — representative-curve shortlist (proposal only; no choice)

No candidate below is selected, ranked easy/hard, or drawn. These are
auditability and coverage-stratum candidates for the later §1 spec, not a
claim that they establish behavioural diversity:

| Candidate stored pair | Why it remains on the shortlist | Trace |
|---|---|---|
| LDPE–dodecane | Dense-catalog polymer; a direct 28-node curve is already preserved, including runtime-served clipped saturation, making independent reproduction unusually easy. | DuckDB above; `/home/aaltamimi2/dissolve-v12-audit/gate_consolidate_baseline.json`, key `05.rows`, SHA-256 `bfefd264e1b694e116e07756fca50964165975a0cfb81fa0baafb62cdd75eb01` |
| PP–dodecane | Same stored solvent and the same 28 nodes as LDPE, so the owner can choose a controlled same-solvent polymer comparison without changing solvent identity. | DuckDB above; the same baseline, key `06.rows` |
| PU–butanone | Intermediate ragged catalog (78 stored pairs); this stored pair has all 28 cells evaluable and can represent the coverage caveat if the owner wants a sparse-catalog polymer in the curve panel. | DuckDB above; direct grouped read-only SQL |
| NYLON6–butanone | One of the two 32-pair catalogs; this stored pair has all 28 cells evaluable and can show that a complete stored curve does not imply a 990-solvent catalog. | DuckDB above; direct grouped read-only SQL |
| PES–butanone | The other 32-pair catalog; this stored pair also has all 28 cells evaluable, leaving the owner free to choose which sparse polymer, if either, belongs in the narrative. | DuckDB above; direct grouped read-only SQL |

For the three butanone candidates, the verification query groups by polymer and
solvent and counts 28 stored cells, 28 cells satisfying the runtime rule, and
zero `nonpositive` cells. No solubility values or shape summaries are used to
recommend them. The §1 spec must still decide the target polymer set, which
pair is scientifically easy versus hard, what behavioural contrast the curves
must land, and whether a sparse-catalog caveat belongs in the main panel. Until
then, the curves half is blocked by owner pair selection and remains unbuilt.

## Clause 7 — owner stops

The proposal uses read-only SQL only. It does not run BioSTEAM, edit frozen
`thermo.py` or `tests/test_thermo.py`, edit `src/dissolve/`, call any screening
or separation function, modify another worktree, or merge to `v12`.
