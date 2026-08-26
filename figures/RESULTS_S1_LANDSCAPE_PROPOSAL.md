# Pre-build proposal: Results §1 stored-solubility landscape drafts

Status: **submitted to `codex-v12-auditor-4`; nothing in this proposal may be
built before clause-by-clause `GATE: ADMIT`.** These would be additional first
drafts, not final publication figures. The prior coverage drafts remain
unchanged.

## Clause 1 — scope and outputs

Build two separately named, single-axes figures:

1. `results_s1_solubility_scatter`: every runtime-evaluable stored
   `solubility_pct` value against its stored temperature node, with polymer
   identity encoded by colour and marker and `exact_100_artifact` cells encoded
   as a distinct open mark.
2. `results_s1_dissolving_fraction`: for every polymer and stored temperature
   node, the fraction of that polymer's stored solvent catalog whose cell is
   runtime-evaluable and has `solubility_pct >= 5.0`.

Each figure is exported as a same-basename PNG and SVG plus a provenance JSON.
Neither figure contains a subplot, inset axes, selected polymer–solvent curve,
or named-pair filter. Existing `unified_solubility_query.*` and both earlier
coverage figures are not modified or replaced.

Proposed new files after admission:

- `figures/results_s1_landscape.py`
- `figures/results_s1_solubility_scatter.png`
- `figures/results_s1_solubility_scatter.svg`
- `figures/results_s1_solubility_scatter.provenance.json`
- `figures/results_s1_dissolving_fraction.png`
- `figures/results_s1_dissolving_fraction.svg`
- `figures/results_s1_dissolving_fraction.provenance.json`
- `figures/test_results_s1_landscape.py`

## Clause 2 — numeric authorities and digest locks

The generator refuses before SQL if any numeric/semantic source has moved:

| Path | SHA-256 | Role |
|---|---|---|
| `src/dissolve/data/thermodynamics.duckdb` | `4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc` | Stored polymer, solvent, temperature, solubility, and raw disposition values |
| `src/dissolve/thermodynamics.py` | `2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204` | Runtime evaluable rule: `is_valid OR invalid_reason = 'exact_100_artifact'` |
| `src/dissolve/tools.py` | `a9ac03d1412d9be6ebef7155683a0bd8a628fbd59d2e77ad9fd547ff001e5d50` | `_screen_direction` default `solubility_threshold_pct=5.0` and threshold comparison |
| `src/dissolve/separation.py` | `f1b05ca8684c6ea6d47b60793f60a5607220f8b7366ccab457931758b9e8f985` | `screen_precipitation_order` default `min_dissolution_solubility_wt_pct=5.0` |

The generator reads the two Python sources with `ast` and asserts both named
defaults remain exactly 5.0; it does not import or call either screen.

The landscape steer is non-numeric framing only:
`/home/aaltamimi2/dissolve-v12-audit/RESULTS_S1_LANDSCAPE_FIGURE_STEER.md`,
SHA-256
`150faa3d3219fc710afbc3164bfec30a6d8f4f499a3ff0aa696681c446f8d3c0`.
The current inbox digest is
`3eb6ae9176b8e826af0ec35514262babb49466f7117259a494a98bbaa6536ceb`
and the current charter digest is
`f6f0a7e0d3d948e66a27202b285d924590ea0bafff39f2475462ca33f45a1a40`.
They are recorded as governing authorities, never used as number sources.

## Clause 3 — independent recount and readiness

The builder re-counted directly from the locked DuckDB before writing this
proposal. The scatter source query will be:

```sql
SELECT polymer, solvent, temperature_c, solubility_pct,
       invalid_reason = 'exact_100_artifact' AS is_served_ceiling
FROM solubility_grid
WHERE is_valid OR invalid_reason = 'exact_100_artifact'
ORDER BY polymer, solvent, temperature_c;
```

The recount returns 252,474 evaluable cells: 248,378 raw-valid and 4,096
`exact_100_artifact`. All evaluable values are positive; the stored range is
`1e-8` through `100.0` wt%, and all stored values exactly equal to 100 have the
`exact_100_artifact` disposition. There are 96,325 evaluable cells at or above
5.0 wt%. These facts are assertions re-derived by the eventual generator, not
numbers accepted from the steer.

The fraction query will deliberately preserve the fixed stored-solvent
denominator:

```sql
SELECT polymer, temperature_c,
       COUNT(DISTINCT solvent) AS stored_solvent_denominator,
       COUNT(*) FILTER (
           WHERE (is_valid OR invalid_reason = 'exact_100_artifact')
             AND solubility_pct >= 5.0
       ) AS dissolving_numerator,
       dissolving_numerator::DOUBLE / stored_solvent_denominator
           AS dissolving_fraction
FROM solubility_grid
GROUP BY polymer, temperature_c
ORDER BY polymer, temperature_c;
```

