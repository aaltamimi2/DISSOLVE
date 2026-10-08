"""Charge audit of a PlastChem release (2026-10-07, owner: "are there any other similar compounds which should actually
be charged but that we treat as alcohols?"). Every release compound is modelled as a neutral molecule; this lists the
computed ones that carry an acidic or basic group strong enough to be ionized in water, by class, first match wins.
The pKa ranges are typical aqueous literature values for each class, for orientation only; nothing here is computed.

    python3 scripts/charge_audit.py /mnt/r/plastchem-euler/promotion-v2 OUT.tsv"""
import collections
import csv
import gzip
import sys
from pathlib import Path

from rdkit import Chem

WEAK_N = "!$(N[N+](=O)[O-]);!$(NC#N);!$(N[OX2]);!$(NC=O)"  # not a nitro-, cyano-, hydroxy- or acyl-substituted N
CLASSES = [  # name, SMARTS, typical aqueous pKa, what ionization means
    ("fluorinated carboxylic acid (fluorine on the alpha carbon, like the paper's PFAS)",
     "[CX4](F)[CX3](=O)[OX2H1]", "about 0-1", "anion in water and in protic solvents"),
    ("di- or trichloro carboxylic acid (alpha carbon)", "[CX4](Cl)(Cl)[CX3](=O)[OX2H1]", "about 0.7-1.4",
     "anion in water"),
    ("diacid with conjugated or vicinal COOH (oxalic, maleic type)",
     "[$([OX2H1][CX3](=O)[CX3](=O)[OX2H1]),$([OX2H1][CX3](=O)[CX3]=[CX3][CX3](=O)[OX2H1])]", "about 1.2-3 (first)",
     "mono-anion in water"),
    ("polynitrophenol", "[OX2H1]c1ccc([N+](=O)[O-])cc1[N+](=O)[O-]", "about 4-4.6", "phenolate in water at pH 7"),
    ("amino acid or aminopolycarboxylate (alpha-amino acid)",
     "[NX3;H2,H1,H0;!$(NC=O);!$(N-a);!$(N-[#7,#8])][CX4][CX3](=O)[OX2H1]", "about 2 and 9-10",
     "zwitterion in water; chelators (EDTA, NTA, DTPA) polyanions"),
    ("strong base: guanidine, amidine or imidazoline",
     f"[$([NX3;{WEAK_N}][CX3](=[NX2;{WEAK_N}])[NX3;{WEAK_N}]),"
     f"$([NX3;{WEAK_N};!$(Na);!$(N-[#7])][CX3;!$(C-[O,S]);!$(C-a);!$(C-C=O)](=[NX2;!a;!$(N-[#7]);{WEAK_N}])[CX4])]",
     "about 10-14 (conjugate acid)", "cation in water"),
    ("other carboxylic acid", "[CX3](=O)[OX2H1]", "about 3-5", "anion in water near neutral or basic pH"),
]


def audit(release):
    with gzip.open(Path(release) / "contaminants.csv.gz", "rt") as handle:
        rows = [r for r in csv.DictReader(handle) if r["campaign_status_at_snapshot"] == "converged"
                and r["partition_predicted_rows"] not in ("", "0")]
    patterns = [(name, Chem.MolFromSmarts(smarts), pka, meaning) for name, smarts, pka, meaning in CLASSES]
    out = []
    for r in rows:
        mol = Chem.MolFromSmiles(r["smiles"])
        if mol is None:
            continue
        for name, pattern, pka, meaning in patterns:
            if mol.HasSubstructMatch(pattern):
                out.append({"class": name, "typical_pKa": pka, "when_ionized": meaning, "name": r["name"],
                            "cas": r["cas"], "inchikey": r["input_inchikey"], "smiles": r["smiles"]})
                break
    return len(rows), out


if __name__ == "__main__":
    total, found = audit(sys.argv[1])
    fields = ["class", "typical_pKa", "when_ionized", "name", "cas", "inchikey", "smiles"]
    with open(sys.argv[2], "w", newline="") as handle:
        writer = csv.DictWriter(handle, fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(found, key=lambda r: ([c[0] for c in CLASSES].index(r["class"]), r["name"].casefold())))
    print(f"{total} computed compounds; flagged {len(found)}:")
    for name, n in collections.Counter(r["class"] for r in found).items():
        print(f"  {n:4}  {name}")
