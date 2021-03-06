import h5py
import nibabel as nib
from funcs_correlate import shift, standardize
from pietools import load_nii_or_npy, is_numeric
import numpy as np

class AttrArray(np.ndarray):
    """Subclass of ndarray. Provides attributes property to match hdf5 dset.
    
    This is used when data are not loaded into hdf5, but externally"""
    def __new__(self, np_arr, attrs):
        obj = np_arr.view(self)
        obj.attrs = attrs
        return obj

class Run:
    """Class to wrap /Subjects/sub_id/Cond group"""

    def __init__(self, h5grp):
        """Load individual scan, given Group object.

        """
        self.grp = h5grp
        #self.data    = h5grp['data']    if 'data'   in h5grp else None
        self.thresh  = h5grp['thresh']  if 'thresh' in h5grp else None
        self.attrs = h5grp.attrs

    @property
    def data(self):
        if not self.grp.get('data'): return None            # no data exists
        attrs = self.grp['data'].attrs
        if attrs.get('reference'):
            d =  load_nii_or_npy(attrs['reference'])
            return AttrArray(d, attrs)                      # data from external npy or nifti
        else:
            return self.grp['data']                         # data from hdf5

    @property
    def subset(self):
        sub = self.data.attrs['subset']
        if type(sub) is str:              # load from string
            return np.load(sub)
        elif hasattr(sub, 'shape'):       # boolean array
            return sub
        else:                             # no subset
            return None
        
    def load(self, use_subset=True, standardized=False, threshold=False, roi=False, _slice=None):
        """
        Return data as a numpy array. Uses data attributes to shift, and (optionally) threshold.
        Final ROI timecourses are standardized.

        Parameters:
        subset       --  boolean mask for subsetting timecourse
        standardized --  demean and scale timecourse for each voxel
        threshold    --  set thresholded values to nan
        roi          --  roi mask to subset spatial dims
        _slice       --  slice along first dimension to take

        """
        # Subset loading
        sub_path = self.data.attrs['subset']
        if is_numeric(use_subset) and type(use_subset) != bool:     # subset given
            subset = use_subset
        elif use_subset and np.any(sub_path):                         # else use from run attrs
            subset = np.load(sub_path) if type(sub_path) is str else sub_path
        else:
            subset = slice(None)           #to preserve array shapes when subsetting

        # ROI 
        if np.any(roi):
            if threshold: roi = roi & ~self.thresh[...]
            M_roi = self.data[...][roi]
            shifted = shift(M_roi, h=self.data.attrs['offset'], outlen=self.data.attrs['max_len'])   #shift last dim forward h
            final = shifted[..., subset]
            if standardized: standardize(final, inplace=True)
            return standardize(final.mean(axis=0))               #restandardize, and collapse the vox X ts 2D mat TODO: why is this done?

        # Full 4D array
        else: 
            data = self.data[_slice][np.newaxis,...] if _slice is not None else self.data           # sliced (for parallel jobs)
            shifted = shift(data, h=self.data.attrs['offset'], outlen=self.data.attrs['max_len'])   # shift last dim forward h
            final = shifted[..., subset]
            if standardized: standardize(final, inplace=True)
            if threshold: 
                thresh = self.thresh[_slice][np.newaxis,...] if _slice is not None else self.thresh[...]
                final[thresh] = np.nan
        
            return final

    def threshold(self, threshold, data=None, save=False):
        """Boolean mask of values below threshold or that are nan.

        Parameters:
        threshold -- value that mean timecourse must exceed
        data      -- if given, threshold this data set. Otherwise, load run data.
        save      -- save threshold to dataset "threshold" in addition to returning

        """
        data = data if not data is None else self.load()
        thresh_mask = ~(data.mean(axis=-1) > threshold)    #thresh anything not over threshold (including nan)
        
        if save:
            self.thresh = self.grp.require_dataset('thresh', shape=self.data.shape[:-1], dtype=bool)
            assert self.thresh.shape == thresh_mask.shape           #TODO create thresh if not exist
            self.thresh[...] = thresh_mask
            self.thresh.attrs['threshold'] = threshold
        self.grp.file.flush()
        return thresh_mask

    def create_dataset(self, data, overwrite=False, reference=False, compression='gzip', chunks=(1,), **kwargs):
        """Data can be np.ndarray, nifti, or file name. Remaining dimensions for chunks are inferred from data
        
        Even if ref is True, still loads data (to ensure it exists)
        """
        #TODO should fill attributes be mandatory?
        #TODO ref and overwrite args
        if self.data and not overwrite:
            raise BaseException('data already exists')
        if type(data) is str: np_arr = load_nii_or_npy(data)                #string
        elif type(data) is nib.nifti1.Nifti1Image: np_arr = data.get_data() #nifti
        else: np_arr = data                                                 #np array
        #rdy_arr = np_arr.reshape([-1, np_arr.shape[-1]])
        chunks += np_arr.shape[len(chunks):]             #fill in rest of chunk information
        if reference:
           self.grp.create_group('data')                 #make dummy group for attrs 
           reference = data                              # file path
        else:
            self.grp.create_dataset('data', data=np_arr, chunks=chunks, compression=compression)
        if kwargs: self.fill_attributes(reference=reference, **kwargs)
        self.grp.file.flush()
        #self.data.attrs['shape'] = np_arr.shape

    def fill_attributes(self, offset=0, max_len=None, exclude=False, notes="", reference=False, subset=False, **kwargs): #TODO should initial arguments be set from Exp setup, so they can be in the yaml?
        """Kwargs are unused (there to soak up uneccesary args)"""
        if not self.data: raise BaseException('data does not exist')
        if not max_len: max_len = self.data.shape[-1]
        self.data.attrs['offset'] = offset
        self.data.attrs['max_len'] = max_len
        self.data.attrs['exclude'] = exclude
        self.data.attrs['notes'] = notes
        self.data.attrs['reference'] = reference
        self.data.attrs['subset'] = subset
        #'blocks': pandas,
        #nii_hdr,
        #'date_scanned': unix date?
        self.grp.file.flush()

    def copy(self, new_cond):
        g_sub = self.grp.parent
        if new_cond in g_sub: Exception("group %s already exists"%new_cond)  #otherwise will create as subgroup
        g_sub.copy(self.grp.name, new_cond)
        return Run(g_sub[new_cond])
        
    def summary(self):
        print 'Data:\t',self.data
        print 'thresh:\t', self.thresh
        width = max(map(len, self.data.attrs))
        for key, val in self.data.attrs.iteritems(): 
            print "{:{}}".format(key, width), ':    ', val


