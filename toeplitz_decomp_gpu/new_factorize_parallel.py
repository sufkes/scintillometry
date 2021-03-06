import numpy as np
from scipy.linalg.blas import cgeru, cherk, cgemm, scnrm2#, ctrsm
from scipy.linalg.lapack import ctrtrs
from numpy.linalg import cholesky
import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
sys.path.insert(0, currentdir + "/Exceptions")

from ToeplitzFactorizorExceptions import *

from mpi4py import MPI

from GeneratorBlocks import Blocks
from GeneratorBlock import Block

from time import time
import arrayfire as af

np.seterr(all='raise')

use_gpu_Om2 = True # Perform O(m^2) matrix operations on GPU. 
use_gpu_Om3 = True # Perform O(m^3) matrix operations on GPU.

MAXTIME = int(60*60*23.5) #23.5 hours in seconds
timePerLoop = []
startTime = time()

SEQ, WY1, WY2, YTY1, YTY2 = "seq", "wy1", "wy2", "yty1", "yty2"
class ToeplitzFactorizor:
    
    def __init__(self, folder, n,m, pad, detailedSave = False):
        self.comm = MPI.COMM_WORLD
        size  = self.comm.Get_size()
        self.size = size
        self.rank = self.comm.Get_rank()
        self.n = n
        self.m = m # With padding, m is twice its original value.
        self.pad = pad
        self.folder = folder
        self.blocks = Blocks()
        
        self.detailedSave = detailedSave
        self.numOfBlocks = n*(1 + pad)
        
        # Associate a GPU with each MPI process.
        if (use_gpu_Om2 or use_gpu_Om3):
            af.device.set_device(self.rank % af.get_device_count())
            self.af_device_id = af.device.get_device()