This follows the brief's prose requirement that the denominator is *that
polymer's stored solvents*: 990 for the nine dense polymers, 78 for PU, and 32
for NYLON6 and PES at every node. A `nonpositive` stored cell stays in its
polymer's denominator but cannot enter the numerator because it is not
runtime-evaluable. This intentionally does **not** use the steer's illustrative
`WHERE evaluable` / `AVG(...)` query, whose denominator would shrink at nodes
with refused measurements and contradict the stated 78/32/32 rule. Auditor-4
must rule explicitly on this interpretation before build.

The recount yields exactly 12 × 28 = 336 fractions. Every polymer series is
nonconstant: the number of distinct fractions ranges from 9 to 28 across the
12 polymers. Thus the brief's stop condition does not fire; the axis separates
stored behavior without changing the threshold or denominator. The figure
will make the descriptive claim only: the share of each stored solvent catalog
meeting the engine-adjacent 5 wt% threshold changes differently with
temperature. It will not claim route feasibility, selectivity, recovery, or an
easy/hard pair.

## Clause 4 — Figure A encoding: all-cell landscape

- Include all 12 polymers and all 252,474 runtime-evaluable cells. Sparse
  polymers are neither omitted nor silently filled.
- x records remain the 28 exact stored nodes. For visibility, use a
  deterministic display-only x dodge by polymer within each 5 °C interval and
  a smaller deterministic solvent jitter derived from SHA-256 of the stored
  solvent identity. The sidecar retains the unshifted stored temperature for
  every source row. The caption/annotation states that horizontal displacement
  is display-only and no intermediate temperature was evaluated.
- Use a logarithmic y axis because the independently recounted positive values
  span ten orders of magnitude. Draw a black 5 wt% reference line and label it
  as the engine-adjacent threshold traced to the two named source defaults.
- Use one frozen 12-entry `(colour, marker)` identity map. The same map is used
  in Figure B. Colour is not the sole identity encoding.
- Raw-valid cells use filled marks. The 4,096 served-ceiling cells use open
  marks at 100 wt% and a legend key that says "served ceiling", not a precise
  100% prediction.
- The complete point cloud is rendered at 300 dpi as a rasterized data layer
  inside the SVG; axes, labels, legend, and threshold remain vector text/paths.
  This avoids a multi-megabyte 252,474-object Illustrator file without
  dropping or sampling any cell. PNG contains the same complete cloud.
- Marker diameter is at least 7 pt but low opacity is used for the dense raw
  cloud. Overplot within the scientific data region is the density encoding;
  no text, legend, title, or annotation may enter that region.

## Clause 5 — Figure B encoding: dissolving fraction

- Plot all 12 polymer series at the same 28 exact stored nodes on y = 0–1.
- A point is drawn at every stored node. Thin connecting lines are visual
  guides only; text states that fractions are computed only at stored nodes.
- Use the same colour/marker mapping and a minimum 7 pt marker diameter.
- Display each sparse polymer's fixed denominator in the legend label:
  `PU (n=78)`, `NYLON6 (n=32)`, and `PES (n=32)`; dense labels may share a
  `n=990` legend note to avoid repeating it nine times. All legend text is 10
  pt black.
- Crossings among data series are admitted as scientific data intersections,
  not layout overlap. Labels and legend remain outside the plotted data
  rectangle. There is no interpolation of missing pairs and no selected
  solvent.

## Clause 6 — checker semantics and must-fire controls

The existing exact-one-axes, PNG+SVG, typography, black-text, title-width,
minimum-size, and closed-artist checks remain. Because both requested plots
necessarily contain data overplot/crossings, the checker gains an explicit
`data_layers` role tied to one registered data-region rectangle:

- a data layer must be registered, clipped, and wholly contained in the data
  region;
- data/data overlap inside that region is permitted as the scientific
  encoding;
- every non-data artist still uses the strict collision rules;
- every text/legend extent must stay outside the data region except axis tick
  labels, whose registered extents stay outside the axes patch;
- scatter sizes and line markers are introspected and must be at least 7 pt;
  and
- broad `structural_artists` registration cannot exempt a user data artist.

Five named mechanical validators close the output and cross-figure contracts:

- `check_svg_raster_contract(svg_path, expected_group="solubility-cloud")`
  parses the SVG XML, requires the tagged Figure A data group to contain an
  embedded raster `<image>`, and rejects a vector point cloud in that group.
  Figure B is separately asserted to retain vector lines/markers.
