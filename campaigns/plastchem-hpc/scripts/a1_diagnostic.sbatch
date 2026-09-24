#!/bin/bash
#SBATCH --partition=research
#SBATCH --constraint=milan&cpu
#SBATCH --exclude=euler09,euler10
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --job-name=contam-a1-diag
set -euo pipefail
source "$HOME/plastchem-euler/orca-env.sh"
exec python3 "$HOME/plastchem-euler/diagnostics-a1/a1_diagnostic.py" "$1"