#            print "rank: "+str(self.rank)+", af.device.get_device(): "+str(af.device.get_device())+", self.af_device_id: "+str(self.af_device_id)
        
        kCheckpoint = 0 # 0 = no checkpoint
        
        # Check whether the code has been stopped mid-execution, and can be continued from a checkpoint.
        if os.path.exists("processedData/" + folder + "/checkpoint"):
            for k in range(n*(1 + self.pad) - 1, 0, -1):
                if os.path.exists("processedData/{0}/checkpoint/{1}/".format(folder, k)):
                    path, dirs, files = os.walk("processedData/{0}/checkpoint/{1}/".format(folder, k)).next()
                    file_count = len(files)
                    if file_count == 2*self.numOfBlocks:
                        kCheckpoint = k 
                        if self.rank == 0: 
                            print ("Using Checkpoint #{0}".format(k))
                        break
        else:
            if self.rank == 0:
                os.makedirs("processedData/{0}/checkpoint/".format(folder)) # Create checkpoint folder if one does not exist.
        self.kCheckpoint = kCheckpoint
        if not os.path.exists("results"):
            if self.rank == 0:
                os.makedirs("results") # Create results subfolder for current run if one does not exist.
        if not os.path.exists("results/{0}".format(folder)):
            if self.rank == 0:
                os.makedirs("results/{0}".format(folder))   

        # Initialize and save array which stores the final Cholesky factor.
        if self.rank==0:
            if not os.path.exists("results/{0}".format(folder + "_uc.npy")):
                uc = np.zeros((m*n,1), dtype='complex64')
                np.save("results/{0}".format(folder + "_uc.npy"), uc)
                
        # Ensure that files and directories are created before the rest of the nodes continue.
        initDone = np.array([0])
        
        self.comm.Bcast(initDone, root=0)
        
        
    def addBlock(self, rank):
        folder = self.folder
        b = Block(rank)
        k = self.kCheckpoint
        if k!= 0:
            A1 = np.load("processedData/{0}/checkpoint/{1}/{2}A1.npy".format(folder, k, rank))
            A2 = np.load("processedData/{0}/checkpoint/{1}/{2}A2.npy".format(folder, k, rank))
            b.setA1(A1) # Assigns A1 for current instance of Block
            b.setA2(A2) # Assigns A2 for current instance of Block
        else:
            if rank >= self.n:
                m = self.m
                b.createA(np.zeros((m,m), 'complex64')) # Assigns A1 and A2 for current instance of Block.
                
            else:
                T = np.load("processedData/{0}/{1}.npy".format(folder,rank))
                b.setT(T) # Assigns T for current instance of Block.
        b.setName("results/{0}_uc.npy".format(folder))
        self.blocks.addBlock(b)     
        return 

    #### ALGORITHM 3 ####
    def fact(self, method, p):
        if method not in np.array([SEQ, WY1, WY2, YTY1, YTY2]):
            raise InvalidMethodException(method)
        if p < 1 and method != SEQ:
            raise InvalidPException(p)
        
        
        pad = self.pad
        m = self.m
        n = self.n
        
        folder = self.folder
        
        if self.kCheckpoint==0:
            #### ALGORITHM 3: STEP 1 ####
            self.__setup_gen()

            # At this point, MPI processes with rank < n have:
            # A1 = T_rank * cinv        (2m x 2m matrix)
            # A2 = i * T_rank * cinv    (2m x 2m matrix)
            # MPI processes with rank >= n have:
            # A1 = 0                    (2m x 2m matrix)
            # A2 = 0                    (2m x 2m matrix)

            for b in self.blocks:
                if not pad and b.rank == n*(1 + pad) - 1:
                    b.updateuc(b.rank)
                    
        if (self.detailedSave):
            for b in self.blocks:        
                np.save("results/{0}/L_{1}-{2}.npy".format(folder, 0, b.rank), b.getA1())
        #### ALGORITHM 3: STEP 3 ####
        for k in range(self.kCheckpoint + 1,n*(1 + pad)):
            
            if self.rank == 0:
                print ("Loop {0} of {1}".format(k,2*n-1))
                
            self.k = k
            
            #### ALGORITHM 3: STEP 4 #### 
            # Build current generator at step k: A(k) = [A1(s1:e1,:) A2(s2:e2,:)]
            s1, e1, s2, e2 = self.__set_curr_gen(k, n) # Set s1, e1, s2, e2, work1, work2 for all MPI processes.
            
            #### ALGORITHM 3: STEP 5 ####
            # Reduce current generator A(k) to proper form.
            if method==SEQ:
                self.__seq_reduc(s1, e1, s2, e2)
            else:
                self.__block_reduc(s1, e1, s2, e2, m, p, method, k)
            
            # Save results immediately if we reached the end of the loop
            for b in self.blocks:
                if b.rank <=e1 and b.rank + k == n*(1 + pad) - 1:
                    b.updateuc(k%self.n)
                if b.rank <= e1 and self.detailedSave:
                    np.save("results/{0}/L_{1}-{2}.npy".format(folder, k, b.rank + k), -b.getA1())
                
            # CheckPoint
            saveCheckpoint = np.array([0])
            if self.rank==0:
                timePerLoop.append(time() - sum(timePerLoop) - startTime)
                
                elapsedTime = time() - startTime
                if elapsedTime + max(timePerLoop) >= MAXTIME: # Max instead of np.mean, just to be safe
                    print ("Saving Checkpoint #{0}".format(k))
                    if not os.path.exists("processedData/{0}/checkpoint/{1}/".format(folder, k)):
                        try:
                            os.makedirs("processedData/{0}/checkpoint/{1}/".format(folder, k))
                        except:
                            pass
                    saveCheckpoint = np.array([1])
            self.comm.Bcast(saveCheckpoint, root=0)
            
            if saveCheckpoint:
                for b in self.blocks:
                    # Creating Checkpoint
                    A1 = np.save("processedData/{0}/checkpoint/{1}/{2}A1.npy".format(folder, k, b.rank), b.getA1())
                    A2 = np.save("processedData/{0}/checkpoint/{1}/{2}A2.npy".format(folder, k, b.rank), b.getA2())
                exit()

    ## Private Methods
    
    #### ALGORITHM 3: STEP 1 ####
    def __setup_gen(self): # Sets up generator matrix A.
        n = self.n
        m = self.m
        pad = self.pad
        A1 = np.zeros((m, m), 'complex64')
        A2 = np.zeros((m, m), 'complex64')
        
        # The root rank will compute the cholesky decomposition
        if self.blocks.hasRank(0):
            c = cholesky(self.blocks.getBlock(0).getT())
            c = np.conj(c)
        else:
            c = np.empty((m,m),'complex64')
            
        self.comm.Bcast(c, root=0)

        for b in self.blocks:
            if b.rank < self.n:
