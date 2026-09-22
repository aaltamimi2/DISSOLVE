# Results §1 plottable-artifact inventory

Inventory performed on 2026-08-26 by `codex-v12-builder-4` in
`/home/aaltamimi2/dissolve-v12-visuals` at engine/worktree commit
`b06ee152ced407e796829b04701de91c62feb63f`. This is an availability report,
not a figure proposal and not an authorization to build a figure.

## Governing inputs

| Path | SHA-256 | Role |
|---|---|---|
| `/home/aaltamimi2/dissolve-v12-audit/V4_VISUALIZATION_CHARTER.md` | `3787f3ff4032a1d9cdbe53d94c841b08b985eb9bc8d59c076a5f674106a06e60` | Builder/auditor rules, including the gated loop and non-negotiables |
| `/home/aaltamimi2/dissolve-v12-audit/RESULTS_S1_BRIEF.md` | `321c20cfb1849b7dbc562edf7d8c8313dc15075a1a8a80ee17c32d75b17e85ff` | Names the five requested figure subjects; explicitly not an analysis specification |

## Existing engine artifacts and symbols

### Stored thermodynamic grid — directly plottable

- Artifact: `src/dissolve/data/thermodynamics.duckdb`
- SHA-256: `4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc`
- Runtime reader: `src/dissolve/thermodynamics.py`
- Reader SHA-256: `2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204`
- Named read-only symbols: `get_solubility_result`, `get_solubility_curve`,
  `get_available_pairs`, and `_grid_nodes`.
- Registered query: `src/dissolve/tools.py:solubility_query` (source SHA-256
  `a9ac03d1412d9be6ebef7155683a0bd8a628fbd59d2e77ad9fd547ff001e5d50`).

A read-only schema/count inventory found 253,456 stored `solubility_grid` rows,
12 polymer identities, 990 solvent identities, 28 temperature nodes, and 9,052
stored polymer-solvent pairs. The full Cartesian envelope is 332,640 cells, so
79,184 cells are not measured. Raw asset disposition is 248,378 valid rows and
5,078 flagged rows (`4,096 exact_100_artifact`, `982 nonpositive`). The runtime
reader explicitly reclassifies `exact_100_artifact` as available, yielding
252,474 evaluable cells and 982 measurement-rejected cells. Pair coverage is
990 solvents for nine polymers, 78 for PU, and 32 each for NYLON6 and PES.

These are stored values and stored dispositions, not a newly computed engine
analysis. A coverage panel or explicitly named stored pair curves can therefore
be rendered from this artifact without manufacturing missing results. The
brief has not yet selected which pairs are scientifically representative.

### Separation-analysis functions — callable, but not persisted §1 results

The following registered functions exist at source commit `b06ee15`:

| Subject | Path and symbol | What the returned payload can contain |
|---|---|---|
| Single-step screen | `src/dissolve/tools.py:screen_polymer_separation` | Target/off-target solubilities, selectivity, solvent and temperature candidates |
| Pairwise diagnostic | `src/dissolve/tools.py:screen_pairwise_solubility_overlap` | Pair rank, maximum gap, and each pair's best discriminating condition |
| Route planning | `src/dissolve/separation.py:plan_multistage_separation` | Ranked routes, stage conditions, bottleneck selectivity, cumulative off-target burden, peak temperature, branch/beam settings |
| Cooling screen | `src/dissolve/separation.py:screen_precipitation_order` | Dissolution point, two threshold crossings, ordering window, and recovery window |
| Cool/reheat extension | `src/dissolve/separation.py:screen_cool_then_reheat_getter` | Named dissolve/cool/reheat state values and explicit limitations |

`src/dissolve/separation.py` has SHA-256
`f1b05ca8684c6ea6d47b60793f60a5607220f8b7366ccab457931758b9e8f985`.
Function availability is not artifact availability: invoking one now to obtain
missing manuscript values would be a new engine result, prohibited by the
charter until the §1 spec authorizes and records the run.

## Existing audit artifacts

