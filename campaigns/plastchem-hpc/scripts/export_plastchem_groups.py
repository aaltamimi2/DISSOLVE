"""Export PlastChem's chemical groups and harmonized functions from the pinned workbook, for the collaborator layout
of the v2 archives (owner, 2026-10-07: organise the contaminants into the Zhou 2026 publication sets, then into the
families DISSOLVE's agent uses; families for the rest).

Run with a Python that has pandas and openpyxl:  python3 scripts/export_plastchem_groups.py
Writes inputs/census/plastchem_db_v1.0_groups_functions.csv: identifiers, Harmonized_functions and every 0/1 column of
the workbook's Priority_groups and Other_groups sections (priority_groups itself is a flag, not a group, and the two
unnamed spacer columns are dropped). Refuses an unexpected workbook digest."""
import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = Path.home() / "dissolve-v12-builder-1/plastchem-contaminants/plastchem_db_v1.0.xlsx"
WORKBOOK_SHA256 = "df4ccad3b238b3b76d180db48892c9c17e7cd823cecb495e7888ea0690216984"  # census notes, 2026-09-12
OUT = ROOT / "inputs/census/plastchem_db_v1.0_groups_functions.csv"
IDS = ["plastchem_ID", "inchikey", "cas", "pubchem_name", "iupac_name", "Harmonized_functions"]

digest = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
if digest != WORKBOOK_SHA256:
    sys.exit(f"workbook digest {digest} is not the pinned census workbook")
top = pd.read_excel(WORKBOOK, sheet_name="Full database", header=None, nrows=2, dtype=str)
section, groups = None, []
for heading, column in zip(top.iloc[0], top.iloc[1]):
    section = heading if isinstance(heading, str) else section
    if section in ("Priority_groups", "Other_groups") and isinstance(column, str) and column != "priority_groups":
        groups.append(column)
frame = pd.read_excel(WORKBOOK, sheet_name="Full database", header=1, dtype=str)
frame = frame[frame["plastchem_ID"].notna()][IDS + groups]
if len(frame) != 17932:
    sys.exit(f"{len(frame)} entries, not the census's 17,932")
OUT.parent.mkdir(parents=True, exist_ok=True)
frame.to_csv(OUT, index=False)
print(OUT, len(frame), "entries,", len(groups), "groups;", hashlib.sha256(OUT.read_bytes()).hexdigest())
