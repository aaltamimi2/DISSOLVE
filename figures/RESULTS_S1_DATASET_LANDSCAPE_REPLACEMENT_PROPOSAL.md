# Proposal: single-panel dataset-landscape replacement drafts

Status: **proposal only; do not build before standing auditor-4 issues an
explicit clause-by-clause `GATE: ADMIT`.** Every named output below is a
separate figure file with exactly one Matplotlib axes. There is no multi-panel
composite, subplot grid, or inset axes. The owner may assemble the admitted
files later in Illustrator.

These drafts would replace the two admitted landscape drafts in the main-text
story; the existing files remain untouched until the owner separately directs
retirement or deletion. They are first drafts, not final publication figures.

## Clause 1 — analysis cohort and authoritative counts

The owner's five headline grid counts are mutually consistent with one exact
cohort: all stored polymers **except PU**. The proposal names that selection
rather than silently inferring it.

Read-only SQL against
`src/dissolve/data/thermodynamics.duckdb`, SHA-256
`4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc`,
reproduces for this cohort:

- 11 polymers;
- 990 distinct fitted-grid solvent labels;
- 8,974 stored polymer-solvent pairs;
- 28 stored temperature nodes, 25--160 °C in 5 °C increments; and
- 251,272 stored grid cells (`8,974 × 28`).

The proposal will not label all 251,272 cells "evaluable values." Runtime
disposition from `src/dissolve/thermodynamics.py`, SHA-256
`2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204`,
reproduces 250,527 evaluable cells (246,525 raw-valid plus 4,002
`exact_100_artifact` served ceilings) and 745 refused `nonpositive` cells in
the 11-polymer cohort.

The property/admission stream is distinct from the fitted grid. The same
locked DuckDB contains 1,006 `solvent_data` property records, of which 846
have an original stored boiling point, and 786 governed
`solvent_admission_data` records. All 786 admission records have CAS, an
effective boiling point at least 25 °C, and a `hazard_lookup_status` beginning
with `resolved`; this is an admission record, not a claim that a solvent is
safe. The underlying property source is
`src/dissolve/data/Solvent_Data.csv`, SHA-256
`c9dfd6556ec3f755157b951408a852df44b7cbdc18dea02c36266000d0c9bd49`.

## Clause 2 — separately exported main-text assets

Each item is one 7.25-inch-wide, single-axes PNG+SVG pair with a same-basename
provenance JSON:

1. `results_s1_dataset_scale`: one vector schematic that keeps two branches
   visibly distinct. The property branch reports 1,006 property records, 846
   with original boiling point, and 786 governed admission records. The grid
   branch reports 11 named polymers (PU explicitly excluded), 990 grid
   solvents, 8,974 stored pairs, 28 exact nodes, 251,272 stored cells, and the
   evaluable/refused disposition. Every numeral comes from the Clause 1 SQL;
   no prose count is copied from the owner steer.
2. `results_s1_polymer_pair_coverage`: one horizontal bar axes with the common
   denominator stated as "stored solubility pairs / 990." Nine cohort
   polymers have 990 pairs; NYLON6 and PES have 32 each. PU is absent and the
   caption states the cohort exclusion.
3. `results_s1_solvent_property_domain`: one physical property axes,
   `x = logP`, `y = normal boiling point (°C)`. The 1,006-record property
   catalog is a light neutral layer where both fields exist (846 points).
   Governed admission identities are a dark layer using the effective
   admission boiling point (786 points). There are no marginal axes, inset,
   PCA, UMAP, or safety inference. Stored heat capacity and specific energy
   distributions are deferred to separately named figures if later needed;
   they are not squeezed onto incompatible scales in this axes.
4. `results_s1_fraction_above_threshold_heatmap`: one 11 × 28 quantitative
   heatmap. Rows use the frozen onset order in Clause 4; columns are every
   exact stored node. Each cell is
   `COUNT(runtime-evaluable S >= 5) / COUNT(runtime-evaluable)`. Thus refused
   nonpositive measurements do not silently become insoluble observations.
   The sidecar records all 308 numerators and denominators. Only 25, 50, 75,
   100, 125, 150, and 160 °C are labelled, but all 28 columns are drawn.
5. `results_s1_dissolution_onset_distribution`: one 100%-stacked horizontal
   distribution per polymer. For each stored pair,
   `T5 = MIN(stored temperature_c)` among runtime-evaluable cells with
   `solubility_pct >= 5.0`. The final explicit category is
   `Never by 160 °C`; there is no interpolation or extrapolation. Each row's
   denominator is its stored pair catalog (990 or 32), and the sidecar records
   every onset-bin count.

No Matplotlib colorbar axes is permitted. Quantitative legends are registered
vector swatches/text in the figure margin and do not enter the data rectangle.

## Clause 3 — threshold authority

The 5 wt% value is not taken from this proposal. The generator AST-parses and
asserts both source defaults before aggregation:

