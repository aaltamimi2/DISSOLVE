#!/bin/bash
#SBATCH -p research
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH -t 08:00:00
source $HOME/dissolve-hpc/env.sh
cd $HOME/dissolve-hpc
BLOCK=$1
SHARD=$(printf "%s-%04d" $BLOCK $SLURM_ARRAY_TASK_ID)
echo "$(hostname) $(lscpu | grep "Model name" | sed "s/.*: *//") $(date)"
if [ "$BLOCK" = feeds ] || [ "$BLOCK" = mc ]; then
  python bin/run_feeds.py manifests/$BLOCK/$SHARD.jsonl results/$BLOCK/$SHARD
else
  python bin/run_shard.py manifests/$BLOCK/$SHARD.jsonl results/$BLOCK/$SHARD.runs.jsonl.gz
fi
echo "exit $? $(date)"