### `audit/PLANNER_SURFACE.v1.json`

- Path: `audit/PLANNER_SURFACE.v1.json`
- SHA-256: `db31e21c99045b98508f6046f778381239692d25306d43abbbb948b6ad916818`
- Reproducer: `audit/measure_planner_surface.py`
- Reproducer SHA-256: `e8ea21996d887ab308329ffe9c6ad265bbdbafd68d909068fef7fad243f5029f`
- Artifact's recorded engine SHA: `344bb148ab4b34bb7cf3198aae5150bf402bd319`
- Artifact's recorded measurement time: `2026-08-21T17:00:19Z`

This audit artifact preserves:

- one best route summary for the five-polymer feed
  `[LDPE, PP, PS, PET, NYLON66]`, including four stage solvent/temperature/
  selectivity rows and the final residue;
- the best sequence for three LDPE/PP search settings (default count breadth,
  a selectivity window, and the `all` breadth token);
- one LDPE/PP precipitation-screen summary containing a recommended solvent
  and only the first precipitation-proxy temperature; and
- a named cool/reheat probe with warnings, but not the state values needed for
  a figure.

It is not sufficient for the requested publication panels. The route extract
omits the explicit bottleneck-selectivity and cumulative-off-target values for
the leading alternatives; it contains no runner-up stage package. The breadth
extract contains no alternative counts or diversity measure. The cooling
extract omits the second crossing, ordering window, recovery window, and curve
values. Re-running its producer would compute current engine results and is not
authorized by this inventory task.

### External gate baselines

Several external regression baselines are themselves plottable thermodynamic
snapshots. They reinforce the thermodynamic-landscape readiness finding but do
not supply the four missing derived analysis packages:

- `/home/aaltamimi2/dissolve-v12-audit/gate_query_baseline.json`, SHA-256
  `e907e95c8a68a945a4e5976db5d293abf921931f1d5aace360977e5f26abae77`.
  It contains identity-keyed LDPE/dodecane point and 100–140 °C curve records,
  a complete 28-node PP/dodecane curve, and ranked dodecane snapshots. Its
  producer is `gate_query.py`, SHA-256
  `4844bb707d566b1f3d159b07a73c1de012f83f90f61bce69e0577dbf52809701`.
- `/home/aaltamimi2/dissolve-v12-audit/gate_consolidate_baseline.json`,
  SHA-256
  `bfefd264e1b694e116e07756fca50964165975a0cfb81fa0baafb62cdd75eb01`.
  It contains direct 28-row LDPE/dodecane and PP/dodecane curves plus point,
  ranking, pagination, and refusal snapshots. Its producer is
  `gate_consolidate.py`, SHA-256
  `f146194a6c43921664ae8f61348f76bea13003f33a146d69a7c26d0cb5722a8d`.
- `/home/aaltamimi2/dissolve-v12-audit/gate_saturation_baseline.json`,
  SHA-256
  `3f266dc7dcb7da2f4549e80841d5a9a8821ab38593b73a2d7ee72ef39a137e5e`.
  Its `cells` map preserves 248,378 previously servable identity-keyed grid
  cells; the gate separately requires the 4,096 saturated cells to become
  reachable without moving those values. Its producer is `gate_saturation.py`,
  SHA-256
  `7f20fc8923626803660b0a50c820717edc2c0ef8676ced30b5123a8f7658d93a`.
- `/home/aaltamimi2/dissolve-v12-audit/gate_refusal_baseline.json`, SHA-256
  `0d93ad01e98a6faf857e8053d1113ea46a135ca1efbcee428bba324e3a036131`.
  It contains numeric-leaf snapshots for served point/curve, pairwise, route,
  and precipitation calls plus typed refusal summaries. Its producer is
  `gate_refusal.py`, SHA-256
  `979e3794d207dd4582ccbd01ccd888192b0a78f65073dc44ad8bf241630da032`.
