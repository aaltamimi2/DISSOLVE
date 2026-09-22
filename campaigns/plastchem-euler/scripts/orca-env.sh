# Source this file on Euler for this lane's ORCA 6.1.1 installation.
export ORCA_ROOT="$HOME/plastchem-euler/software/orca_6_1_1_linux_x86-64_shared_openmpi418_avx2"
export ORCA_BIN="$ORCA_ROOT/orca"
export PATH="$ORCA_ROOT:$PATH"
export LD_LIBRARY_PATH="$ORCA_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
