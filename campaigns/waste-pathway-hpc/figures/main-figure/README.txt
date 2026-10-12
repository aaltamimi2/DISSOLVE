SELF-CONTAINED HPC MAIN-FIGURE REPRODUCTION
Target: hpc-pathway-main-v12, 6.3 x 6.15 inches, 300 dpi.
Intended repository destination: campaigns/waste-pathway-hpc/figures/main-figure/

RUN

Use Python 3.11.9 and install requirements.txt in an environment of your choice:
    python -m pip install -r requirements.txt
    python make_figure.py --data /path/to/campaign/data --out /path/to/output
    python verify.py --out /path/to/output

The renderer reads four tables only: washes_surface, sobol_indices,
mc_candidates and mc_scenarios. Each can be NAME.parquet, a directory named
NAME.parquet containing Parquet parts, or a directory named NAME containing
parts. Nested parts are discovered recursively and queried through DuckDB.
The data directory is read-only. No earlier figure scripts, drawings, CSVs,
source checkout, BioSTEAM installation, network access or model rerun is needed.
Do not choose this package or the data directory as --out: use a separate output
directory. Repeated runs replace the same output filenames.

Only make_figure.py renders the figure. It builds the schematic, plots and
composition directly; there is no sequence of earlier SVG patches. reference/
is used exclusively by verify.py. The renderer reads the bundled icon, fonts,
caption templates and reference fingerprints in assets/, not reference CSV/SVG
files. Matplotlib/fontconfig caches and DuckDB temporaries go inside --out/.cache.

OUTPUTS

hpc-pathway-main.{svg,pdf,png}: composed publication figure.
schematic, wash-cost-surface, sobol-msp, sobol-gwp, pathway-pareto,
profit-advantage and panel-1-wash-cost-sensitivity: individual SVG/PDF/PNG files.
Every data panel has a .data.csv and .sql. Sobol CSVs retain all factors, not just
the four bars. The Pareto CSV retains out-of-view candidates and recomputed front
membership. The profit CSV contains both group maxima and their difference for
every scenario. profit-summary.json holds unrounded summary statistics.
hpc-pathway-main.caption.txt: complete technical caption.
hpc-pathway-main.manuscript-caption.txt: concise v12 manuscript caption.
run.json: source table paths/hashes, counts, dimensions and settings.
verify.py writes verification.json, svg-geometry-differences.json and a PNG
difference image inside --out. It exits nonzero if any data comparison fails
or a required rendering cannot be read. Visual differences are reported rather
than hidden; they do not alone change the exit code.

WHAT THE FIGURE SHOWS

The top row is an illustrative question, the selective-solvent/process-costing/
route-enumeration/Pareto workflow, and one-at-a-time wash sensitivity. It is not
a logged agent execution. The middle row shows LDPE/toluene minimum selling
price and supplied global Sobol sensitivity. Sobol calculations were performed
separately in the campaign; tea_tornado did not generate these Sobol results.
The bottom row shows the resale-excluded profit/emissions front and the profit
advantage of 2-3 washes over 0-1 wash. Negative differences favour simpler
processing; positive differences favour additional washes. They are profit
differences, not absolute profitability.

SETTINGS

The marked block at the top of make_figure.py contains the edit points:
- POLYMER, SOLVENT, ENERGY_CASE: heat-map filters and middle-row description.
  ENERGY_CASE also filters MC scenarios. Sobol tables have no energy-case
  column: SOBOL_ENERGY_CASE records their known case and must agree.
- FEED, LOCATION, BASE_PLANT: scenario selection for the baseline front.
- PLANTS: the two MC plant keys and their visible labels. The present layout
  is a comparison of two plants. Update capacity labels to match your model.
- FEED_DESCRIPTION: explanatory caption metadata. Feed composition is not
  inferred from the four tables. QUERY is independently editable.
- EXCLUDE_RESALE: removes downstream_key='resale' before every optimization.
- SIMPLE_WASHES and MULTIPLE_WASHES: inclusive group bounds for the profit
  difference. Both groups must have candidates in every selected draw.
- PERCENTILES: endpoints for the horizontal sample ranges; median stays 0.5.
- FIGURE_WIDTH_IN, BODY_FONT_PT, HEADING_FONT_PT: final physical width and
  publication text sizes. The original aspect ratio is 1008:984. Default text
  sizes are 7.65 and 7.92 pt, with live SVG text and embedded PDF fonts.
- EMISSIONS_LIMIT_KT: deliberate emissions crop; CSV retains all candidates.
- PARETO_Y_LIMITS, DELTA_X_LIMITS: reference ranges expanded when new values
  require more room. Numeric labels and positions are computed from the data.
- HEATMAP_LIMITS, HEATMAP_TICKS: logarithmic colour range and actual-price
  tick values. Limits expand for new extrema; edit ticks for a new scale.
  The categorical capacity rows and feed-share columns come from the table.