- `/home/aaltamimi2/dissolve-v12-audit/gate_roster_baseline.json`, SHA-256
  `5b7a9c87d46c61ab2f030494087e41433d23815cea910ac1792aad023b286ec7`.
  It contains numeric-leaf point, solvent-rank, and single-step separation
  snapshots used to protect roster behavior. Its producer is `gate_roster.py`,
  SHA-256
  `73c1ab3098aaeaeea73bda1173950c3874d1125a7fe91cdb4003506f2c96a727`.
- `/home/aaltamimi2/dissolve-v12-audit/gate_design_point_baseline.json`,
  SHA-256
  `bee5068b524f1fdc64c575fa81581bdf09a96eb47fae5774e1f66e217c989ddd`.
  It contains numeric leaves for two exact one-step consumed routes (including
  bottleneck selectivity and off-target burden) coupled to TEA design-point
  checks. String identities are omitted and neither record is a multistage
  leading-pathway package. Its producer is `gate_design_point.py`, SHA-256
  `9f896f7c4d555995c32b3e20dfc2a8f4bff67f361627447163ae60c6c6b42b3d`.

`/home/aaltamimi2/dissolve-v12-audit/gate_2a_baseline.json` (SHA-256
`d03cb6be5ce34c0c34105c4b3886b345a3e3b39db814c49eb92bb578aed96917`)
contains numeric-leaf regression snapshots for one LDPE/PP pairwise call, one
LDPE/PP route call, and one LDPE/PP precipitation call. Its producer is
`/home/aaltamimi2/dissolve-v12-audit/gate_2a.py` (SHA-256
`6a294eb07ae309200fa14fce0cd0d11a6beb2578e558af3626e00d0599bc7406`).
The baseline deliberately strips string leaves, including solvent identity,
and is a value-preservation gate rather than a publication result artifact.

`/home/aaltamimi2/dissolve-v12-audit/gate_nofilter_baseline.json` (SHA-256
`365c31c891a2262785819f78d023d54a6c2e35be5fa959b1ca38639eeab759a0`)
contains numeric-leaf snapshots for an exact LDPE/dodecane/140 °C query and an
LDPE/all-solvents/140 °C page. Its producer is
`/home/aaltamimi2/dissolve-v12-audit/gate_nofilter.py` (SHA-256
`8421452577909a3fd8e7fdf6e3e48c52b9373a9789d5f24803c837e22dc28067`).
It is useful regression evidence but does not supply representative curves.

After dispositioning the relevant gate baselines above, no inventoried artifact
contains a complete all-polymer separability matrix, a complete multi-route §1
result package, a complete cooling ladder, or a breadth-sensitivity series.
Literature and planning Markdown files are briefs or source discussion, not
settled numeric result artifacts.

## Figure readiness today

| Named figure | Status today | Reason |
|---|---|---|
| Thermodynamic landscape / representative solubility behaviour | **Raw-data drawable; final figure not yet specified** | The immutable grid supplies coverage and stored pair curves. The §1 spec still must name the representative pairs/panels and their claim before the gated proposal can be admitted. |
| Multistage separation pathway | **Cannot be drawn to the brief** | The only persisted route summary is one older best-route extract and omits the required leading-pathway metrics and alternatives. |
| Selective precipitation / cooling behaviour | **Cannot be drawn to the brief** | A partial audit summary exists, but the two crossings, ordering separation, target cooling interval, and plotted curve/state values are not persisted together. The PET/LDPE p-xylene example remains an unvalidated candidate. |
| Polymer-polymer separability matrix | **Cannot be drawn to the brief** | The function exists and one pair has a numeric regression snapshot, but no full admitted matrix artifact exists; whole-feed success is also unverified. |
| Breadth sensitivity | **Cannot be drawn to the brief** | Three search modes have only best-sequence summaries. No admitted series records route count, route diversity, or the downstream ranking alternatives requested by the brief. |

Thus one figure subject has stored data that can be rendered today, and four do
not have complete plottable artifacts. None may be built as a final publication
figure until the v1 specification is released and a figure/panel/source
proposal receives clause-by-clause `GATE: ADMIT`.
