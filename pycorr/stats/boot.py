from numpy.random import randint
from scipy.stats import norm
import numpy as np

def circ(start, l, maxlen):
    """Change start index in-place, so end indexes can grab next l elements in circular manner"""
    indx_edge = maxlen - start < l
    start[indx_edge] = start[indx_edge] - maxlen

def bootstrap_ts_indexes(data, l, n_samples=10000, method='circular'):
    """
    Given data points data, where axis 0 is considered to delineate points, return
    an array where each row is a set of bootstrap indexes. This can be used as a list
    of bootstrap indexes as well.
    """
    npoints = data.shape[0]
    if method == 'circular': 
        N = int(np.ceil(npoints / float(l)))
        blocks = randint(npoints, size=(n_samples, N))
        circ(blocks, l, maxlen=npoints)
        expand_block = lambda block: np.array([block + ii for ii in xrange(l)]).flatten(order='F')
        indexes = np.array([expand_block(block) for block in blocks])
    return indexes[..., :npoints]


from statsmodels.tsa.stattools import acf, ccovf

def lam(s):
  return (np.abs(s)>=0)*(np.abs(s)<0.5) + 2*(1-np.abs(s))*(np.abs(s)>=0.5)*(np.abs(s)<=1)

# PORT of b.star function from R
def b_star(data, Kn = None, mmax = None, Bmax = None, c = None,
           round = True):
    if len(data.shape) == 1: data = data.reshape([-1,1])
    elif len(data.shape) > 2: raise Exception('data may not be greater than 2d')

    n, k = data.shape
    if Kn   is None: Kn   = max(5, np.ceil(np.log(n)))
    if mmax is None: mmax = np.ceil(np.sqrt(n)) + Kn
    if Bmax is None: Bmax = np.ceil(min(3 * np.sqrt(n), n/3.))
    if c    is None: c    = norm.ppf(0.975)
    BstarCB = np.empty(k)         # TODO: Risky to initialize as empty?
    for ii in range(k):
        rho_k = acf(data[:,ii])[1:]
        rho_k_crit = c * np.sqrt(np.log10(n)/n)
        insig = np.abs(rho_k) < rho_k_crit
        num_insig = np.array([np.sum(insig[jj:jj + Kn]) for jj in range(int(mmax-Kn+1))])
        
        # Determine mhat
        if np.any(num_insig == Kn): 
            # mhat is indx of first with Kn insig
            mhat = np.where(num_insig == Kn)[0][0] + 1   #[indx_tuple][arr_pos]
        elif np.any(np.abs(rho_k) > rho_k_crit):
            # mhat is indx of max rho_k greater than rho_k_crit
            mhat = max(np.where(np.abs(rho_k) > rho_k_crit)) + 1
        else: mhat = 1
        
        M = min(2*mhat, mmax)
        kk = np.arange(-M, M+1.)
        
        ccov_right = ccovf(data[:,ii], data[:,ii], unbiased=False)[:M+1]       # M+1 to include lag 0
        R_k = np.concatenate([ccov_right[-1:0:-1], ccov_right])
        Ghat = np.sum(lam(kk / M) * np.abs(kk) * R_k)
        DCBhat = 4./3 * np.sum(lam(kk/M) * R_k)**2

        BstarCB[ii] = (2 * Ghat**2 / DCBhat)**(1/3.) * n**(1/3.)

    if round: 
        BstarCB[BstarCB > Bmax] = Bmax
        BstarCB[BstarCB < 1] = 1
        np.round(BstarCB, out=BstarCB)
    
    return BstarCB
   
def ts_boot(dlist, func, l, n_samples=10000, method='circular', out=None, indx_file='', **kwargs):
    """Return func results from bootstrapped samples of data
    
    Parameters:
        data (ndarray): data to draw bootstrap samples from last dim
        func:           func to run over new samples
        n_samples:      number of bootstrapped samples to draw
        kwargs:         additional arguments for func
    """
    assert len(np.unique([data.shape[-1] for data in dlist])) == 1    #All have same number timepoints
    
    example_tc = dlist[0].T
    if out is None: out = [None] * n_samples
    
    if not indx_file: indexes = bootstrap_ts_indexes(example_tc, l, n_samples)
    else: indexes = np.load(indx_file)

    for ii, boot_indx in enumerate(indexes):
        sample = [data[..., boot_indx] for data in dlist]
        out[ii] = func(sample, **kwargs)
    return np.array(out, copy=False)

from pycorr.funcs_correlate import intersubcorr, crosscor
def isc_within_boot(dlist, standardized=False):
    intersubcorr(crosscor(dlist, standardized=True))
    
    
def run_boot_within_isc_diff(A, B, l, n_reps, out_arr=None):
    out = {}

    out_shape = (n_reps, ) + A[0].shape[:-1]      #n_reps x spatial_dims
    out_arr = np.zeros(out_shape, dtype=float)
    swap_dims = range(1,len(out_shape)) + [0]                        #list with first and last dims swapped

    calc_mean_isc = lambda dlist: intersubcorr(crosscor(dlist, standardized=True)).mean(axis=-1)
    out['distA'] = ts_boot(A, calc_mean_isc, l, n_samples=n_reps, out = out_arr.copy())
    out['distB'] = ts_boot(B, calc_mean_isc, l, n_samples=n_reps, out = out_arr.copy())
    for k in ['distA', 'distB']: out[k] = out[k].transpose(swap_dims)
    out['r'] = (calc_mean_isc(A) - calc_mean_isc(B))[..., np.newaxis] #since 1 corr, add axis for broadcasting
    out['p_gt'] = (out['distA'] - out['distB'] > 0).mean(axis=-1)
    out['p_ltq'] = (out['distA'] - out['distB'] <= 0).mean(axis=-1)
    return out