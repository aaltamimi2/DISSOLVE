"""Export the columns the census funnel needs from the pinned PlastChem workbook (owner request 2026-10-06, halogen tier).

Run with a Python that has openpyxl (the campaign venv has none):  python3 scripts/export_plastchem_workbook.py
Writes inputs/census/plastchem_db_v1.0_full_database_subset.csv and refuses an unexpected workbook digest."""
import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = Path.home() / "dissolve-v12-builder-1/plastchem-contaminants/plastchem_db_v1.0.xlsx"
WORKBOOK_SHA256 = "df4ccad3b238b3b76d180db48892c9c17e7cd823cecb495e7888ea0690216984"  # census notes, 2026-09-12
OUT = ROOT / "inputs/census/plastchem_db_v1.0_full_database_subset.csv"
COLUMNS = ["plastchem_ID", "cas", "cas_fixed", "pubchem_cid", "pubchem_name", "iupac_name", "molecular_formula",
           "molecular_weight", "canonical_smiles", "isomeric_smiles", "inchikey", "charge",
           "inorganic_compounds", "organometallics", "UVCBs", "polymers", "mixtures"]

digest = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
if digest != WORKBOOK_SHA256:
    sys.exit(f"workbook digest {digest} is not the pinned census workbook")
frame = pd.read_excel(WORKBOOK, sheet_name="Full database", header=1, dtype=str)
frame = frame[frame["plastchem_ID"].notna()][COLUMNS]
if len(frame) != 17932:
    sys.exit(f"{len(frame)} entries, not the census's 17,932")
OUT.parent.mkdir(parents=True, exist_ok=True)
frame.to_csv(OUT, index=False)
print(OUT, len(frame), hashlib.sha256(OUT.read_bytes()).hexdigest())
