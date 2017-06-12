#!/bin/sh
# @ job_name           = test_m_newtoeplitz
# @ job_type           = bluegene
# @ comment            = "n=4, m=8, zero-padded"
# @ error              = $(job_name).$(Host).$(jobid).err
# @ output             = $(job_name).$(Host).$(jobid).out
# @ bg_size            = 64
# @ wall_clock_limit   = 0:30:00
# @ bg_connectivity    = Torus
# @ queue 

# A debug block has 64 nodes, 64*16 = 1024 cores, 1024*4 = 4096 threads.
# Each node has 16 cores, 16*4 = 64 threads. 
# Each core has 4 threads.
# Free to choose RPN and OMP_NUM_THREADS such that (RPN * OMP_NUM_THREAD) <= number of threads per node = 64.

method=yty2					# Scheme of decomposition. yty2 is the method describe in Nilou's report.
offsetn=0
offsetm=0
n=4
m=8
p=2							# VISAL SAYS: Can set to m/4, m/2, m, 2m. Fastest when set to m/2 or m/4.
pad=1						# 0 for no padding; 1 for padding.

nodes = 64					# Nonfunctional -- only for presentation purposes.
NP=8						# Number of MPI processes. Must be set to 2n for this code. NP <= (RPN * bg_size)
RPN=8						# Number of MPI processes per node = 1,2,4,8,16,32,64. RPN <= NP
export OMP_NUM_THREADS=8	# Number of OpenMP threads per MPI process = 1,2,4,8,16,32,64. (RPN * OMP_NUM_THREADS ) <= 64 = threads per node

source /scratch/s/scinet/nolta/venv-numpy/setup
module purge
module unload mpich2/xl
module load binutils/2.23 bgqgcc/4.8.1 mpich2/gcc-4.8.1 
module load python/2.7.3

cd /scratch/a/aparamek/sufkes/scintillometry/toeplitz_decomp/

echo "----------------------"
echo "STARTING in directory $PWD"
date
echo "n ${n}, m ${m}, bg ${nodes}, np ${NP}, rpn ${RPN}, omp ${OMP_NUM_THREADS}"
time runjob --np ${NP} --ranks-per-node=${RPN} --envs HOME=$HOME LD_LIBRARY_PATH=/scinet/bgq/Libraries/HDF5-1.8.12/mpich2-gcc4.8.1//lib:/scinet/bgq/Libraries/fftw-3.3.4-gcc4.8.1/lib:$LD_LIBRARY_PATH PYTHONPATH=/scinet/bgq/tools/Python/python2.7.3-20131205/lib/python2.7/site-packages/ : /scratch/s/scinet/nolta/venv-numpy/bin/python run_real_new.py ${method} ${offsetn} ${offsetm} ${n} ${m} ${p} ${pad}
echo "ENDED"