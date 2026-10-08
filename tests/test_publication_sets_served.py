"""The compound sets of Zhou et al., Green Chem. 2026 are served (promotion-v3, owner 2026-10-08: "make sure all of
these are served in the live app ... fully drop those charged PFAS"): 26 PFAS, 8 phthalates and 4 brominated flame
retardants, each computed as the neutral species the paper modelled, with every partition and LLE row."""

from __future__ import annotations

import csv

import pytest
from rdkit import Chem

from dissolve import contaminant_search, plastchem_release
from dissolve import contaminants as screens
from dissolve.contaminants import _key
from dissolve.contracts import parse_tool_result

#: label -> (alias the release offers or None, InChIKey served). Salts are their parent acids, as the paper modelled them.
PAPER = {
    "PFAS": {
        "PFBA": "YPJUNDFVDDCYIH-UHFFFAOYSA-N", "PFPA": "CXZGQIAOTKWCDB-UHFFFAOYSA-N",
        "PFHxA": "PXUULQAPEKKVAH-UHFFFAOYSA-N", "PFHA": "ZWBAMYVPMDSJGQ-UHFFFAOYSA-N",
        "PFOA": "SNGREZUHAYWORS-UHFFFAOYSA-N", "PFNA": "UZUFPBIDKMEQEQ-UHFFFAOYSA-N",
        "PFDA": "PCIUEQPBYFRTEM-UHFFFAOYSA-N", "TFPA": "CSEBNABAWMZWIF-UHFFFAOYSA-N",
        "PFTriDA": "LVDGGZAZAYHXEY-UHFFFAOYSA-N", "PFUnDA": "SIDINRCMMRKXGQ-UHFFFAOYSA-N",
        "PFDoDA": "CXGONMQFMIYUJR-UHFFFAOYSA-N", "PFTetraDA": "RUDINRUXCKIXAJ-UHFFFAOYSA-N",
        "PFBS": "JGTNAGYHADQMCM-UHFFFAOYSA-N", "PFPS": "ACEKLXZRZOWKRY-UHFFFAOYSA-N",
        "PFHxS": "QZHDEAJFRJCDMF-UHFFFAOYSA-N", "PFHS": "OYGQVDSRYXATEL-UHFFFAOYSA-N",
        "PFOS": "YFSUTJLHUFNCNZ-UHFFFAOYSA-N", "PFUnDS": "UKHUPOMCGUFNAP-UHFFFAOYSA-N",
        "PFTriDS": "JMIXTHMPTMXARZ-UHFFFAOYSA-N", "PFDS": "HYWZIAVPBSTISZ-UHFFFAOYSA-N",
        "PFDoDS": "CFCRODHVHXGTPC-UHFFFAOYSA-N", "KClHxDFS": "GGOUUEMCWBTDMT-UHFFFAOYSA-N",
        "PFNS": "MNEXVZFQQPKDHC-UHFFFAOYSA-N", "NaDoDFNt": "AFDRCEOKCOUICI-UHFFFAOYSA-N",
        "NH4TetraFPt": "CSEBNABAWMZWIF-UHFFFAOYSA-N", "NH4PFNt": "AFDRCEOKCOUICI-UHFFFAOYSA-N",
    },
    "Phthalates": {
        "BBP": "IRIAEXORFWYRCZ-UHFFFAOYSA-N", "DBP": "DOIRQSBPFJWKBE-UHFFFAOYSA-N",
        "DEHP": "BJQHLKABXJIVAM-UHFFFAOYSA-N", "DEP": "FLKPEMZONWLCSK-UHFFFAOYSA-N",
        "DiDP": "ZVFDTKUVRCTHQE-UHFFFAOYSA-N", "DiNP": "HBGGXOJOCNVPFY-UHFFFAOYSA-N",
        "DnHP": "KCXZNSGUUQJJTR-UHFFFAOYSA-N", "DnOP": "MQIUGAXCHLFZKX-UHFFFAOYSA-N",
    },
    "BFR": {
        "Tri-PBDE": "UPNBETHEXPIWQX-UHFFFAOYSA-N", "HBCD": "DEIGXXQKDWULML-MOCCIAMBSA-N",
        "DECA": "WHHGLZMJPXIBIX-UHFFFAOYSA-N", "TBBPA-dbp": "LXIZRZRTWSDLKK-UHFFFAOYSA-N",
    },
}
#: Labels naming a class (tribromodiphenyl ethers) or a mixture of stereoisomers: listed, never offered as aliases.
NOT_ALIASES = {"Tri-PBDE", "HBCD"}


