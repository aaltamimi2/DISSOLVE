#!/bin/bash
# Build orca-archive/ on Euler: plan, pack, xz, split into 95 MB parts, checksums. Detached; progress in orca-archive.log.
set -o pipefail
cd ~/opencosmo-export || exit 1
rm -rf orca-archive && mkdir orca-archive
{
  echo "START $(date)"
  if nice -n 10 python3 pack_orca_files.py plan &&
     nice -n 10 python3 pack_orca_files.py pack | nice -n 19 xz -T2 -6 |
       split -b 95000000 -d -a 2 - orca-archive/orca-calculation-files.tar.xz.part; then
    cd orca-archive && sha256sum orca-calculation-files.tar.xz.part* > SHA256SUMS &&
      echo "$(cat orca-calculation-files.tar.xz.part* | sha256sum | cut -d' ' -f1)  orca-calculation-files.tar.xz" >> SHA256SUMS &&
      xz -dc < <(cat orca-calculation-files.tar.xz.part*) | tar -tf - | wc -l | sed 's/^/entries in archive: /' &&
      ls -l && cat SHA256SUMS && echo "DONE $(date)"
  else
    echo "FAILED $(date)"
  fi
} > orca-archive.log 2>&1
rm -rf ~/opencosmo-export/orca-calculation-files   # copy tree left by the stopped copy-based builder