- `check_polymer_style_contract(scatter_registry, fraction_registry,
  manifest_style_map)` reads each labelled series artist's RGBA colour and
  marker path/name and requires an exact 12-polymer match in both figures and
  the provenance manifests.
- `check_data_layer_bounds(record, renderer)` inspects every source offset or
  line vertex after the artist transform, not merely its clipped visible
  extent, and requires all transformed data coordinates to lie within the
  registered data-region bounds. Thus clipping cannot conceal out-of-region
  source geometry.
- `check_data_role_closed_world(fig, registry)` discovers user-created
  `Collection` and data-bearing `Line2D` artists independently of the supplied
  roles. Each must be registered as a data layer or collision-relevant mark;
  registration only as `structural_artists` is rejected.
- `check_display_displacement(source_rows, rendered_offsets,
  displacement_manifest)` recomputes every polymer dodge and solvent jitter
  from the stored `(polymer, solvent, temperature_c)` identity. It asserts a
  one-to-one row order, exact unshifted node membership, the admitted maximum
  displacement within its 5 °C node interval, and the canonical SHA-256 digest
  of `(source identity, stored x, rendered x)` recorded in the sidecar. This is
  a semantic check independent of output-byte determinism.

New positive checks receive these must-fire counterparts, printed as expected
failures by the harness:

1. Wrong DuckDB, reader, tools, or separation digest fails before SQL.
2. A parsed threshold default other than 5.0 fails before aggregation.
3. Omitting `exact_100_artifact` from evaluable cells fails the 252,474 and
   4,096 recount assertions.
4. Treating the 982 `nonpositive` cells as evaluable fails disposition checks.
5. Replacing a sparse fixed denominator with the evaluable-only count fails
   the 78/32/32 denominator assertions.
6. Changing one numerator or flattening one polymer series fails the 336-row,
   exact-ratio, and nonconstant-series checks.
7. Filtering to a named polymer or solvent fails the all-12/all-cell source
   roster assertions.
8. A served-ceiling cell placed in the filled raw-valid layer fails the
   distinct-layer count check.
9. An unregistered or unclipped scatter/line data layer fails closed-world data
   registration.
10. A data marker below 7 pt fails rendered/introspected size checking.
11. Moving a legend label into the data region fails text/data-region
    clearance.
12. Adding a second axes, omitting PNG, omitting SVG, or changing an output
    byte continues to fail the existing controls.
13. Saving Figure A with `rasterized=False` produces a deterministic SVG but
    fails `check_svg_raster_contract` because the tagged cloud contains vector
    point uses rather than an embedded image.
14. Changing one polymer marker or colour in Figure B only fails
    `check_polymer_style_contract`, even when each figure's own legend remains
    internally consistent.
15. Moving one registered scatter offset outside the data limits while leaving
    clipping enabled fails `check_data_layer_bounds`; a clipped-away bad point
    is not accepted.
16. Registering a scatter or line only in `structural_artists` fails
    `check_data_role_closed_world`.
17. Replacing one source row's SHA-256 jitter with a fixed but deterministic
    offset yields byte-stable output but fails `check_display_displacement` and
    its sidecar displacement digest.

All five validators run in the same harness as the existing standards checks.
An accept-all figure, output, SVG-contract, style-contract, data-bounds,
data-role, or displacement checker must make the harness return nonzero.

## Clause 7 — deterministic outputs and provenance

The generator uses Agg, DejaVu Sans, one 10 pt black text size, a fixed
polymer style map, stable SHA-256 jitter, fixed axes/legend coordinates, fixed
PNG DPI, `svg.fonttype="none"`, fixed `svg.hashsalt`, and fixed SVG metadata.
Two clean temporary generations must be byte-identical for both PNG/SVG pairs
and both provenance JSON files; a deliberately altered byte must fail the same
comparator.

Each sidecar records full source paths and digests, exact SQL, parsed threshold
locations/defaults, the full polymer style map, unshifted x-node semantics,
denominators and numerators for all 336 fractions, per-layer row counts,
the canonical source-to-render displacement digest, generator digest,
standards report, output digests, and the exact reproduction command:

```text
PYTHONPATH=src:. python figures/results_s1_landscape.py
```

## Clause 8 — owner stops

Only read-only DuckDB SQL and AST source inspection are proposed. The build
will not run BioSTEAM; call a screening, route, pairwise, precipitation, or
cool/reheat function; edit `src/dissolve/`; touch frozen `thermo.py` or
`tests/test_thermo.py`; modify the `plastics` package or another worktree;
select representative pairs; overwrite `unified_solubility_query.*`; or merge
to `v12`.
