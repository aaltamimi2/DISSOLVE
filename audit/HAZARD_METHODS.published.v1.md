# Hazard Analysis (published Methods)

Sealed measurement tree `1d97a737c231357f87041e42f43ec18de99db869`.
`safety.duckdb` pin `_ASSET_SHA256` `88ce0d09ac28de17045702a8a283de6610b5fe1ab33aa5f90bf6e98edfd75a74`.
Spec `HAZARD_METHODS_SPEC.v1.md` sha256
`b6e0e3b662ba407395e0a913248f3a18c70856959ddbcf025fc1595ced96ce06`.
This file is the published Methods object. It does not edit `safety.py` or
`safety.duckdb`.

## Paragraph

Hazard Analysis. Safety figures come from two places, and the distinction
matters for reproducibility. CAS number, PubChem CID, boiling point, logP,
and green-solvent G-score are stored locally (thermodynamic solvent snapshot
and `safety.duckdb`). Flash point, autoignition temperature, vapor
pressure, GHS classification, and NIOSH and OSHA exposure limits are
retrieved from PubChem when a safety card is built with retrieval enabled,
are not pinned to a snapshot, and appear as nulls on the card; a failed
PubChem heading is named in provenance (`pubchem_failed_headings`) and is
not otherwise distinguished from a heading that returned no value. The
green-solvent screen does not call that builder and does not reach the
network. Route-solvent substitution uses the same builder and retrieves
PubChem unless retrieval is explicitly disabled. GSK G-scores
(`gsk_safety`, `ml_predicted=False`, source string `GSK_dataset.csv`; the
Ram Prasad CSV is a byte-identical duplicate, not a second table) take
precedence over GreenSolventDB (`green_solvent`, `ml_predicted=True`)
where both match. On the 990 solvents in the default screenable roster
(usable-grid intersection; built-in scope `all`), 130 are served from GSK,
840 from GreenSolventDB, and 20 from neither. 128 have a row in both
tables; the two GSK-only screenable solvents are 1,2-dimethoxyethane
(110-71-4) and cis-decalin (493-01-6). On those 128, the mean absolute
difference is 0.30 G-score units (signed mean +0.01). The green screen
then drops solvents below an unsourced G-score floor of 6.0. Heating risk
on a safety card is computed from the stored boiling, flash, and
autoignition values at the operating temperature; missing flash yields
`incomplete` unless boiling-point flags already force high or critical.

## Universe and join

- Enumerating function: `get_available_solvents`.
- Scope token: `all`, origin `built_in`, from `resolve_solvent_scope`.
- Measured n: 990 on the sealed tree named above.
- Served-source join: `_gscore` looks up `gsk_safety` by `lower(name)` or
  exact `cas_number`, `LIMIT 1` with no `ORDER BY`; on a miss it looks up
  `green_solvent` the same way. GSK first is precedence, not a second score.
- Served-source partition of that 990: 130 `GSK_dataset.csv` / 840
  `GreenSolventDB_10k.csv` / 20 neither. 130 + 840 + 20 = 990.
- Both-table hits (same name/CAS rule, ignoring precedence): 128.
- GSK-served minus both-exist leftover keys: `1,2-dimethoxyethane`
  (CAS `110-71-4`) and `cis-decalin` (CAS `493-01-6`).
- Average on the 128 pairs: mean absolute difference 0.30 G-score units;
  signed mean +0.01. Green is `LIMIT 1` with no `ORDER BY`.
- Offline path named: `screen_green_solvent_candidates`. It does not call
  `build_safety_profile` and does not call `_pubchem`.
- Live-by-default path named: `screen_route_solvent_substitutions`
  (`include_pubchem=True`). The same function with `include_pubchem=False`
  does not reach `_pubchem`.
- Card default `get_solvent_safety_card` uses `include_pubchem=True`.
- Default green-screen floor: `minimum_g_score=6.0` from
  `_DEFAULT_MINIMUM_G_SCORE`, `minimum_g_score_source=default`,
  `minimum_g_score_citation_status=unsourced`. No citation is invented.
- Failed vs absent PubChem headings: `provenance.pubchem_failed_headings`
  (and `pubchem_heading_errors`). They are not a distinct `data_gaps` key.
  Failed flash and empty flash are both a missing `flash_point_c` on the
  card; the card text does not distinguish them.
