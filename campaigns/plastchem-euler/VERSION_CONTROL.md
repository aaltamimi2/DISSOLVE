# Workspace version control

Owner instruction, 2026-09-22: commit at each completed milestone. Never push this repository anywhere without explicit owner authorization. Git was initialized in place; running campaign processes were not stopped or relocated.

Track the charter and amendments, scripts, methods workflow, reports, pinned inputs, census reading notes, and analysis tables. `state/`, `logs/`, Python caches, and explicitly listed files larger than 10,000,000 bytes are excluded. Before later milestones, check for newly created files above that limit and list their exact paths in `.gitignore`; do not rely on an extension pattern.

`EXCLUSION_MANIFEST.json` records the initial excluded payload's paths, sizes and SHA256 hashes. Git's internal `.git/` directory is repository metadata, not excluded scientific payload. The SSH control socket has no regular-file byte stream and is documented with a null digest rather than copied. Cache files remain rebuildable local artifacts.

`WORKSPACE_ARCHIVE_RECEIPT.json` records the verified dated archive at `/mnt/r/plastchem-euler/workspace-archive/2026-09-22/`. The state/log payload is stored as `state.tar.gz` and `logs.tar.gz`, transferred with scp and verified both as whole archives and member-by-member. The archive is a per-file capture over a time interval, not an atomic snapshot of live state. Its `state-logs-digest-manifest.json` records source/destination digest equality and per-file capture times; live files may have advanced afterward. Never overwrite this dated capture when taking a future milestone snapshot: use a new dated or timestamped destination.

Third-party census papers, supplements, and the original PlastChem database are outside the repository at that archive's `census-sources/`, with a separate digest manifest. `inputs/census/PLASTCHEM_READING_NOTES.md` is copied from the read-only builder reference; pinned derived campaign inputs remain tracked here. Bulk ORCA surfaces and other externally stored campaign outputs remain under `/mnt/r/plastchem-euler/`; this initial Git commit is not a backup of that entire bulk-data tree.

Reproduction helper: `scripts/archive_workspace_milestone.py` documents the initial capture process. Its destination is fixed to this initial milestone; do not rerun it over the sealed archive. Use a new destination for later captures.
