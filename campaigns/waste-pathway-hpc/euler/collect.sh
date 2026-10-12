#!/bin/bash
#SBATCH -p research
#SBATCH --cpus-per-task=2
#SBATCH --mem=48G
#SBATCH -t 04:00:00
#SBATCH -J dcollect
source $HOME/dissolve-hpc/env.sh
cd $HOME/dissolve-hpc
OUT=${1:-analysis}
python bin/collect.py results manifests $OUT
echo "exit $? $(date)"