#                b.createA(ctrsm(alpha=1.0, a=c, b=b.getT().T, lower=1).T)
                b.createA(ctrtrs(a=c, b=b.getT().T, lower=1)[0].T)
                
        # We are done with T.
        for b in self.blocks:
            b.deleteT()
        
        return A1, A2

    #### ALGORITHM 3: STEP 4 ####
	# Get indices used to construct the current generator A(k) of the kth Schur complement from the proper generator of the (k-1)th Schur complement.
    def __set_curr_gen(self, k, n):
        s1 = 0
        e1 = min(n, (n*(1 + self.pad) - k)) -1
        s2 = k
        e2 = e1 + s2
        
        # work1 and work2 are ranks which MPI messages are sent to/received from.
        for b in self.blocks:
            if s1 <= b.rank <=e1:
                b.setWork1(b.rank + k)
            else:
                b.setWork1(None)
            if e2 >= b.rank >= s2:
                b.setWork2(b.rank - k)
            else:
                b.setWork2(None)
        return s1, e1, s2, e2

    #### ALGORITHM 8 ####
    def __block_reduc(self, s1, e1, s2, e2, m, p, method, k):
        n = self.n
       
        X2_list = np.zeros((m, m+1), 'complex64')
        for sb1 in range (0, m, p):
            
            for b in self.blocks:
                b.setWork(None, None)
                if b.rank==0: b.setWork1(s2)
                if b.rank==s2: b.setWork2(0)
        
            sb2 = s2*m + sb1
            eb1 = min(sb1 + p, m) # next j
            eb2 = s2*m + eb1
            u1 = eb1
            u2 = eb2
            p_eff = min(p, m - sb1)
            
            temp =  np.zeros((p_eff, m+1), 'complex64')
            if method == WY1 or method == WY2:
                S = np.array([np.zeros((m,p)),np.zeros((m,p))], 'complex64')
            elif method == YTY1 or YTY2:
                S = np.zeros((p, p), 'complex64')
            
            
            for j in range(0, p_eff):
                j1 = sb1 + j
                j2 = sb2 + j
                
                #### ALGORITHM 5 #### 
                # Compute X2 and beta for jth Householder vector
                
                # The following function involves the passing of messages between rank=0 and rank=s2=k (both directions).
                data= self.__house_vec(j1, s2, j, b)
  
                temp[j] = data
                X2 = data[:self.m]
                beta = data[-1]
                
                # The following function involves the passing of messages between rank=0 and rank=s2=k (both directions).
                self.__seq_update(X2, beta, eb1, eb2, s2, j1, m, n)

            XX2 = temp[:,:m]
            if b.rank == s2 or b.rank == 0:
                S = self.__aggregate(S, XX2, beta, m, j, p_eff, method)
                self.__set_curr_gen(s2, n) # Updates work1, work2.
                
                # The following function involves the passing of messages between rank=0 and rank=s2=k (both directions).
                self.__new_block_update(XX2, sb1, eb1, u1, e1, s2,  sb2, eb2, u2, e2, S, m, p_eff)
            X2_list[sb1:sb1+p_eff,:] = temp
        
        b.createTemp(np.zeros((m, m+1), 'complex64'))
        b.setTemp(X2_list)
        
        if b.getCond()[0]:
            pass
        else:
            self.comm.Bcast(b.getTemp(), root=s2)
            
        temp = b.getTemp()
        for sb1 in range (0, m, p):
            
            for b in self.blocks:
                b.setWork(None, None)
                if b.rank==0: b.setWork1(s2)
                if b.rank==s2: b.setWork2(0)
        
            sb2 = s2*m + sb1
            eb1 = min(sb1 + p, m) # next j
            eb2 = s2*m + eb1
            u1 = eb1
            u2 = eb2
            p_eff = min(p, m - sb1)
            
            temp2 = temp[sb1:sb1+p_eff,:]
            XX2 = temp2[:,:m]
            beta = temp2[-1,-1]
            if method == YTY1 or YTY2:
                S = np.zeros((p, p), 'complex64')
            S = self.__aggregate(S, XX2, beta, m, j, p_eff, method)
            self.__set_curr_gen(s2, n) # Updates work
            self.__block_update(XX2, sb1, eb1, u1, e1, s2,  sb2, eb2, u2, e2, S, method)
        return
    
    def __new_block_update(self, X2, sb1, eb1, u1, e1,s2, sb2, eb2, u2, e2, S, m, p_eff):
        for b in self.blocks:
            num = self.numOfBlocks
            invT = S
            if b.rank == s2: # rank s2=k sends to rank 0.
                s = u1
                A2 = b.getA2()
                if s != m:
