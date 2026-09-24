"""Binary liquid-liquid coexistence from activities, never from infinite dilution alone."""
import math
import numpy as np
from scipy.optimize import least_squares
from scipy.special import expit, logit

def mixing_g(x,lng):
 x=np.asarray(x);lng=np.asarray(lng)
 return x*np.log(x)+(1-x)*np.log1p(-x)+x*lng[:,0]+(1-x)*lng[:,1]

def lower_hull(x,g):
 hull=[]
 for i in range(len(x)):
  while len(hull)>=2:
   a,b=hull[-2:]
   if (g[b]-g[a])*(x[i]-x[b]) < (g[i]-g[b])*(x[b]-x[a]):break
   hull.pop()
  hull.append(i)
 return hull

def solve_lle(evaluate,mw_solute,mw_solvent,grid_sizes=(1000,2000)):
 """evaluate(xs)->ln gamma rows; endpoints pure. Refine chemical-potential equality.

 Global stability is tested on both independent grids, plus dense samples near
 each tie line. A converged result needs consistent endpoints and <=1e-7 mu
 residual. Grid resolution is an explicit numerical qualification, not proof
 against arbitrarily narrow miscibility gaps.
 """
 results=[]
 for n in grid_sizes:
  xs=np.unique(np.r_[np.arange(1,n)/n,np.geomspace(1e-14,1e-3,45),1-np.geomspace(1e-14,1e-3,45)])
  lng=np.asarray(evaluate(xs));assert lng.shape==(len(xs),2) and np.isfinite(lng).all()
  x=np.r_[0,xs,1];g=np.r_[0,mixing_g(xs,lng),0];hull=lower_hull(x,g)
  gaps=[(a,b) for a,b in zip(hull[:-1],hull[1:]) if b-a>1 and np.max(g[a:b+1]-np.interp(x[a:b+1],[x[a],x[b]],[g[a],g[b]]))>1e-9]
  ties=[];issues=[]
  for ia,ib in gaps:
   xa=max(x[ia],1e-14);xb=min(x[ib],1-1e-14);middle=(xa+xb)/2
   def residual(z):
    xx=expit(z);v=evaluate(xx);mu=np.c_[np.log(xx),np.log1p(-xx)]+v
    return mu[0]-mu[1]
   candidates=[]
   for initial in [[xa,xb],[max(xa*.5,1e-14),min(1-(1-xb)*.5,1-1e-14)],[(xa+middle)/2,(xb+middle)/2]]:
    fit=least_squares(residual,logit(initial),bounds=([-34,logit(middle)],[logit(middle),34]),xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=200,diff_step=1e-4)
    endpoints=expit(fit.x);error=float(np.max(np.abs(residual(fit.x))))
    if endpoints[1]-endpoints[0]>1e-7:candidates.append((error,fit))
   if not candidates:issues.append({'reason':'only_trivial_equal_composition_roots'});continue
   _,fit=min(candidates,key=lambda pair:pair[0])
   xa,xb=expit(fit.x);err=float(np.max(np.abs(residual(fit.x))))
   endpoint_g=mixing_g(np.array([xa,xb]),evaluate([xa,xb]));slope=(endpoint_g[1]-endpoint_g[0])/(xb-xa);intercept=endpoint_g[0]-slope*xa
   tangent_min=float(np.min(g-(intercept+slope*x)))
   probe=np.unique(np.r_[np.linspace(xa,xb,101),np.geomspace(max(xa/10,1e-14),min(xa*10,.999999),41),1-np.geomspace(max((1-xb)/10,1e-14),min((1-xb)*10,.999999),41)])
   tangent_min=min(tangent_min,float(np.min(mixing_g(probe,evaluate(probe))-(intercept+slope*probe))))
   tie={'x_solvent_rich':float(xa),'x_solute_rich':float(xb),'chemical_potential_residual':err,'minimum_tangent_distance_RT':tangent_min}
   if err>1e-7 or tangent_min < -1e-7 or xb-xa<1e-7:issues.append(tie)
   else:ties.append(tie)
  if issues:results.append({'grid_intervals':n,'status':'unresolved_tie_line','issues':issues,'tie_lines':ties});continue
  ties.sort(key=lambda t:t['x_solvent_rich']);solubility=ties[0]['x_solvent_rich'] if ties else 1.
  results.append({'grid_intervals':n,'status':'two_liquid_phases' if ties else 'single_liquid_phase','tie_lines':ties,'solute_mole_fraction_solubility':solubility,'solute_wt_percent_solubility':100*solubility*mw_solute/(solubility*mw_solute+(1-solubility)*mw_solvent)})
 final=dict(results[-1]);final['grid_checks']=results
 if len(results)>1:
  if any('solute_mole_fraction_solubility' not in r for r in results) or len({r['status'] for r in results})!=1:final['status']='grid_or_tie_line_unresolved'
  else:
   dx=abs(results[-1]['solute_mole_fraction_solubility']-results[-2]['solute_mole_fraction_solubility'])*100
   dw=abs(results[-1]['solute_wt_percent_solubility']-results[-2]['solute_wt_percent_solubility']);final.update(grid_change_mol_percentage_points=dx,grid_change_wt_percentage_points=dw)
   if max(dx,dw)>.01:final['status']='grid_not_converged'
 if final['status'] in ['single_liquid_phase','two_liquid_phases']:
  for basis,value in [('mol',100*final['solute_mole_fraction_solubility']),('wt',final['solute_wt_percent_solubility'])]:final['above_15_'+basis+'_percent']=None if abs(value-15)<=.01 else bool(value>15)
 return final

if __name__=='__main__':
 # Independent analytic regular-solution regression: chi<=2 miscible; chi=3 symmetric gap.
 for chi in [0.,1.,3.]:
  def analytic(xs):
   x=np.asarray(xs);return np.c_[chi*(1-x)**2,chi*x*x]
  r=solve_lle(analytic,100,100,grid_sizes=(100,200))
  if chi<=2:assert r['status']=='single_liquid_phase' and r['solute_mole_fraction_solubility']==1
  else:
   t=r['tie_lines'][0];a=t['x_solvent_rich'];b=t['x_solute_rich'];assert abs(a+b-1)<1e-7 and abs(math.log(a/(1-a))+chi*(1-2*a))<1e-7 and abs(a-.07072018168)<1e-7
  print(chi,r['status'],r.get('solute_mole_fraction_solubility'))
