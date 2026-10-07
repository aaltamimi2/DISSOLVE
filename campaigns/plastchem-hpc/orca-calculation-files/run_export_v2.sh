#!/bin/bash
# A-11: build both archives on Euler in ~/opencosmo-export-v2. Surfaces first (tar follows the symlinks), then the ORCA
# files (run_pack.sh). Detached; progress in export-v2.log.
set -o pipefail
cd ~/opencosmo-export-v2 || exit 1
{
  echo "START $(date)"
  rm -rf opencosmo-outputs surfaces-archive && mkdir surfaces-archive
  if python3 build_export_v2.py &&
     tar -chf - opencosmo-outputs | nice -n 19 xz -T2 -6 |
       split -b 95000000 -d -a 2 - surfaces-archive/opencosmo-outputs.tar.xz.part; then
    (cd surfaces-archive && sha256sum opencosmo-outputs.tar.xz.part* > SHA256SUMS &&
      echo "$(cat opencosmo-outputs.tar.xz.part* | sha256sum | cut -d' ' -f1)  opencosmo-outputs.tar.xz" >> SHA256SUMS &&
      cp ../opencosmo-outputs/MANIFEST.tsv . && ls -l)
    echo "SURFACES_DONE $(date)"
    bash run_pack.sh && cat orca-archive.log && python3 verify_orca_archive.py && echo "EXPORT_V2_DONE $(date)"
  else
    echo "FAILED $(date)"
  fi
} > export-v2.log 2>&1