def _served(con, inchikey):
    cid, smiles, computed = con.execute(
        "SELECT id, smiles, computed FROM contaminants WHERE inchikey = ?", [inchikey]).fetchone()
    partition = con.execute("SELECT count(*), count(DISTINCT solvent), count(DISTINCT polymer) FROM partition "
                            "WHERE id = ? AND logp IS NOT NULL", [cid]).fetchone()
    lle = con.execute("SELECT count(*) FROM lle WHERE id = ?", [cid]).fetchone()[0]
    return cid, smiles, computed, partition, lle


def test_every_compound_of_the_three_sets_is_served_neutral_and_complete():
    con = screens._plastchem()
    assert {name: len(labels) for name, labels in PAPER.items()} == {"PFAS": 26, "Phthalates": 8, "BFR": 4}
    for labels in PAPER.values():
        for label, inchikey in labels.items():
            cid, smiles, computed, partition, lle = _served(con, inchikey)
            mol = Chem.MolFromSmiles(smiles)
            assert computed, label
            assert sum(atom.GetFormalCharge() for atom in mol.GetAtoms()) == 0, f"{label} is not neutral"
            assert partition == (320, 32, 10) and lle == 64, label  # 32 solvents x 10 polymers; 32 x 2 regimes
    assert len({key for labels in PAPER.values() for key in labels.values()}) == 36  # two salt pairs share an acid


def test_the_papers_labels_name_them_and_class_names_do_not():
    con = screens._plastchem()
    aliases: dict[str, set[int]] = {}
    for alias, cid in con.execute("SELECT alias, id FROM aliases").fetchall():
        aliases.setdefault(alias, set()).add(cid)
    for labels in PAPER.values():
        for label, inchikey in labels.items():
            cid = con.execute("SELECT id FROM contaminants WHERE inchikey = ?", [inchikey]).fetchone()[0]
            if label in NOT_ALIASES:
                assert _key(label) not in aliases, label
            else:
                assert aliases.get(_key(label)) == {cid}, label
    for salt_cas, acid in (("73606-19-6", "GGOUUEMCWBTDMT-UHFFFAOYSA-N"), ("958445-44-8", "AFDRCEOKCOUICI-UHFFFAOYSA-N"),
                           ("62037-80-3", "CSEBNABAWMZWIF-UHFFFAOYSA-N")):
        cid = con.execute("SELECT id FROM contaminants WHERE inchikey = ?", [acid]).fetchone()[0]
        assert aliases.get(_key(salt_cas)) == {cid}, salt_cas  # a salt's CAS finds its parent acid


def test_no_charged_pfas_is_served_in_any_form():
    """The 24 PFAS anions of amendment A-12 (InChIKeys ending -M) are not in the release, not even as status rows."""
    con = screens._plastchem()
    assert con.execute("SELECT count(*) FROM contaminants WHERE inchikey LIKE '%-M'").fetchone()[0] == 0
    meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
    assert meta["publication_labels"] == "47"


def test_a_sulfur_search_finds_the_paper_pfas_and_says_nothing_else_was_computed():
    found = parse_tool_result(contaminant_search.find_plastchem_contaminants(elements=["sulfur"]))["data"]
    assert found["total"] == found["with_partition_data"] == 11
    assert {m["inchikey"] for m in found["matches"]} <= set(PAPER["PFAS"].values())
    assert "computed no other PlastChem compound" in found["scope_note"]
    assert "Zhou et al." in found["coverage"]
    chlorine = parse_tool_result(contaminant_search.find_plastchem_contaminants(elements=["Cl"]))["data"]
    assert "scope_note" not in chlorine  # the campaign computed chlorine compounds itself


def _labels_file(tmp_path, rows):
    path = tmp_path / "publication-labels.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["label", "alias", "set", "input_inchikey", "offered", "note"])
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path


def test_the_writer_adds_offered_labels_and_refuses_an_ambiguous_one(tmp_path):
    row = dict(label="PFOS", alias="PFOS", set="PFAS", input_inchikey="K1", offered="yes", note="")
    release = _labels_file(tmp_path, [row, dict(row, label="HBCD", alias="HBCD", input_inchikey="K2", offered="no")])
    assert plastchem_release._publication_labels(release, {"K1": 1, "K2": 2}, set()) == {(_key("PFOS"), 1)}
    with pytest.raises(ValueError, match="would also name another contaminant"):
        plastchem_release._publication_labels(release, {"K1": 1, "K2": 2}, {(_key("PFOS"), 7)})
    with pytest.raises(ValueError, match="which the release lacks"):
        plastchem_release._publication_labels(release, {"K2": 2}, set())
    assert plastchem_release._publication_labels(tmp_path / "none", {}, set()) == set()
