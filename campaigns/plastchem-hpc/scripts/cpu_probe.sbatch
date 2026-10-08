#!/bin/bash
#SBATCH --partition=research
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
#SBATCH --no-requeue
# A-13 owner NOTE 2026-10-08 17:10 UTC: which CPU a node type really is, before its reproduction check. Prints one JSON
# line: host, partition, CPU model, AVX2/AVX-512, memory, and whether ORCA 6.1.1 starts there.
source "$HOME/plastchem-euler/orca-env.sh"
python3 - <<'PY'
import json, os, re, socket, subprocess
from pathlib import Path
info = Path('/proc/cpuinfo').read_text()
field = lambda name: (re.search(r'^' + re.escape(name) + r'\s*:\s*(.+)', info, re.M) or [None, None])[1]
flags = (field('flags') or '').split()
orca = subprocess.run([os.environ['ORCA_BIN'], '--version'], capture_output=True, text=True, timeout=60)
print(json.dumps({'host': socket.gethostname(), 'partition': os.environ.get('SLURM_JOB_PARTITION'),
                  'job': os.environ.get('SLURM_JOB_ID'), 'constraint_tag': os.environ.get('PROBE_TAG'),
                  'cpu_model': field('model name'), 'cpu_family': field('cpu family'), 'model_number': field('model'),
                  'avx2': 'avx2' in flags, 'avx512f': 'avx512f' in flags, 'cores_visible': os.cpu_count(),
                  'mem_total_kib': int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text()).group(1)),
                  'orca_version_output': (orca.stdout + orca.stderr).strip()[-300:]}))
PY
