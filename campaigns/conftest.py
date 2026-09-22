"""Keep campaign snapshots out of the project test suite.

The campaign trees under campaigns/ are standalone script collections, not project tests. Their
test_*.py files run their checks at import time against campaign state and the R: drive, so
collecting them from the repository root aborts the whole suite. Run them from their campaign.
"""

collect_ignore = ["plastchem-euler"]
