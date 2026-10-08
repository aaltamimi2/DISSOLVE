#!/bin/bash
#SBATCH --job-name=contam-coverage
#SBATCH --partition=research
#SBATCH --constraint=milan&cpu
#SBATCH --exclude=euler09,euler10
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=72:00:00
#SBATCH --signal=B:USR1@120
#SBATCH --no-requeue
# A-13: copied from halogen.sbatch. submit_coverage_remote.py names the job contam-coverage-<chunk> and passes the
# chunk's own --mem and --time (raised per row only after an out-of-memory or time-limit failure, and recorded).
set -euo pipefail
source "$HOME/plastchem-euler/orca-env.sh"
exec python3 "$HOME/plastchem-euler/coverage-v1/$1/coverage_runner.py" "$1"