#                    B2 = cgemm(alpha=1.0, a=X2.T[:m, :p_eff], b=A2.T[:m, s:], trans_a=2).T
                    
                    # Tested on Tower (no errors; correct solution).
                    if use_gpu_Om3:
                        a_af = af.interop.np_to_af_array(A2[s:,:m])
                        b_af = af.interop.np_to_af_array(X2[:p_eff,:m])
                        B2_af = af.blas.matmul(a_af,b_af,rhs_opts=af.MATPROP.CTRANS)
                        B2 = np.array(B2_af,order='C')
                    else:
                        B2 = cgemm(alpha=1.0, a=X2.T[:m, :p_eff], b=A2.T[:m, s:], trans_a=2).T
                else:
                    B2 = np.array([],'complex64')
                self.comm.Send(B2, dest=b.getWork2()%self.size, tag=3*num + b.getWork2())
                del A2
                
            if b.rank == 0: # rank 0 receives from and sends to rank s2=k.
                s=u1
                
                A1 = b.getA1()
                B1 = A1[s:, sb1:eb1]
                
                B2 = np.empty((m - s, p_eff), 'complex64')
                self.comm.Recv(B2, source=b.getWork1()%self.size, tag=3*num + b.rank)  
                M = B1 - B2
                
                if s != m: # if M is nonempty
#                    M = ctrsm(alpha=1.0, a=invT.T[:p_eff,:p_eff], b=M.T, lower=1).T
#                    M = ctrtrs(a=invT.T[:p_eff,:p_eff], b=M.T, lower=1)[0].T
                    
                    # Tested on Tower (no errors; correct solution).
                    if use_gpu_Om3:
                        A_af = af.np_to_af_array(invT[:p_eff,:p_eff].T)
                        B_af = af.np_to_af_array(M.T)
                        X_af = af.lapack.solve(A=A_af, B=B_af,options=af.MATPROP.LOWER)
                        M = np.array(X_af).T
                    else:
                        M = ctrtrs(a=invT.T[:p_eff,:p_eff], b=M.T, lower=1)[0].T
                
                self.comm.Send(M, dest=b.getWork1()%self.size, tag=4*num + b.rank)
                A1[s:, sb1:eb1] = A1[s:, sb1:eb1] + M
                del A1   
    
            if b.rank == s2: # rank s2=k receives from rank 0.
                s = u1
                M = np.empty((m - s, p_eff), 'complex64')
                self.comm.Recv(M, source=b.getWork2()%self.size, tag=4*num + b.getWork2())
                
                if s != m: # if selection is nonempty
                    A2 = b.getA2()
