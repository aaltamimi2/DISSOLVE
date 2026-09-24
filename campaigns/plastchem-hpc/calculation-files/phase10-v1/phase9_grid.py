"""Vectorized independent COSMOspace iterations for an initial binary grid.

Same 24a tau, starting Gamma=1, 0.7 damping, relative convergence criterion and
returned (pre-update) Gamma as opencosmorspy. Rows stop independently. Original
engine pure references and combinatorial term are used. This is only for the
initial grids: nonlinear tie-line probes still call the unmodified engine.
"""
import numpy as np


def binary_grid(engine,xs,T):
    assert len(engine.enth.mol_lst)==2
    assert not engine.par.calculate_contact_statistics_molecule_properties
    xs=np.asarray(xs,dtype=float)
    x=np.c_[xs,1-xs]
    n=len(engine.enth.segtp_collection)
    areas=np.zeros((2,n))
    for i,mol in enumerate(engine.enth.mol_lst):
        for k,value in mol.get_segtp_area_dct().items():areas[i,k]=value
    X=x@areas;X/=X.sum(axis=1)[:,None]
    tau=engine.enth.get_interaction_arrays(T)['tau']
    gamma=np.ones_like(X);active=np.ones(len(xs),dtype=bool)
    for iteration in range(1,engine.par.cosmospace_max_iter+2):
        indices=np.flatnonzero(active)
        old=gamma[indices]
        new=1/((X[indices]*old)@tau.T)
        converged=np.all(np.abs(new-old)/old<engine.par.cosmospace_conv_thresh,axis=1)
        active[indices[converged]]=False
        remaining=indices[~converged]
        # The scalar implementation returns the old Gamma on convergence.
        gamma[remaining]=.7*(new[~converged]-old[~converged])+old[~converged]
        if not active.any():break
    if active.any():raise ValueError('COSMOspace did not converge for binary grid')
    ln_gamma=np.log(gamma)@(areas/engine.par.a_eff).T
    ln_gamma+=np.array([engine.comb.calculate(row,T)['lng'] for row in x])
    # Pure reference states use the original engine's numerical implementation.
    reference=[]
    for i in range(2):
        pure=np.zeros(2);pure[i]=1.
        enth=engine.enth.calculate(pure,T)['lng']
        comb=engine.comb.calculate(pure,T)['lng']
        reference.append(float(enth[i]+comb[i]))
    ln_gamma-=np.array(reference)
    assert np.isfinite(ln_gamma).all()
    return ln_gamma