- TOP_SOBOL_FACTORS and FACTOR_LABELS: count and readable factor labels.
  Unknown factors use their column names with underscores replaced by spaces.
- ROUTE_LABELS: tuned label text and offsets from each route's actual point,
  in kt CO2e/year and million USD/year. These reproduce the approved figure;
  they move with the point after a model update. New front members receive
  automatic point-offset labels. MAX_FRONT_LABELS caps annotation density;
  all front points remain plotted. Inspect long new labels and retune offsets
  if they collide; arbitrary future label lengths cannot be guaranteed to fit.
- QUERY, DAG_LABELS, TOOL_LABEL, SENSITIVITY_TOOL_LABEL,
  SENSITIVITY_DESCRIPTION, OUTPUT_LABELS: schematic text. The diagram is drawn
  from source knowledge, not discovered by introspection. Longer text or more
  nodes needs a layout review; the current layout has four workflow nodes.
- FORMULA: visible delta formula; the default letters are defined in captions.
- JITTER_SEED: deterministic vertical jitter, purely for visibility.
- BASE_RECOVERY_PERCENT and RECOVERY_RANGE_PERCENT: recorded assumptions that
  must be reviewed after model updates, not inferred numerical results.

DATA AND QUERIES

The exact default SQL is documented in queries/ and each run writes the SQL
actually used. Queries contain table names only, never machine-specific paths.
1. wash-cost-surface: washes_surface, filtered by cfg_target_polymer, cfg_solvent
   and cfg_energy_case; ordered by capacity then target share. Fields: id, ok,
   target share, capacity, msp_usd_per_kg, lca_status, error. No interpolation.
2. sobol-msp and sobol-gwp: sobol_indices, filtered by polymer/solvent and metric
   msp_usd_per_kg or gwp_kg_co2e_per_kg; ordered by ST descending then factor.
   The first TOP_SOBOL_FACTORS rows are drawn with ST_low/ST_high whiskers.
3. pathway-pareto: join mc_candidates and mc_scenarios on scenario_id/location;
   select FEED, BASE_PLANT, LOCATION, ENERGY_CASE, draw IS NULL, then exclude
   resale. Sort by candidate label for stable CSV order. Discard supplied on_*
   flags, which belonged to the full candidate set. Recompute the front by
   sorting emissions ascending/profit descending and retaining profit records
   with the original relative 1e-9 improvement tolerance. Duplicate objective
   points use the first record in the stable order, as in the approved figure.
4. profit-advantage: same join, selected feed/location/energy and two plants,
   draw IS NOT NULL, resale excluded. Aggregate maximum annual profit in each
   wash-count group by scenario/location/plant/draw/annual feed tonnes. Subtract
   simpler-route profit from multiple-wash profit and divide by feed tonnes.
   Default data give 300 draws per plant; there is no hard-coded sample count.

SCIENTIFIC ASSUMPTIONS THAT MUST TRAVEL WITH THE FIGURE

- Routes selling the residual as resin are excluded because purity was not
  established; this includes residuals remaining after washing.
- The 20,000 t/year case still processes the same 8,000 t/year example feed.
  It assumes other feeds use shared capacity and allocates the example feed
  its throughput share of annual plant costs. Other feeds are not explicitly
  simulated here. A larger but underutilized plant need not realize this benefit.
- The original downstream cost reconstruction includes zero operating costs
  for incineration and gasification; pyrolysis has no revenue. These structural
  assumptions require review independent of Monte Carlo sampling precision.
- Emissions are partial gate-to-gate model estimates, without displaced virgin
  resin credit. Profit-optimal routes are not environmental optima. The screen
  does not establish final product purity; circularity is a normalized index.
- The owner-confirmed base solvent recovery is 99.9999% per pass, effective
  loss 0.0001%. The legacy configured field says 0.01% loss; its conversion is
  intentional and is not corrected by this package. Recovery varied only from
  99.9999% to 99.99% in the original sensitivity runs. Do not extrapolate weak
  loss sensitivity to broader recovery ranges. Surface loading is fixed;
  Sobol and MC loading varies.
- Original screening: target solubility >=10 wt%, retained polymers <=3 wt%,
  cooled target <=1 wt%. C1 means on-site boiler and turbogenerator.
- Original Sobol design: Saltelli N=1,024, 17 factors, 200 bootstrap resamples;
  whiskers are supplied 95% bootstrap intervals. Total-order indices include
  interactions and need not sum to one. MC intervals are 5th-95th sample
  percentiles, not confidence intervals or empirical real-world probabilities.
- The two plant cases use paired sampled assumptions. The package reads these
  samples; it does not regenerate the sampling design or rerun BioSTEAM.

SOURCE OF THE DAG AND ICON

