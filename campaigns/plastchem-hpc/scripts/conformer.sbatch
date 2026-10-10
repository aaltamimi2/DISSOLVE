#!/bin/bash
#SBATCH --job-name=confstudy
#SBATCH --partition=research
#SBATCH --constraint=rome|naples
#SBATCH --exclude=euler09,euler10
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=72:00:00
#SBATCH --signal=B:USR1@120
#SBATCH --no-requeue
# Exploratory conformer study (owner request 2026-10-10): copied from coverage.sbatch. conformer_submit_remote.py names
# the job confstudy-<chunk> (not contam-*, so it never counts toward the campaign's Milan cap) and passes --mem, --time,
# the pool constraint and a positive --nice, so campaign tasks on the same pools start first.
set -euo pipefail
source "$HOME/plastchem-euler/orca-env.sh"
exec python3 "$HOME/plastchem-euler/conformers-v1/$1/conformer_runner.py" "$1"
