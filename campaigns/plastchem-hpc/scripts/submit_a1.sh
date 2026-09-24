#!/bin/bash
set -euo pipefail
cd "$HOME/plastchem-euler/diagnostics-a1"
# Exclusive submission marker prevents accidental resubmission after a lost SSH connection.
(set -o noclobber; : > submission.started)
for key in XLYOFNOQVPJJNP-UHFFFAOYSA-N LFQSCWFLJHTTHZ-UHFFFAOYSA-N; do
  jobid=$(sbatch --parsable --chdir="$HOME/plastchem-euler/diagnostics-a1" --output="$HOME/plastchem-euler/diagnostics-a1/logs/%j.out" --error="$HOME/plastchem-euler/diagnostics-a1/logs/%j.err" a1_diagnostic.sbatch "$key")
  printf '%s %s\n' "$key" "$jobid" >> submissions.txt
done
cat submissions.txt
