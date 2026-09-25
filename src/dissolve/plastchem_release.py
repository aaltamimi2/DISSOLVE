"""Promote a sealed PlastChem openCOSMO-RS release into DISSOLVE's asset. The one writer of that asset; the
contaminant screens only read it.

    python -m dissolve.plastchem_release /mnt/r/plastchem-euler/promotion-v1 [--extension RELEASE ...] [--preview]
    (writes data/plastchem_opencosmo.duckdb)

An extension release adds solvents computed later for the same contaminants and polymers, such as the 39 common
solvents beyond the 32-solvent panel. The asset serves the union.

The contaminant families a user can search by ("phthalates", "antioxidants") are built from the PlastChem workbook
and checked against that asset:

    python -m dissolve.plastchem_release --families plastchem_db_v1.0.xlsx   (writes data/plastchem_families.json)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from .contaminants import _MISCIBLE_BASIS, _PLASTCHEM_ASSET, _PLASTCHEM_FAMILIES, _SERVED_CONVENTION, _key


# Plastic additives are asked about by abbreviation ("does DEHP leach"), and PlastChem names them in full. Each
# abbreviation maps by CAS number to the release's own entry (checked by name when this list was made, 2026-09-24);
# an abbreviation whose CAS the release lacks adds nothing. Ambiguous ones (DOP, NP) are left out on purpose.
_ABBREVIATIONS = {
    "DEHP": "117-81-7", "DBP": "84-74-2", "BBP": "85-68-7", "BBzP": "85-68-7", "DINP": "20548-62-3",
    "DIDP": "26761-40-0", "DEP": "84-66-2", "DMP": "131-11-3", "DIBP": "84-69-5", "DEHA": "103-23-1",
    "DEHT": "6422-86-2", "DOTP": "6422-86-2", "DINCH": "166412-78-8", "ATBC": "77-90-7", "TOTM": "3319-31-1",
    "BPA": "80-05-7", "BPF": "620-92-8", "BHT": "128-37-0", "Irganox 1076": "2082-79-3", "Tinuvin P": "2440-22-4",
    "Chimassorb 81": "1843-05-6", "TXIB": "6846-50-0",
}


def _checked(release: Path, allow_preview: bool) -> dict[str, Any]:
    """The release's manifest, once every file matches it and the release is complete (or a preview is allowed)."""
    manifest = json.loads((release / "manifest.json").read_text())
    for name, entry in manifest["files"].items():
        if hashlib.sha256((release / name).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{name} does not match the release manifest")
    if manifest.get("status") != "complete" and not allow_preview:
        raise ValueError(f"release status is {manifest.get('status')!r}, not complete")
    return manifest


def _same_inputs(base: Path, extension: Path) -> None:
    """An extension must describe the same contaminants and polymers as the base; gzip headers may differ."""
    for name, read in (("contaminants.csv.gz", lambda p: gzip.decompress(p.read_bytes())),
                       ("polymer-product-map.csv", lambda p: p.read_bytes())):
        if read(extension / name) != read(base / name):
            raise ValueError(f"{extension.name}/{name} differs from the base release's")


def promote_opencosmo_release(
    release: str | Path, out: str | Path | None = None, *, allow_preview: bool = False,
    extensions: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Build the PlastChem asset from a campaign release and any extension releases. Every file must match its release
    manifest, and a partial preview is refused unless allow_preview (development only; the asset then says so). An
    extension must carry the same contaminants and polymer map, and may add only solvents no other release serves."""
    release, target = Path(release), Path(out or _PLASTCHEM_ASSET)
    releases = [release, *map(Path, extensions)]
    manifests = [_checked(path, allow_preview) for path in releases]
    served: dict[str, str] = {}
    for path in releases:
        if path != release:
            _same_inputs(release, path)
        solvents = {row[0] for row in duckdb.connect().execute(
            "SELECT DISTINCT product_solvent_key FROM read_parquet(?)", [str(path / "partition.parquet")]).fetchall()}
        repeated = sorted(solvents & served.keys())
        if repeated:
            raise ValueError(f"{path.name} repeats solvents {served[repeated[0]]} already serves: {', '.join(repeated)}")
        served.update(dict.fromkeys(solvents, path.name))
    part = [str(path / "partition.parquet") for path in releases]
    lle = [str(path / "binary-lle.parquet") for path in releases]
    tmp = target.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(
        """CREATE TABLE contaminants AS SELECT CAST(row_number() OVER (ORDER BY input_inchikey) AS INTEGER) AS id,
               input_inchikey AS inchikey, name, NULLIF(smiles, '') AS smiles,
               NULLIF(cas, '') AS cas, NULLIF(plastchem_id, '') AS plastchem_id,
               TRY_CAST(molecular_weight_g_mol AS DOUBLE) AS molecular_weight, tier,
               campaign_status_at_snapshot AS status, NULLIF(perceived_inchikey, '') AS perceived_inchikey,
               NULLIF(COALESCE(NULLIF(failure_mode, ''), NULLIF(exclusion_reason, '')), '') AS reason,
               input_inchikey IN (SELECT DISTINCT input_inchikey FROM read_parquet(?, union_by_name=true)) AS computed
           FROM read_csv_auto(?, all_varchar=true)""",
        [part, str(release / "contaminants.csv.gz")],
    )
    con.execute(
        """CREATE TABLE partition AS SELECT c.id, product_solvent_key AS solvent, campaign_polymer AS polymer,
               max(logP_concentration) FILTER (WHERE convention = 'normalized') AS logp,
               max(logP_concentration) FILTER (WHERE convention = 'existing') AS logp_existing,
               max(r.status) FILTER (WHERE convention = 'normalized') AS status
           FROM read_parquet(?, union_by_name=true) r JOIN contaminants c ON c.inchikey = r.input_inchikey
           GROUP BY ALL ORDER BY c.id, polymer, solvent""",
        [part],
    )
    con.execute(
        """CREATE TABLE lle AS SELECT c.id, product_solvent_key AS solvent, lower(temperature_regime) AS regime,
               temperature_K - 273.15 AS temperature_c, r.status, value_validated AS validated,
               solute_wt_percent_solubility AS wt_percent, CASE WHEN value_validated THEN above_15_wt_percent END AS miscible
           FROM read_parquet(?, union_by_name=true) r JOIN contaminants c ON c.inchikey = r.input_inchikey
           ORDER BY c.id, solvent, regime""",
        [lle],
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
    by_cas = {cas: cid for cid, _, _, cas, _, _ in rows if cas}
    abbreviations = {(_key(abbr), by_cas[cas]) for abbr, cas in _ABBREVIATIONS.items() if cas in by_cas}
    aliases |= abbreviations
    con.execute("CREATE TABLE aliases (alias VARCHAR, id INTEGER)")
    con.executemany("INSERT INTO aliases VALUES (?, ?)", sorted(aliases))
    statuses = [manifest.get("status") for manifest in manifests]
    meta = {
        "release_status": next((status for status in statuses if status != "complete"), "complete"),
        "cohort_sha256": manifests[0].get("cohort_sha256"),
        "manifest_sha256": hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest(),
        "releases": json.dumps([
            {"release": path.name, "status": status,
             "manifest_sha256": hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
             "solvents": sum(name == path.name for name in served.values())}
            for path, status in zip(releases, statuses)]),
        "served_convention": _SERVED_CONVENTION, "miscibility_basis": _MISCIBLE_BASIS,
        "logp_temperature_c": "25.0", "parameterization": "openCOSMO-RS 24a",
        "abbreviations": str(len(abbreviations)),
    }
    con.execute("CREATE TABLE metadata (key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(meta.items()))
    counts = dict(zip(("contaminants", "computed", "partition", "lle", "solvents"), con.execute(
        """SELECT (SELECT count(*) FROM contaminants), (SELECT count(*) FROM contaminants WHERE computed),
                  (SELECT count(*) FROM partition), (SELECT count(*) FROM lle),
                  (SELECT count(DISTINCT solvent) FROM partition)""").fetchone()))
    con.close()
    tmp.replace(target)
    return {**counts, **meta, "asset": str(target)}


# --- Contaminant families. Nobody knows the names of 5,830 contaminants, so a user can ask for a family by a plain
# name. Most families are PlastChem's own chemical groups (columns of the workbook's "Full database" sheet). Where the
# plain name needs it, a structure narrows the group: slip agents are its fatty amides, not formamide. PlastChem has no
# antioxidant or UV-stabilizer group, and its function labels are no substitute ("Antioxidant" also tags methanol and
# acetic acid), so those two families are structural classes. Examples must resolve as typed, because a user who
# reads one in the family picker will type it.

_SMARTS = {
    # antioxidants: a phenol with an ortho tert-alkyl or cycloalkyl group, a diarylamine, an N,N'-disubstituted
    # p-phenylenediamine, a 2,2,4-trimethyl-1,2-dihydroquinoline, a gallate, a chromanol (tocopherols), a benzofuranone
    # lactone, and the phosphite and thioester secondary antioxidants
    "hindered_phenol": "[OX2H]c1c([CX4;H0]([CH3])([CH3])[#6;!$(c1ccc([OX2H])cc1);!$(c1ccccc1[OX2H])])cccc1",
    "cycloalkyl_phenol": "[OX2H]c1c([CX4;R1;$([CH1]),$([CH0][CH3])]([#6;R1])[#6;R1])cccc1",
    "diarylamine": "[NX3H1](c1ccccc1)c1ccccc1",
    "phenylenediamine": "[NX3H1;!$(NC=O)]([#6;!$(C=O)])c1ccc(cc1)[NX3H1;!$(NC=O)][#6;!$(C=O)]",
    "dihydroquinoline": "[CX4]1([CH3])([CH3])[CX3]=[CX3]([CH3])c2ccccc2[NX3H]1",
    "gallate": "[OX2H]c1c([OX2H])cc(cc1[OX2H])C(=O)O[#6]",
    "chromanol": "[OX2H]c1ccc2O[CX4]([CH3])CCc2c1",
    "benzofuranone": "O=C1Oc2ccccc2C1c1ccccc1",
    "phosphite": "[PX3](Oc)(O[#6])O[#6]",
    "thiodipropionate": "O=C(O[#6])CC[SX2]CCC(=O)O[#6]",
    # what those patterns also catch that is not an antioxidant: dyes, pigment acids and nitro intermediates (a
    # bisphenol's isopropylidene bridge is not the tert-alkyl group of a hindered phenol; see above)
    "anthraquinone": "O=C1c2ccccc2C(=O)c2ccccc12",
    "ring_aryl_ketone": "c[CX3;R](=O)[#6,#7]",
    "azo": "[#6]N=N[#6]",
    "nitro": "[$([NX3](=O)=O),$([NX3+](=O)[O-])]",
    "nitroso": "[#6][NX2]=O",
    "aryl_acid": "c[CX3](=O)[OX2H1]",
    "dimethylaminoaryl": "c[NX3]([CH3])[CH3]",
    # UV stabilizers: 2-(2-hydroxyphenyl) benzotriazoles and triazines, 2-hydroxybenzophenones, hindered-amine light
    # stabilizers (2,2,6,6-tetramethylpiperidines), cyanoacrylates, oxanilides, aryl salicylates, benzylidene malonates
    "benzotriazole_uva": "[OX2H]c1ccccc1-n1nc2ccccc2n1",
    "triazine_uva": "[OX2H]c1ccccc1-c1ncncn1",
    "hydroxybenzophenone": "[OX2H]c1ccccc1[CX3;!R](=O)c1ccccc1",
    "hals": "[CX4]1([CH3])([CH3])[CX4][CX4][CX4][CX4]([CH3])([CH3])[NX3]1",
    "cyanoacrylate": "N#C[CX3](=[CX3](c)c)C(=O)O[#6]",
    "oxanilide": "c[NH]C(=O)C(=O)[NH]c",
    "aryl_salicylate": "[OX2H]c1ccccc1C(=O)Oc1ccccc1",
    "benzylidene_malonate": "O=C(O[#6])C(=[CH]c1ccccc1)C(=O)O[#6]",
    "benzophenone": "c1ccccc1[CX3;!R](=O)c1ccccc1",
}
_ANTIOXIDANT = ("hindered_phenol", "cycloalkyl_phenol", "diarylamine", "phenylenediamine", "dihydroquinoline",
                "gallate", "chromanol", "benzofuranone", "phosphite", "thiodipropionate")
_NOT_ANTIOXIDANT = ("anthraquinone", "ring_aryl_ketone", "azo", "nitro", "nitroso", "aryl_acid", "dimethylaminoaryl",
                    "benzotriazole_uva", "triazine_uva")
_UV = ("benzotriazole_uva", "triazine_uva", "hydroxybenzophenone", "hals", "cyanoacrylate", "oxanilide",
       "aryl_salicylate", "benzylidene_malonate")

# name, the term a question uses, aliases, what it holds, and how membership is decided: a PlastChem group ("group"),
# structures of which any ("any") or all ("all") must match and none ("none") may, and a minimum carbon count.
_FAMILY_SPECS: tuple[dict[str, Any], ...] = (
    {"name": "Phthalates", "term": "phthalates", "group": "orthophthalates",
     "aliases": ("phthalate", "phthalate esters", "phthalate plasticizers", "ortho-phthalates", "orthophthalates",
                 "o-phthalates"),
     "description": "ortho-phthalate plasticizers", "examples": ("DEHP", "DBP", "BBP")},
    {"name": "Terephthalates", "term": "terephthalates", "group": "isophthalates_terephthalates_trimellitates",
     "aliases": ("terephthalate", "terephthalate esters", "isophthalates", "trimellitates"),
     "description": "terephthalate, isophthalate and trimellitate esters",
     "examples": ("DEHT", "Dimethyl terephthalate", "Dibutyl terephthalate")},
    {"name": "Bisphenols", "term": "bisphenols", "group": "bisphenols",
     "aliases": ("bisphenol analogues", "bisphenol analogs"),
     "description": "bisphenol A and its analogues", "examples": ("BPA", "BPF", "Bisphenol B")},
    {"name": "Alkylphenols", "term": "alkylphenols", "group": "alkylphenols",
     "aliases": ("alkylphenol", "alkyl phenols"),
     "description": "butyl-, octyl- and nonylphenols and their ethoxylates",
     "examples": ("4-Nonylphenol", "4-tert-Octylphenol", "4-tert-Butylphenol")},
    {"name": "Antioxidants", "term": "antioxidants", "any": _ANTIOXIDANT, "none": _NOT_ANTIOXIDANT,
     "aliases": ("antioxidant", "phenolic antioxidants", "hindered phenols", "aminic antioxidants"),
     "description": "hindered-phenol, aminic, phosphite and thioester antioxidants, gallates and tocopherols",
     "examples": ("BHT", "2,4-Di-tert-butylphenol", "Alpha-Tocopherol")},
    {"name": "UV stabilizers", "term": "UV stabilizers", "any": _UV,
     "aliases": ("uv stabilizer", "uv stabilisers", "uv stabiliser", "uv-stabilizers", "uv absorbers", "uv absorber",
                 "light stabilizers", "uv filters"),
     "description": "benzotriazole, triazine and benzophenone UV absorbers and hindered-amine light stabilizers",
     "examples": ("UV-328", "Tinuvin P", "Octabenzone")},
    {"name": "Benzophenones", "term": "benzophenones", "group": "acetophenones_benzophenones", "all": ("benzophenone",),
     "aliases": ("benzophenone derivatives",),
     "description": "benzophenone photoinitiators and UV absorbers",
     "examples": ("Benzophenone", "Oxybenzone", "4-Methylbenzophenone")},
    {"name": "Aromatic amines", "term": "aromatic amines", "group": "aromatic_amines",
     "aliases": ("aromatic amine", "primary aromatic amines", "arylamines"),
     "description": "primary aromatic amines",
     "examples": ("4,4'-Methylenedianiline", "o-Toluidine", "2,4-Diaminotoluene")},
    {"name": "Slip agents", "term": "slip agents", "group": "aliphatic_primary_amides", "min_carbons": 12,
     "aliases": ("slip agent", "slip additives", "fatty acid amides", "fatty amides"),
     "description": "fatty acid amides", "examples": ("Erucamide", "Oleamide", "Octadecanamide")},
    {"name": "Salicylates", "term": "salicylates", "group": "salicylate_esters", "aliases": ("salicylate esters",),
     "description": "salicylate esters", "examples": ("Methyl salicylate", "Homosalate", "2-Ethylhexyl salicylate")},
    {"name": "Parabens", "term": "parabens", "group": "parabens", "aliases": ("paraben", "4-hydroxybenzoates"),
     "description": "4-hydroxybenzoate esters", "examples": ("Methylparaben", "Propylparaben", "Butylparaben")},
)
_ELEMENT_NAMES = {"F": "fluorine", "Cl": "chlorine", "Br": "bromine", "I": "iodine", "S": "sulfur", "P": "phosphorus",
                  "Si": "silicon", "B": "boron", "Se": "selenium"}


def _family_basis(spec: dict[str, Any]) -> str:
    parts = [f"PlastChem group {spec['group']}"] if "group" in spec else []
    if "any" in spec:
        parts.append("any of " + ", ".join(spec["any"]).replace("_", " "))
    if "all" in spec:
        parts.append("with a " + ", ".join(spec["all"]).replace("_", " ") + " core")
    if "none" in spec:
        parts.append("excluding " + ", ".join(spec["none"]).replace("_", " "))
    if "min_carbons" in spec:
        parts.append(f"at least {spec['min_carbons']} carbons")
    return "; ".join(parts)


def _outside_reason(row: dict[str, Any], mol: Any) -> str:
    """Why a PlastChem entry of a family is not in the release (which holds neutral CHNO molecules up to 700 g/mol)."""
    if row["inorganic_compounds"] or row["organometallics"]:
        return "contains a metal"
    if row["UVCBs"] or row["polymers"] or row["mixtures"]:
        return "a mixture or polymer, not one structure"
    if "." in row["smiles"]:
        return "a salt or a mixture of molecules"
    if mol is None:
        return "no structure in PubChem"
    from rdkit import Chem

    if Chem.GetFormalCharge(mol) != 0:
        return "an ion"
    elements = {atom.GetSymbol() for atom in mol.GetAtoms()} - {"C", "H", "N", "O"}
    named = sorted(_ELEMENT_NAMES[e] for e in elements if e in _ELEMENT_NAMES)
    if len(named) < len(elements):
        return "contains a metal"
    if named:
        return "contains " + " and ".join(named)
    heavy = float(row.get("molecular_weight") or 0) > 700
    return "heavier than 700 g/mol" if heavy else "not in the campaign's input list"


def build_families(workbook: str | Path, out: str | Path | None = None, *, asset: str | Path | None = None,
                   specs: Sequence[dict[str, Any]] = _FAMILY_SPECS) -> dict:
    """Write data/plastchem_families.json from the PlastChem workbook: each family's members by InChIKey (the screens
    evaluate those the asset computed) and, for the entries the asset lacks, their names and why. Refuses an alias
    that names a contaminant or another family, and an example that does not resolve to one of the family's members."""
    import openpyxl
    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    workbook, target = Path(workbook), Path(out or _PLASTCHEM_FAMILIES)
    con = duckdb.connect(str(asset or _PLASTCHEM_ASSET), read_only=True)
    released = {key for (key,) in con.execute("SELECT inchikey FROM contaminants").fetchall()}
    aliases = {alias: cid for alias, cid in con.execute("SELECT alias, id FROM aliases").fetchall()}
    ids = {key: cid for cid, key in con.execute("SELECT id, inchikey FROM contaminants").fetchall()}
    patterns = {name: Chem.MolFromSmarts(smarts) for name, smarts in _SMARTS.items()}
    sheet = openpyxl.load_workbook(workbook, read_only=True)["Full database"].iter_rows(values_only=True)
    next(sheet)  # section headers
    columns = [name for name in next(sheet)]
    entries = []
    for values in sheet:
        if not values or values[0] is None:
            continue
        row = {name: value for name, value in zip(columns, values) if name}
        row = {**row, **{name: row.get(name) not in (None, 0, "0") for name in
                         ("inorganic_compounds", "organometallics", "UVCBs", "polymers", "mixtures")}}
        row["smiles"] = row.get("isomeric_smiles") or row.get("canonical_smiles") or ""
        mol = Chem.MolFromSmiles(row["smiles"]) if row["smiles"] and "." not in row["smiles"] else None
        entries.append((row, mol))
    folded = {}
    families = []
    for spec in specs:
        members, outside = set(), {}
        for row, mol in entries:
            if "group" in spec and row.get(spec["group"]) in (None, 0, "0"):
                continue
            if any(key in spec for key in ("any", "all", "none")) and mol is None:
                continue
            matches = lambda names: [mol.HasSubstructMatch(patterns[name]) for name in names]
            if "any" in spec and not any(matches(spec["any"])):
                continue
            if "all" in spec and not all(matches(spec["all"])):
                continue
            if "none" in spec and any(matches(spec["none"])):
                continue
            if "min_carbons" in spec and (mol is None or sum(atom.GetSymbol() == "C" for atom in mol.GetAtoms())
                                          < spec["min_carbons"]):
                continue
            key = row.get("inchikey")
            if key and key in released:
                members.add(key)
            else:
                name = str(row.get("pubchem_name") or row.get("cas") or row.get("plastchem_ID"))
                outside.setdefault(name, {"name": name, "inchikey": key or None, "reason": _outside_reason(row, mol)})
        names = [spec["name"], spec["term"], *spec["aliases"]]
        for alias in map(_key, names):
            if alias in aliases:
                raise ValueError(f"family alias {alias!r} of {spec['name']} names a contaminant")
            if folded.setdefault(alias, spec["name"]) != spec["name"]:
                raise ValueError(f"family alias {alias!r} names both {folded[alias]} and {spec['name']}")
        member_ids = {ids[key] for key in members}
        for example in spec["examples"]:
            if aliases.get(_key(example)) not in member_ids:
                raise ValueError(f"{spec['name']} example {example!r} does not resolve to a family member")
        families.append({
            "name": spec["name"], "term": spec["term"], "aliases": list(spec["aliases"]),
            "description": spec["description"], "basis": _family_basis(spec), "examples": list(spec["examples"]),
            "members": sorted(members), "outside_release": sorted(outside.values(), key=lambda item: item["name"]),
        })
    payload = {
        "source": {"workbook": workbook.name, "sha256": hashlib.sha256(workbook.read_bytes()).hexdigest()},
        "checked_against_manifest_sha256": dict(con.execute("SELECT key, value FROM metadata").fetchall()).get(
            "manifest_sha256"),
        "families": families,
    }
    target.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return {family["name"]: len(family["members"]) for family in families}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--families"]:
        print(json.dumps(build_families(args[1]), indent=2))
        sys.exit(0)
    print(json.dumps(promote_opencosmo_release(
        args[0], allow_preview="--preview" in args,
        extensions=[args[i + 1] for i, arg in enumerate(args) if arg == "--extension"],
    ), indent=2))