#                    A2[s:, :m] = cgemm(alpha=1.0, a=X2.T, b=M.T, beta=1.0, c=A2.T[:m, s:]).T # Very slight improvement over numpy.dot()
                    
                    # Tested on Tower (no errors; correct solution).
                    if use_gpu_Om3:
                        a_af = af.interop.np_to_af_array(A2[s:,:m])
                        b_af = af.interop.np_to_af_array(M)
                        c_af = af.interop.np_to_af_array(X2)
                        a_af = a_af + af.matmul(b_af, c_af)
                        A2[s:, :m] = np.array(a_af)
                    else:
                        A2[s:, :m] = cgemm(alpha=1.0, a=X2.T, b=M.T, beta=1.0, c=A2.T[:m, s:]).T
                    
                    del A2 
        return 
    
    def __block_update(self, X2, sb1, eb1, u1, e1,s2, sb2, eb2, u2, e2, S, method):
        def yty2():
            invT = S
            for b in self.blocks: # ranks k+1, ..., min(n-1+k, 2n-1) send to (rank-k)
                if b.work2 == None: 
                    continue
                s = 0 
                if b.rank == s2:
                    continue
                A2 = b.getA2()
#                B2 = cgemm(alpha=1.0, a=X2.T[:m, :p_eff], b=A2.T[:m, s:], trans_a=2).T
                
                # Tested on Tower (no errors; correct solution).
                if use_gpu_Om3:
                    a_af = af.interop.np_to_af_array(A2[s:,:m])
                    b_af = af.interop.np_to_af_array(X2[:p_eff,:m])
                    B2_af = af.blas.matmul(a_af,b_af,rhs_opts=af.MATPROP.CTRANS)
                    B2 = np.array(B2_af,order='C')
                else:
                    B2 = cgemm(alpha=1.0, a=X2.T[:m, :p_eff], b=A2.T[:m, s:], trans_a=2).T
                
                self.comm.Send(B2, dest=b.getWork2()%self.size, tag=3*num + b.getWork2())
                
                del A2
                
            for b in self.blocks: # ranks 1, ..., min(n-1, 2n-1-k) receive from and send to (rank+k)
                if b.work1 == None: 
                    continue
                s = 0
                if b.rank == 0:
                    continue
                
                A1 = b.getA1()
                B1 = A1[s:, sb1:eb1]
                B2 = np.empty((m - s, p_eff), 'complex64')
                self.comm.Recv(B2, source=b.getWork1()%self.size, tag=3*num + b.rank)  
                
                M = B1 - B2
#                M = ctrsm(alpha=1.0, a=invT.T[:p_eff,:p_eff], b=M.T, lower=1).T
#                M = ctrtrs(a=invT.T[:p_eff,:p_eff], b=M.T, lower=1)[0].T

                if use_gpu_Om3:
                    A_af = af.np_to_af_array(invT[:p_eff,:p_eff].T)
                    B_af = af.np_to_af_array(M.T)
                    X_af = af.lapack.solve(A=A_af, B=B_af,options=af.MATPROP.LOWER)
                    M = np.array(X_af).T
                else:
                    M = ctrtrs(a=invT.T[:p_eff,:p_eff], b=M.T, lower=1)[0].T
                
                self.comm.Send(M, dest=b.getWork1()%self.size, tag=4*num + b.rank)
                A1[s:, sb1:eb1] = A1[s:, sb1:eb1] + M
                del A1
                
            for b in self.blocks: # ranks k+1, ..., min(n-1+k, 2n-1) receive from (rank-k)
                if b.work2 == None: 
                    continue
                s = 0 
                if b.rank == s2:
                    continue
                M = np.empty((m - s, p_eff), 'complex64')
                self.comm.Recv(M, source=b.getWork2()%self.size, tag=4*num + b.getWork2())
                
                A2 = b.getA2()
