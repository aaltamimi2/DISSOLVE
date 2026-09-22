# Proposal: reserve white for exact-zero density cells

Status: **proposal only; do not change an admitted figure before standing
auditor-4 issues an explicit `GATE: ADMIT`.** This proposal answers the
owner's direct review of `results_s1_density_pet.png`: exact-zero density cells
currently receive the darkest viridis color and visually overwhelm the sparse
positive distribution.

## Clause 1 — exact scope

Apply one shared rendering rule to the eleven already admitted, separately
named, single-axes density figures only:

`results_s1_density_ps`, `results_s1_density_pvc`,
`results_s1_density_pp`, `results_s1_density_ldpe`,
`results_s1_density_pc`, `results_s1_density_hdpe`,
`results_s1_density_nylon6`, `results_s1_density_evoh`,
`results_s1_density_pet`, `results_s1_density_nylon66`, and
`results_s1_density_pes`.

The dataset-scale, pair-coverage, property-domain, threshold-heatmap, and
onset-distribution figures remain byte-unchanged. There is no composite,
subplot, inset, or new axes.

## Clause 2 — rendering rule

For the density matrix only:

- a cell whose stored fraction is exactly `0.0` is filled white
  (`#FFFFFF`);
- every cell whose fraction is greater than zero retains its current
  quantitative viridis encoding on the fixed `[0, 1]` normalization;
- the legend's `0` swatch is white with the existing black outline; and
- positive legend swatches remain unchanged.

White is not a missing-data code. Every polymer-temperature density column has
an admitted, positive runtime-evaluable denominator, and the 51 bin counts
close to that denominator. Therefore white has one mechanical meaning in these
files: no runtime-evaluable solvent occupies that bin at that stored node.

## Clause 3 — numeric authority and provenance

No number, bin, denominator, threshold, source selection, axes limit, label,
or polymer order changes. Numeric authority remains read-only
`src/dissolve/data/thermodynamics.duckdb`, SHA-256
`4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc`,
with the source/runtime locks already recorded in every sidecar and in
`results_s1_dataset_landscape.py`.

Each revised density sidecar will add an explicit zero-rendering contract:
`zero_fraction_fill = #FFFFFF`, `positive_fraction_colormap = viridis`, and
`normalization = [0, 1]`. Its output and generator digests will be refreshed.
Because the shared generator itself changes, all sixteen sidecars will refresh
their generator digest to remain truthful. For the five non-density figures,
that generator-digest field is the only permitted sidecar change: their PNG,
SVG, output digests, numeric payloads, queries, and all other provenance fields
remain byte-for-byte unchanged.

## Clause 4 — deterministic controls

The standalone harness will print these new defective cases as expected
failures:

1. an exact-zero density cell receives any non-white fill;
2. a positive density cell receives white;
3. the zero legend swatch receives any non-white fill; and
4. a density sidecar omits or changes `zero_fraction_fill`;
5. a density sidecar omits or changes `positive_fraction_colormap` or
   `normalization`; and
6. an injected accept-all density-color/provenance validator makes the harness
   fail.

All existing numeric, closure, one-axes, paired PNG/SVG, vector-only SVG,
artist-registration, provenance, byte-determinism, and accept-all controls
remain required. The harness will expose a semantic density-color/provenance
validator that checks the actual `PolyCollection` face colors against the
measured matrix, the registered zero legend swatch, and all three exact
sidecar contract fields.

## Clause 5 — owner stops

The change is rendering-only. It does not run BioSTEAM or a screen; edit
`src/dissolve/`; alter the DuckDB; select a representative pair; build a
deferred matrix; overwrite `unified_solubility_query.*`; change or delete a
prior non-density output; or merge to `v12`.
