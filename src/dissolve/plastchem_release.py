"""Promote a sealed PlastChem openCOSMO-RS release into DISSOLVE's asset. The one writer of that asset; the
contaminant screens only read it.

    python -m dissolve.plastchem_release /mnt/r/plastchem-euler/promotion-v1   (writes data/plastchem_opencosmo.duckdb)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

from .contaminants import _MISCIBLE_BASIS, _PLASTCHEM_ASSET, _SERVED_CONVENTION, _key


def promote_opencosmo_release(
    release: str | Path, out: str | Path | None = None, *, allow_preview: bool = False,
) -> dict[str, Any]:
    """Build the PlastChem asset from a campaign release. Every file must match the release manifest, and a partial
    preview is refused unless allow_preview (development only; the asset then says so)."""
    release, target = Path(release), Path(out or _PLASTCHEM_ASSET)
    manifest = json.loads((release / "manifest.json").read_text())
    for name, entry in manifest["files"].items():
        if hashlib.sha256((release / name).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{name} does not match the release manifest")
    if manifest.get("status") != "complete" and not allow_preview:
        raise ValueError(f"release status is {manifest.get('status')!r}, not complete")
    part, tmp = str(release / "partition.parquet"), target.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(
        """CREATE TABLE contaminants AS SELECT CAST(row_number() OVER (ORDER BY input_inchikey) AS INTEGER) AS id,
               input_inchikey AS inchikey, name, NULLIF(smiles, '') AS smiles,
               NULLIF(cas, '') AS cas, NULLIF(plastchem_id, '') AS plastchem_id,
               TRY_CAST(molecular_weight_g_mol AS DOUBLE) AS molecular_weight, tier,
               campaign_status_at_snapshot AS status, NULLIF(perceived_inchikey, '') AS perceived_inchikey,
               NULLIF(COALESCE(NULLIF(failure_mode, ''), NULLIF(exclusion_reason, '')), '') AS reason,
               input_inchikey IN (SELECT DISTINCT input_inchikey FROM read_parquet(?)) AS computed
           FROM read_csv_auto(?, all_varchar=true)""",
        [part, str(release / "contaminants.csv.gz")],
    )
    con.execute(
        """CREATE TABLE partition AS SELECT c.id, product_solvent_key AS solvent, campaign_polymer AS polymer,
               max(logP_concentration) FILTER (WHERE convention = 'normalized') AS logp,
               max(logP_concentration) FILTER (WHERE convention = 'existing') AS logp_existing,
               max(r.status) FILTER (WHERE convention = 'normalized') AS status
           FROM read_parquet(?) r JOIN contaminants c ON c.inchikey = r.input_inchikey
           GROUP BY ALL ORDER BY c.id, polymer, solvent""",
        [part],
    )
    con.execute(
        """CREATE TABLE lle AS SELECT c.id, product_solvent_key AS solvent, lower(temperature_regime) AS regime,
               temperature_K - 273.15 AS temperature_c, r.status, value_validated AS validated,
               solute_wt_percent_solubility AS wt_percent, CASE WHEN value_validated THEN above_15_wt_percent END AS miscible
           FROM read_parquet(?) r JOIN contaminants c ON c.inchikey = r.input_inchikey
           ORDER BY c.id, solvent, regime""",
        [str(release / "binary-lle.parquet")],
    )
    con.execute(
        """CREATE TABLE polymers AS SELECT product_polymer_key AS product, campaign_polymer AS campaign,
               shared_model_multiple_materials AS shared_model, conformer_count AS conformers,
               legacy_solubility_available AS legacy_grid, mapping_note AS note
           FROM read_csv_auto(?)""",
        [str(release / "polymer-product-map.csv")],
    )
    rows = con.execute("SELECT id, inchikey, name, cas, plastchem_id, perceived_inchikey FROM contaminants").fetchall()
    aliases = {(_key(value), cid) for cid, inchikey, name, cas, pid, perceived in rows
               for value in (name, cas, inchikey, perceived, pid and f"plastchem {pid}") if value}
    con.execute("CREATE TABLE aliases (alias VARCHAR, id INTEGER)")
    con.executemany("INSERT INTO aliases VALUES (?, ?)", sorted(aliases))
    meta = {
        "release_status": manifest.get("status"), "cohort_sha256": manifest.get("cohort_sha256"),
        "manifest_sha256": hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest(),
        "served_convention": _SERVED_CONVENTION, "miscibility_basis": _MISCIBLE_BASIS,
        "logp_temperature_c": "25.0", "parameterization": "openCOSMO-RS 24a",
    }
    con.execute("CREATE TABLE metadata (key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(meta.items()))
    counts = dict(zip(("contaminants", "computed", "partition", "lle"), con.execute(
        """SELECT (SELECT count(*) FROM contaminants), (SELECT count(*) FROM contaminants WHERE computed),
                  (SELECT count(*) FROM partition), (SELECT count(*) FROM lle)""").fetchone()))
    con.close()
    tmp.replace(target)
    return {**counts, **meta, "asset": str(target)}


if __name__ == "__main__":
    print(json.dumps(promote_opencosmo_release(sys.argv[1], allow_preview="--preview" in sys.argv), indent=2))
