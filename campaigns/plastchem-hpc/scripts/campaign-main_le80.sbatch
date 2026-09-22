#!/bin/bash
#SBATCH --job-name=contam-p2-main_le80-v1
#SBATCH --partition=research
#SBATCH --constraint=milan&cpu
#SBATCH --exclude=euler09,euler10
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=24:00:00
#SBATCH --signal=B:USR1@120
#SBATCH --no-requeue
set -euo pipefail
source "$HOME/plastchem-euler/orca-env.sh"
exec python3 "$HOME/plastchem-euler/campaign-v1/campaign_runner.py" main_le80
