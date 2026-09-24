#!/bin/bash
#SBATCH --partition=research
#SBATCH --constraint=milan&cpu
#SBATCH --exclude=euler09,euler10
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=04:00:00
#SBATCH --no-requeue
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export PYTHONPATH="$PWD"
venv/bin/python -u phase8_worker.py 81 "$SLURM_ARRAY_TASK_ID"