- `src/dissolve/tools.py:_screen_direction.solubility_threshold_pct = 5.0`,
  source SHA-256
  `a9ac03d1412d9be6ebef7155683a0bd8a628fbd59d2e77ad9fd547ff001e5d50`;
- `src/dissolve/separation.py:screen_precipitation_order.min_dissolution_solubility_wt_pct = 5.0`,
  source SHA-256
  `f1b05ca8684c6ea6d47b60793f60a5607220f8b7366ccab457931758b9e8f985`.

Neither module is imported and no screen is called.

## Clause 4 — one frozen polymer order

All polymer-row figures use ascending median onset order. For ordering only,
`Never by 160 °C` is an ordinal sentinel after 160 °C; it is never plotted as
a numeric temperature. The exact deterministic order recounted from the
locked DuckDB is:

`PS, PVC, PP, LDPE, PC, HDPE, NYLON6, EVOH, PET, NYLON66, PES`.

The generator recomputes this order from pair-level T5 values and refuses if
the sidecar or any row-oriented figure uses another order. Polymer names are
direct row labels; 11 simultaneous marker shapes and the external polymer
legend are removed.

## Clause 5 — separate supplementary density files

The full stored-solubility landscape is not another point cloud and not a
3 × 4 subplot atlas. It is eleven separately named, single-axes figures:

`results_s1_density_ps`, `results_s1_density_pvc`,
`results_s1_density_pp`, `results_s1_density_ldpe`,
`results_s1_density_pc`, `results_s1_density_hdpe`,
`results_s1_density_nylon6`, `results_s1_density_evoh`,
`results_s1_density_pet`, `results_s1_density_nylon66`, and
`results_s1_density_pes`.

For each file, x is the 28 exact stored nodes. Raw-valid y values use 50
equal-width log10 bins with fixed edges from -8 through 2 (the upper edge is
open); one separate outlined categorical band at the top contains only
`exact_100_artifact` served-ceiling rows. Color is the fraction of that
polymer's runtime-evaluable solvents at that temperature in each raw bin or
the ceiling band, so all 51 rows close to one at every temperature. All eleven
files use identical bin edges, axes ranges, and quantitative legend. Refused
nonpositive rows never enter a log bin. The sidecar records every
per-temperature bin numerator and evaluable denominator. Each density file is
PNG+SVG with vector cells, axes, labels, threshold guide, and legend.

## Clause 6 — deferred assets and owner decisions

No cross-polymer overlap/selectivity matrix is proposed for build yet. The
owner must freeze:

- the stored temperature node or nodes (the examples 80 and 120 °C are not
  silently adopted);
- symmetric Jaccard overlap versus a directional selective-solvent metric;
  and
- the comparison universe for sparse polymers (pairwise common evaluable
  solvent identities versus another explicitly governed denominator).

These choices materially change every matrix cell. A matrix remains blocked
until they are recorded in an admitted proposal. Exact threshold curves and
property-relationship plots are also deferred; if requested, each polymer,
property, and temperature view will be a separately named single-axes file,
not a small-multiple composite.

## Clause 7 — standards, provenance, and must-fire controls

The existing universal 10 pt black typography, exact-one-axes check,
minimum-size checks, closed text/artist/data roles, data-region bounds, and
same-basename PNG+SVG enforcement apply to every file. The generator is
deterministic, read-only, and re-runnable. Each sidecar records absolute source
paths and digests, exact SQL, AST default locations, cohort roster, all plotted
numerators/denominators or bin counts, generator digest, standards report,
output digests, and reproduction command.

The harness must print the following defective cases as expected failures:

1. wrong DuckDB, reader, tools, separation, or property-source digest;
2. PU accidentally entering any 11-polymer cohort figure;
3. treating 251,272 stored cells as 251,272 evaluable cells;
4. treating a refused nonpositive cell as insoluble or density-plottable;
5. omitting a served-ceiling cell or placing it outside the top density bin;
6. changing a heatmap numerator or using stored rather than evaluable cell
   denominators;
7. changing a T5 bin, dropping the never category, interpolating a crossing,
   or changing the frozen row order;
8. misclassifying a catalog, original-BP, or governed-admission identity;
9. changing one density bin edge or failing per-temperature fraction closure;
10. adding a subplot, inset, marginal axes, or Matplotlib colorbar axes;
11. missing PNG, missing SVG, altered output byte, wrong basename, or
    nondeterministic clean generation;
12. unregistered text/data/legend artist, text entering the data region, or a
    mark below the admitted minimum size; and
13. accept-all injections for each new semantic validator, the figure checker,
    and the output checker.

## Clause 8 — owner stops

The build, if admitted, performs only read-only DuckDB SQL and AST/source
inspection. It will not run BioSTEAM; call a screening, route, precipitation,
pairwise, or cool/reheat function; create new engine results; select a
representative pair; edit `src/dissolve/`; touch frozen `thermo.py` or
`tests/test_thermo.py`; modify another worktree; overwrite
`unified_solubility_query.*`; delete the admitted landscape drafts; or merge to
`v12`.