#                A2[s:, :m] = cgemm(alpha=1.0, a=X2.T, b=M.T, beta=1.0, c=A2.T[:m, s:]).T # Very slight improvement over numpy.dot()

                # Tested on Tower (no errors; correct solution).
                if use_gpu_Om3:
                    a_af = af.interop.np_to_af_array(A2[s:,:m])
                    b_af = af.interop.np_to_af_array(M)
                    c_af = af.interop.np_to_af_array(X2)
                    a_af = a_af + af.matmul(b_af, c_af)
                    A2[s:, :m] = np.array(a_af)
                else:
                    A2[s:, :m] = cgemm(alpha=1.0, a=X2.T, b=M.T, beta=1.0, c=A2.T[:m, s:]).T # Very slight improvement over numpy.dot()

                del A2 
            return 
        
        
        m = self.m
        n = self.n
        nru = e1*m - u1
        p_eff = eb1 - sb1 
        num = self.numOfBlocks
        
        if method == WY1:
            return wy1()
        elif method == WY2:
            return wy2()
        elif method ==YTY1:
            return yty1()
        elif method == YTY2:
            return yty2()
        
    def __aggregate(self,S,  X2, beta, m, j, p_eff, method):
#        invT = S
#        invT[:p_eff,:p_eff] = cherk(1.0, X2[:p_eff, :m].T, beta=-1.0, c=np.identity(p_eff, 'complex64').T, trans=2, lower=1, overwrite_c=0).T
#        for jj in range(p_eff):
#            invT[jj,jj] = (invT[jj,jj])/2.
        
        # Tested on Tower (no errors; correct solution).
        if use_gpu_Om3:
            a_af = af.interop.np_to_af_array(X2[:p_eff, :m])
            b_af = af.data.upper(af.blas.matmul(a_af, a_af, rhs_opts=af.MATPROP.CTRANS)) - af.data.identity(p_eff, p_eff, dtype=af.Dtype.c32)
            for jj in range(p_eff):
                b_af[jj,jj] *= 0.5
            invT = np.array(b_af)
        else:
            invT = S
            invT[:p_eff,:p_eff] = cherk(1.0, X2[:p_eff, :m].T, beta=-1.0, c=np.identity(p_eff, 'complex64').T, trans=2, lower=1, overwrite_c=0).T
            for jj in range(p_eff):
                invT[jj,jj] = (invT[jj,jj])/2.
        
        return invT
        
    
    def __seq_reduc(self, s1, e1, s2, e2):
        n = self.n
        m = self.m
        for j in range (0, self.m):
            X2, beta = self.__house_vec(j, s2)
            
            self.__seq_update(X2, beta, e1*m, e2*m, s2, j, m, n)

    def __seq_update(self,X2, beta, e1, e2, s2, j, m, n):
        u = j + 1
        num = self.numOfBlocks
        
        nru = e1*m - (s2*m + j + 1)  
        for b in self.blocks: # rank s2=k sends to rank 0.
            if b.work2 == None: 
                continue
#            B1 = b.getA2().dot(np.conj(X2.T)) # sizes independent of j. Can't improve with zgemv

            # Tested on Tower (no errors, correct solution).
            if use_gpu_Om2:
                a_af = af.interop.np_to_af_array(b.getA2())
                b_af = af.interop.np_to_af_array(np.conj(X2.T))
                B1_af = af.blas.matmul(a_af,b_af)
                B1 = np.array(B1_af)
            else:
                B1 = b.getA2().dot(np.conj(X2.T))
            
            start = 0
            end = m
            if b.rank == s2:
                start = u
            if b.rank == e2/m:
                end = e2 % m or m
            B1 = B1[start:end] # size decreases with j.
            self.comm.Send(B1, dest=b.getWork2()%self.size, tag=4*num + b.getWork2())

        
        for b in self.blocks:# rank 0 receives from and sends to rank s2=k.
            if b.work1 == None:
                continue
            start = 0
            end = m
            if b.rank == 0:
                start = u
            if b.rank == e1/m:
                end = e1 % m or m
            B1 = np.empty(end-start, 'complex64') # size decreases with j.
            
            self.comm.Recv(B1, source=b.getWork1()%self.size, tag=4*num + b.rank)
            A1 = b.getA1()
            B2 = A1[start:end, j] # size decreases with j.
                
            v = B2 - B1 # size decreases with j.
            self.comm.Send(v, (b.getWork1())%self.size, 5*num + b.getWork1())
            A1[start:end,j] -= beta*v # size decreases with j.
            
            del A1

        for b in self.blocks:# rank s2=k receives from rank 0.
            if b.work2 == None: 
                continue
            start = 0
            end = m
            if b.rank == s2:
                start = u
            if b.rank == e2/m :
                end = e2 % m or m
            v = np.empty(end-start, 'complex64') # size decreases with j.
            self.comm.Recv(v, source=b.getWork2()%self.size, tag=5*num + b.rank)
            if start != end:
                A2 = b.getA2()