Campaign data commit: b19301685cf5b7dea269c404a57ea9112b97e3d3.
DAG source checkout: 2cf82c6b4455a7aa32fbae20b125b0418633a638.
assets/dag-source.json preserves file hashes, line spans and relevant excerpts:
  agent.py 124-125, 885-889: tool registration and pathway behavior.
  waste_pathway.py 287-307: interface; 379-386: screening; 476-490: output/fronts.
  waste_pathway_data.py 374-397: screen; 465-485 and 580-595: process costing;
    617-632: route enumeration.
  waste_pathway_milp.py 371-381 and 405-421: enumeration/selection;
    498-514: Pareto calculations.
  agent.py 914-926 and tea_tornado.py 120-134, 247-270, 301-311: wash sensitivity.
All paths above are under src/dissolve/ in that source checkout. The schematic
is a source-derived illustration, not derived at runtime from those files.

assets/icon-tea-lca-plant.svg is the exact campaign icon used in v12: the
session's approved hard-hat robot with its book foreground removed and a vector
process-plant/leaf foreground added for this campaign. It was copied from the
campaign's icon-tea-lca-plant.svg. No other figure assets or benchmark material
are required or included. Treat the supplied illustration as a project asset;
this package does not invent a new license for it.

ENVIRONMENT AND FONTS

requirements.txt pins Python-package versions used for acceptance; Python is
3.11.9. environment.json records OS, hashes, package versions and native library
versions. Tested native Cairo: 1.18.0; Matplotlib's bundled FreeType: 2.6.1.
Linux needs Cairo, fontconfig and FreeType runtime libraries (typically
libcairo2, libfontconfig1, libfreetype6). Other platforms are not acceptance-tested.
The two supplied fonts are DejaVuSans.ttf and DejaVuSans-Bold.ttf, DejaVu Sans
2.37. They are bundled, including their distribution copyright/license notice,
so a fresh clone needs no separate font download. They can also be obtained
from the DejaVu fonts distribution or a system fonts-dejavu-core package.
The renderer generates a private fontconfig configuration pointing at the
bundled fonts. Matplotlib uses its own installed DejaVu layout fonts from the
pinned Matplotlib distribution. Do not substitute fonts if exact matching is
required. Pixel equality can depend on native Cairo and font rasterization.

VERIFICATION AND KNOWN SVG DIFFERENCES

The acceptance run produced byte-identical data CSVs, identical caption text,
identical SVG text content and zero differing PNG pixels against v12.
The SVG XML itself is not identical. Of 1,711 compared graphical primitives,
35 have different attribute strings:
  6 are equivalent numeric formatting (120 vs 120.0 and text y coordinates).
  28 schematic primitives directly contain the final y coordinates instead of
     inheriting the old translate(0,-26) group. Their displayed positions match.
  1 is the white background behind the shared-plant label: its right edge is
     267.554406 instead of the old hand-patched 267.93591015625 SVG units.
     This 0.3815-unit difference is white over white and changes no PNG pixels.
Generated element IDs, nested group prefixes, metadata and PDF creation dates
can also differ and have no scientific or visual meaning. The verifier records
the actual primitive differences and full pixel difference rather than claiming
byte-identical vector files. Each SVG retains editable live text.

AFTER A MODEL OR TABLE UPDATE

1. Export the same four table schemas, as files or partition directories.
2. Review the settings, scientific assumptions, query, labels and DAG citations.
3. Run make_figure.py into a new output directory. The new values determine the
   surface, factor ranking, front, distributions, counts, quantiles and ranges.
4. If extracted values or caption-relevant settings differ from the frozen
   reference, numerical captions are regenerated and CAPTION_REVIEW_REQUIRED.txt
   is written. Review assumptions and updated annotations before publication.
   Frozen v12 captions are used only when data and relevant settings match.
5. verify.py should report data differences and exit nonzero after a deliberate
   model update. This is expected; inspect the report rather than overwriting
   reference/ to conceal differences. Keep a separate reviewed new baseline.

A test with partitioned tables, fewer surface cells, 12 draws per plant, changed
MSP/Sobol values and altered multiwash profits completed successfully; the
verifier correctly rejected it as a different dataset. New long route names
may need manual annotation offsets. This package provides no model validation
and does not certify scientific assumptions after the model changes.

PORTABILITY / WRITE CONFINEMENT

The package was copied outside the figure lane and run there with --data and
--out. An independent Linux Landlock wrapper restricted all persistent writes
to --out; /dev/null was permitted solely as a subprocess sink for Cairo library
discovery. Python-level write paths were also audited. Rendering and verification
passed under this restriction. strace was unavailable because this environment
forbids ptrace, so kernel Landlock confinement was used instead. Details and
commands are in DONE.json and acceptance/. Original figure directories were
left untouched and their manifests were rechecked.
