"""Independent stored LLE arithmetic/qualification checks, no solver import."""
import math


def near(a,b,tolerance=1e-9):
    assert math.isfinite(a) and math.isfinite(b)
    assert abs(a-b)<=tolerance,(a,b,abs(a-b))


def check(value,solute_mass,solvent_mass,failure_policy_sha):
    status=value['status'];valid=status in ['single_liquid_phase','two_liquid_phases']
    assert value['value_validated'] is valid
    assert status in ['single_liquid_phase','two_liquid_phases','grid_or_tie_line_unresolved',
                      'grid_not_converged','activity_nonconvergence']
    if status=='activity_nonconvergence':
        assert value['failure_mode']=='cosmospace_binary_grid_nonconvergence'
        assert value['exception_type']=='ValueError'
        assert value['exception_message']=='COSMOspace did not converge for binary grid'
        assert 'ValueError: '+value['exception_message'] in value['traceback']
        assert value['failure_policy_sha256']==failure_policy_sha
        assert value['grid_checks']==[] and value['tie_lines']==[]
        for name in ['solute_mole_fraction_solubility','solute_wt_percent_solubility',
                     'above_15_mol_percent','above_15_wt_percent']:
            assert value[name] is None,name
        return
    grids=value['grid_checks'];assert [g['grid_intervals'] for g in grids]==[1000,2000]
    if not valid:
        assert value['above_15_mol_percent'] is None and value['above_15_wt_percent'] is None
        return
    assert all(g['status']==status for g in grids)
    for row in [*grids,value]:
        x=row['solute_mole_fraction_solubility'];assert 0<=x<=1
        near(row['solute_wt_percent_solubility'],100*x*solute_mass/(x*solute_mass+(1-x)*solvent_mass))
        if status=='single_liquid_phase':assert x==1 and row['tie_lines']==[]
        else:
            assert row['tie_lines'] and x==row['tie_lines'][0]['x_solvent_rich']
            for tie in row['tie_lines']:
                assert 0<tie['x_solvent_rich']<tie['x_solute_rich']<1
                assert 0<=tie['chemical_potential_residual']<=1e-7
                assert math.isfinite(tie['minimum_tangent_distance_RT']) and tie['minimum_tangent_distance_RT']>=-1e-7
    dx=100*abs(grids[0]['solute_mole_fraction_solubility']-grids[1]['solute_mole_fraction_solubility'])
    dw=abs(grids[0]['solute_wt_percent_solubility']-grids[1]['solute_wt_percent_solubility'])
    assert max(dx,dw)<=.01
    near(value['grid_change_mol_percentage_points'],dx);near(value['grid_change_wt_percentage_points'],dw)
    for basis,number in [('mol',100*value['solute_mole_fraction_solubility']),('wt',value['solute_wt_percent_solubility'])]:
        expected=None if abs(number-15)<=.01 else number>15
        assert value['above_15_'+basis+'_percent'] is expected
