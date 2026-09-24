"""Refresh workbook comparisons from completed A-9 exact-zero results.

Reads recorded results only. The accepted 8.2 package remains unchanged; this
supplement discloses whether the x=0 change affects metrics or screening signs.
"""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['MPLBACKEND']='Agg'
import collections,csv,datetime,hashlib,json,math,shutil,statistics
from pathlib import Path
from analyze_phase9_gate import collected_records

R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase9-v1')
OLD=R/'reports/phase8-2-workbook-validation';OUT=R/'reports/phase9-workbook-validation';OUT.mkdir(exist_ok=True)


def write(name,rows):
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)


def boolean(v):
    assert v in ['True','False','',None],v
    return {'True':True,'False':False,'':None,None:None}[v]


def confusion(rows,truth,pred):
    scored=[r for r in rows if r[pred] is not None]
    cells=collections.Counter(('TP' if r[truth] else 'FP') if r[pred] else ('FN' if r[truth] else 'TN') for r in scored)
    return dict(denominator=len(rows),scored=len(scored),unscored=len(rows)-len(scored),**{k:cells[k] for k in ['TP','TN','FP','FN']})


def main():
    gate=json.loads((D/'gate-comparison.json').read_text());assert gate['lle_compared']==2240 and not gate['errors']
    plan=json.loads((D/'gate-plan.json').read_text());names={u['id']:u['reference_name'] for c in plan['chunks'] for u in c if u['reference']=='phase82'}
    assert len(names)==8
    parts={};lle={}
    for path,value in collected_records('gate-results-v1',chunk_id='0003'):
        if '/partition/' in path:
            for r in value:
                if r['polymer']=='pvc':parts[names[r['unit']],r['solvent'],r['convention']]=r
        elif '/lle/' in path:lle[names[value['unit']],value['solvent'],value['regime']]=value
    assert len(parts)==512 and len(lle)==512
    parity=[];sign_changes=[]
    for old in csv.DictReader((OLD/'logP-workbook-parity.csv').open()):
        r=parts[old['solute'],old['solvent'],old['convention']]
        assert r['solute_surface_sha256']==old['solute_surface_sha256'] and r['solvent_surface_sha256']==old['solvent_surface_sha256']
        row=dict(old,phase82_predicted=float(old['predicted']),phase82_predicted_pass=boolean(old['predicted_pass']),value=float(old['value']),
                 reference_pass=boolean(old['reference_pass']),predicted=r['logP_concentration'],logP_x=r['logP_x'],
                 predicted_pass=r['logP_concentration']>0,residual=r['logP_concentration']-float(old['value']),
                 delta_from_phase82=r['logP_concentration']-float(old['predicted']),solute_x=0.,reference_state='pure_component')
        parity.append(row)
        if row['predicted_pass']!=row['phase82_predicted_pass']:sign_changes.append(row)
    stats={};cms=[]
    for convention in ['normalized','existing']:
        rows=[r for r in parity if r['convention']==convention];assert len(rows)==256
        x=[r['value'] for r in rows];y=[r['predicted'] for r in rows];mx=statistics.mean(x);my=statistics.mean(y)
        slope=math.fsum((a-mx)*(b-my) for a,b in zip(x,y))/math.fsum((a-mx)**2 for a in x)
        residuals=[b-a for a,b in zip(x,y)]
        stats[convention]=dict(n=len(rows),MAE=statistics.mean(abs(d) for d in residuals),RMSE=math.sqrt(statistics.mean(d*d for d in residuals)),bias=statistics.mean(residuals),slope=slope,intercept=my-slope*mx)
        cms.append(dict(quantity='logP_above_zero',convention=convention,**confusion(rows,'reference_pass','predicted_pass')))
    write('logP-workbook-parity-x0.csv',parity)
    write('logP-worst-cases-x0.csv',sorted(parity,key=lambda r:abs(r['residual']),reverse=True)[:40])
    if sign_changes:write('sign-changes-from-phase82.csv',sign_changes)
    else:(OUT/'sign-changes-from-phase82.csv').write_text('solute,solvent,convention,phase82_predicted,predicted\n')
    verdicts=[];lle_changes=[]
    for old in csv.DictReader((OLD/'miscibility-both-layouts-both-bases.csv').open()):
        r=lle[old['solute'],old['solvent'],old['regime']]
        row=dict(old,reference_pass=boolean(old['reference_pass']),calculation_status=r['status'],
                 predicted_15_mol_percent=r.get('above_15_mol_percent'),predicted_15_wt_percent=r.get('above_15_wt_percent'),
                 solute_mole_fraction_solubility=r.get('solute_mole_fraction_solubility'),solute_wt_percent_solubility=r.get('solute_wt_percent_solubility'))
        verdicts.append(row)
        if r['status']!=old['calculation_status'] or any(row[k]!=boolean(old[k]) for k in ['predicted_15_mol_percent','predicted_15_wt_percent']):lle_changes.append(row)
    assert len(verdicts)==1024 and not lle_changes
    for layout in ['blocked','paired']:
        for basis in ['mol','wt']:
            for regime in ['all','RT','high']:
                rows=[r for r in verdicts if r['layout']==layout and (regime=='all' or r['regime']==regime)]
                cms.append(dict(quantity='miscibility_above_15_percent',layout=layout,basis=basis,regime=regime,**confusion(rows,'reference_pass','predicted_15_'+basis+'_percent')))
    write('miscibility-both-layouts-both-bases.csv',verdicts);write('verdict-confusion-matrices.csv',cms)
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),reference_type='computed commercial COSMOtherm workbook; not experimental',
                 logP_metrics=stats,confusion_matrices=cms,partition_sign_changes_from_phase82=len(sign_changes),LLE_status_or_verdict_changes_from_phase82=len(lle_changes),
                 maximum_change_from_phase82=max(abs(r['delta_from_phase82']) for r in parity),gate_plan_sha256=hashlib.sha256((D/'gate-plan.json').read_bytes()).hexdigest())
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    text='# Exact-zero A-9 workbook validation supplement\n\nThis compares the new x=0 results with **computed commercial COSMOtherm workbook values, not measured data**. It uses the same eight phthalates, 32 solvents, PVC ensemble and surface pins as 8.2. Both conventions now use exact infinite dilution; no empirical correction is applied. The original 8.2 report and figures remain unchanged and are historical reference results.\n\n'
    text+='| Convention | n | MAE | RMSE | Bias | Slope | Intercept | TP | TN | FP | FN |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n'
    for c,m in stats.items():
        cm=next(x for x in cms if x.get('convention')==c)
        text+='| '+c+' | 256 | '+' | '.join(f'{m[k]:.6f}' for k in ['MAE','RMSE','bias','slope','intercept'])+' | '+' | '.join(str(cm[k]) for k in ['TP','TN','FP','FN'])+' |\n'
    text+=f"\nStrict logP > 0 is the screening verdict. Changing from 8.2 to exact-zero activities changes **{len(sign_changes)}/512** partition signs. Maximum numerical change is {summary['maximum_change_from_phase82']:.9f} log units. All **512/512 LLE statuses and both 15% verdicts are unchanged**, so the two workbook-layout and two threshold-basis confusion matrices remain applicable. `verdict-confusion-matrices.csv` recalculates and records them, without selecting a layout or basis.\n\n"
    for c in stats:
        text+=c.title()+' largest residuals:\n\n'
        for r in sorted([r for r in parity if r['convention']==c],key=lambda r:abs(r['residual']),reverse=True)[:5]:
            text+=f"- {r['solute']} / {r['solvent']}: {r['residual']:+.6f}, workbook cell {r['cell']}.\n"
        text+='\n'
    text+='The DEHP/water discrepancy is retained, not calibrated away. DiNP and DiDP remain pinned representative structures. The routes differ in engine, parameterization and reoptimized geometry simultaneously; this does not establish a parameterization-only cause or experimental accuracy. The workbook provides only Yes/No miscibility observations, so numerical solubility MAE/RMSE against that workbook are not defined. Generic xylene identity, didecyl-phthalate experimental provenance, workbook layout and threshold basis remain owner questions.\n\n'
    text+='Source: `../phase8-2-workbook-validation/` and the frozen `phase8-v1/validation-inputs.json`. Calculation outputs: `/mnt/r/plastchem-euler/phase9-v1/`, validated Milan chunk 68346_3. Reproduce with `/home/aaltamimi2/plastchem-euler/scripts/refresh_phase9_workbook_validation.py`; this script launches no calculations.\n'
    (OUT/'REPORT.md').write_text(text)
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':12,'axes.titlesize':12,'axes.labelsize':12,'xtick.labelsize':12,'ytick.labelsize':12,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
    fig,axes=plt.subplots(1,2,figsize=(9,4.6));fig.subplots_adjust(left=.09,right=.98,bottom=.25,top=.9,wspace=.3)
    for i,(ax,c) in enumerate(zip(axes,['normalized','existing'])):
        rows=[r for r in parity if r['convention']==c];x=[r['value'] for r in rows];y=[r['predicted'] for r in rows]
        lo=min(x+y)-.2;hi=max(x+y)+.2
        ax.scatter(x,y,s=15,color='#477c9e',alpha=.6,edgecolors='none');ax.plot([lo,hi],[lo,hi],'k--',linewidth=1)
        ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Workbook logP',title=c.title()+', x = 0')
        if i==0:ax.set_ylabel('openCOSMO-RS 24a logP')
        m=stats[c];fig.text(.09+i*.5,.075,f"n = 256; MAE = {m['MAE']:.3f}\nRMSE = {m['RMSE']:.3f}; bias = {m['bias']:+.3f}",fontsize=12,color='black')
    fig.savefig(OUT/'workbook-logP-parity-x0.png',dpi=300);plt.close(fig)
    (OUT/'FIGURE_CAPTIONS.md').write_text('Exact-zero solvent/PVC concentration partition coefficients at 298.15 K against computed commercial COSMOtherm workbook values, 256 pairs per convention. Dashed lines are 1:1; n and uncorrected error metrics are printed below each panel. These are computational-reference comparisons, not experimental accuracy. Same 12-point black text throughout; PNG exported at 300 dpi. CSV: `logP-workbook-parity-x0.csv`.\n')
    pins={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in OUT.iterdir() if p.is_file() and p.name!='manifest.json'}
    (OUT/'manifest.json').write_text(json.dumps(dict(files=pins,utc=summary['utc']),indent=2)+'\n')
    dest=D/'workbook-validation-x0';dest.mkdir(exist_ok=True)
    for p in OUT.iterdir():
        if p.is_file():shutil.copyfile(p,dest/p.name)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