import os
from pietools import query_to_re_groups
from glob import glob
import re
from funcs_correlate import sum_tc

class Exp:
    """

    """

    def __init__(self, f):
        self.f = h5py.File(f) if type(f) is str else f

    def __getitem__(self, x):
        return self.f['conds'][x]

    def setup(self, config, create_conds=False):
        """Create general structure and load in data.

        """
        #if 'data_storage' in config:
        #    for k,v in config['data_storage'].iteritems():
        #        self.f.attrs[k] = v

        self.f.create_group('subjects')
        self.f.create_group('rois')
        self.f.create_group('conds')
        self.f.attrs['sub_folder'] = config['sub_folder']
        try: os.mkdir(config['sub_folder'])
        except: pass #TODO add exception expected

        #Load roi files in from config
        if 'roi_files' in config:
            for m_roi in self.get_subject_files(config['roi_files']):
                fname = m_roi.group()
                roi_id= m_roi.groupdict()['roi_id']
                self.create_roi(roi_id, fname)
        #create new conditions
        if create_conds:
            for condname, cond in config['conds'].iteritems():
                self.create_cond(condname, **cond)
        self.f.flush()

    def create_cond(self, condname, run=None, group=None, offset=0, max_len=False, threshold=0, audio_env=None, 
                    base_dir="", nii_files=None, dry_run=False, reference=False, **kwargs):
        """Create condition.

        Generally used by passing cond params from setup, but can be called manually.
        All parameters set as default for each run in the condition.

        Parameters:
            reference: should the data be copied in, or use a reference to data instead?
        """
        cond = self.f['conds'].create_group(condname)
        cond.attrs['offset'] = offset
        cond.attrs['max_len'] = max_len or False     #can't store None
        cond.attrs['threshold'] = threshold
        cond.attrs['prop_pass_thresh'] = .7
        cond.attrs['run'] = run or condname
        cond.attrs['reference'] = reference
        if group: cond.attrs['group'] = group
        cond.create_group('blocks')
        cond.create_group('correlations')
        cond.create_group('analyses')
        
        #Load conditions in from config
        
        if audio_env:
            aud_path = os.path.join(base_dir, audio_env)
            print aud_path
            cond.create_dataset('audio_env', data=load_nii_or_npy(aud_path))
        
        #Don't attempt to load data when nii_files is blank (dummy cond)
        if not nii_files: return

        #Create subject data
        full_query = os.path.join(base_dir, nii_files)
        for m in self.get_subject_files(full_query):
            fname = m.group()
            sub_id = m.groupdict()['sub_id'] #TODO add run_id to mix
            print fname, sub_id
            if not dry_run: self.create_subrun(sub_id, condname, fname, **cond.attrs)
            #have option to do threshold?
            self.f.flush()

        #TODO blocks (pandas)
        if dry_run:
            for k, v in cond.attrs.iteritems(): print k, ':\t\t', v
            del self.f[cond.name]
        return cond
    
    def create_subrun(self, sub_id, condname, fname_or_arr, reference=False, **kwargs):
        """Creates subject group for storing individual runs.

        """
        path = '%s/%s'%(sub_id, condname)
        #Remote link if sub_folder is specified
        if self.f.attrs['sub_folder'] and not sub_id in self.f['subjects']:
            fname = os.path.join(self.f.attrs['sub_folder'], sub_id+'.h5')
            with h5py.File(fname) as f_new:
                f_new.create_group(sub_id)
            self.f['subjects/%s'%sub_id] = h5py.ExternalLink(fname, sub_id)

        #add condition to subject group
        g_sub = self.f['subjects'].create_group('%s/%s'%(sub_id, condname))
        run = Run(g_sub)
        run.create_dataset(data=fname_or_arr, reference=reference, **kwargs)

        return run

    def get_cond(self, condname):
        #TODO change to getitem method?
        return self.f['conds/%s'%condname]

    def iter_runs(self, condname, group=None):
        """Iterate through all subjects and all runs within subject. Yield run with same name as run attr in cond
        
        Parameters:
        condname --  string specifying condition or condition object
        group    --  group name that must also match in the group attr for each run
        """
        cond = self.get_cond(condname) if hasattr(condname, 'upper') else condname #TODO this is hacky
        for sname, sub in self.f['subjects'].iteritems():
            for run_name, raw_run in sub.iteritems():
                run = Run(raw_run)
                group = group if group else cond.attrs.get('group')
                ingroup = not group or run.attrs.get('group') == group  #TODO print warning if no group set for run? need to set group in Run class
                if run_name == cond.attrs['run'] and ingroup: yield run

    def N_runs(self, condname):
        return len(list(self.iter_runs(condname)))

    def gen_composite(self, condname, overwrite=False):
        """Create linear composite for condition.
        """
        #TODO overwrite
        data = (run.load(standardized=True, threshold=True) for run in self.iter_runs(condname))
        shape = self.iter_runs(condname).next().load().shape
        composite = sum_tc(data, shape=shape, standardize_out=False)
        cond = self.get_cond(condname)
        dset = cond.require_dataset('composite', shape=composite.shape, dtype=composite.dtype)
        dset[...] = composite

    def gen_cond_thresh(self, condname, overwrite=False):
        """Create a group thresholding mask.
        """
        cond = self.get_cond(condname)
        dlist = [~run.thresh[...] for run in self.iter_runs(condname)]              #all that pass threshold
        thresh_fail = self.cond_thresh(dlist, cond.attrs['prop_pass_thresh'])

        if 'threshold' not in cond:
            cond.create_dataset('threshold', data=thresh_fail, dtype=bool)
        else: 
            cond['threshold'][...] = thresh_fail

    @staticmethod
    def cond_thresh(dlist, mustpassprop):
        """For each voxel, determine whether enough subs have high enough mean activation.
        """
        n_must_pass = (mustpassprop) * len(dlist)
        above_thresh = np.sum(dlist, axis=0)
        thresh_fail = above_thresh < n_must_pass 			#sum num of failed subjects per voxel
        return thresh_fail

    def summarize(self, condname):
        cond = self.get_cond(condname)
        def printvalue(name):
            print name, ':\t', cond[name]
        cond.visit(printvalue)

    @staticmethod
    def get_subject_files(globpath):
        """Iterator. Returns re match object for each matching path.
        
        Full path accessed using m.group(); args from m.groupdict()
        """
        #to_glob = globpath.format(sub_id='*')
        to_glob, to_re = query_to_re_groups(globpath)   #for getting sub_id out of results
        nii_files = glob(to_glob)
        
        for fname in nii_files:
            yield re.match(to_re, fname)

    def create_roi(self, roiname, fname):
        """Add new roi to hdf5 in /rois group"""
        roi_data = load_nii_or_npy(fname)
        roi = self.f['rois'].create_dataset(roiname, data=roi_data.astype(bool))
        return roi

class Cond:
    """
    """
    def __init__(self):
        pass