#                cgeru(-beta, X2, v, incx=1, incy=1, a=A2.T[:,start:end], overwrite_x=0, overwrite_y=0, overwrite_a=1)# size of v decreases with j.

                # Tested on Tower (no errors; correct solution).
                if use_gpu_Om2:
                    a_af = af.interop.np_to_af_array(A2[start:end,:])
                    b_af = af.interop.np_to_af_array(beta*v)
                    c_af = af.data.moddims(af.interop.np_to_af_array(X2), 1, d1=X2.shape[0])
                    a_af = a_af - af.blas.matmul(b_af,c_af)
                    A2[start:end,:] = np.array(a_af)
                else:
                    cgeru(-beta, X2, v, incx=1, incy=1, a=A2.T[:,start:end], overwrite_x=0, overwrite_y=0, overwrite_a=1)
                    
                del A2
        
    def __house_vec(self, j, s2, j_count, b):
        isZero = np.array([0])
        b.setFalse(isZero)
        
        X2 = np.zeros(self.m, 'complex64')
        data = np.zeros(self.m+1, 'complex64')
        beta = np.zeros(1, 'complex64')
        z = np.zeros(1, 'complex64')
        sigma = np.zeros(1, 'complex64')
        blocks = self.blocks
        n = self.n
        num = self.numOfBlocks
        
        if self.rank == s2:
            A2 = blocks.getBlock(s2).getA2()
            if np.all(np.abs(A2[j, :]) < 1e-32): # This number was set to 1e-13, which led to highly inaccurate solutions when called. 
                isZero=np.array([1])
                b.setTrue(isZero)
            self.comm.Bcast(b.getCond(), root=s2%self.size)
            del A2
        else:
            self.comm.Bcast(b.getCond(), root=s2%self.size)
        
        if b.getCond()[0]:
            print (isZero)
            data[:self.m] = X2
            data[-1] = beta[0] 
            b.setTemp(data)
            return data
        
        if self.rank == s2: # rank s2=k sends to and receives from rank 0.
            A2 = blocks.getBlock(s2).getA2()
            sigma[0] = scnrm2(A2.T[:, j])**2
            
            self.comm.Send(sigma, dest=0, tag=2*num + s2)
            self.comm.Recv(z, source=0, tag=3*num + s2)
            self.comm.Recv(beta, source=0, tag=4*num + s2)

            X2 = A2[j,:]/z
            A2[j, :] = X2
            
            data[:self.m] = X2
            data[-1] = beta[0] 
            b.setTemp(data)
            self.comm.Send(data, dest=0, tag=5*num + s2)
            del A2
            
        if self.rank == 0: # rank 0 receives from and sends to rank s2=k
            A1 = blocks.getBlock(0).getA1()
            self.comm.Recv(sigma, source=s2%self.size, tag=2*num + s2)
            alpha = (A1[j,j]**2 - sigma)**0.5
            x = sigma/A1[j,j]**2
            if (np.absolute(x) < 1e-6) and (A1.real[j,j] < 0):
#                print "Using expansion to calculate z."
                z[0] = A1[j,j]*x/2
                A1[j,j] = -alpha[0]
            else:
                z[0] = A1[j, j]+alpha[0]
                A1[j,j] = -alpha[0]
            
            beta[0] = (2./(1.-sigma/z**2))

            self.comm.Send(z, dest=s2%self.size, tag=3*num + s2)
            self.comm.Send(beta, dest=s2%self.size, tag=4*num + s2)
            self.comm.Recv(data, source=s2%self.size, tag=5*num + s2)
            del A1

        return data # X2, beta

