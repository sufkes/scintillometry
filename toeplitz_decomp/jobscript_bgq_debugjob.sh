#!/bin/bash
source /scratch/s/scinet/nolta/venv-numpy-1.13.3-scipy-1.0.0/setup
module unload bgqgcc/4.4.6
module load binutils/2.23 bgqgcc/4.8.1 mpich2/gcc-4.8.1

# A debug block has bg_size = 64 nodes, 64*16 = 1024 cores, 1024*4 = 4096 threads.
# Each node has 16 cores, 16*4 = 64 threads. 
# Each core has 4 threads.
# Free to choose RPN and OMP_NUM_THREADS such that (RPN * OMP_NUM_THREAD) <= number of threads per node = 64.

method=yty2     # Scheme of decomposition. yty2 is the method described in Nilou's report.
offsetn=0
offsetm=0
n=256
m=256
p=128            # VISAL SAYS: Can set to m/4, m/2, m, 2m. Fastest when set to m/2 or m/4.
pad=1           # 0 for no padding; 1 for padding.

NP=512          # Number of MPI processes. Must be set to 2n for this code. NP <= (RPN * bg_size)
RPN=8          # Number of MPI processes per node = 1,2,4,8,16,32,64. RPN <= NP
OMP=8           # Number of OpenMP threads per MPI process = 1,2,4,8,16,32,64. (RPN * OMP_NUM_THREADS ) <= 64 = threads per node

if [ "$NP" -ne "$(( 2*n ))" ]
   then
   echo "Error: Set the number of MPI processes to 2n. Quitting."
   exit 1   
fi

echo "n ${n}, m ${m}, p ${p}"

time runjob --np ${NP} --ranks-per-node=${RPN} --envs OMP_NUM_THREADS=${OMP} HOME=$HOME LD_LIBRARY_PATH=/scinet/bgq/Libraries/HDF5-1.8.12/mpich2-gcc4.8.1//lib:/scinet/bgq/Libraries/fftw-3.3.4-gcc4.8.1/lib:$LD_LIBRARY_PATH PYTHONPATH=/scinet/bgq/tools/Python/python2.7.3-20131205/lib/python2.7/site-packages/ : /scratch/s/scinet/nolta/venv-numpy-1.13.3-scipy-1.0.0/bin/python run_real_new.py ${method} ${offsetn} ${offsetm} ${n} ${m} ${p} ${pad}
