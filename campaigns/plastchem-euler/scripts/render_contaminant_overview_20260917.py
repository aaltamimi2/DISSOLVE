"""Render the fixed overview snapshot; no scientific calculation or imputed cells."""
import csv,json,math,hashlib,shutil
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,BoundaryNorm
from matplotlib.patches import Patch
from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D
from PIL import Image
import io
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/progress-2026-09-17/contaminant-overview-draft-v1');s=json.loads((D/'summary.json').read_text());rows=list(csv.DictReader((D/'contaminant-rows.csv').open()));cols=list(csv.DictReader((D/'solvent-columns.csv').open()));pred=list(csv.DictReader((D/'predictions.csv').open()));index={r['input_inchikey']:i for i,r in enumerate(rows)};colidx={r['solvent']:i for i,r in enumerate(cols)}
matrix=np.full((len(rows),len(cols)),np.nan)
for p in pred:matrix[index[p['input_inchikey']],colidx[p['solvent']]]=float(p['log10_K_mole_fraction'])
assert matrix.shape==(5803,70) and np.isfinite(matrix).sum()==len(pred)
np.savez_compressed(D/'heatmap-matrix.npz',values=matrix,input_inchikey=np.array(list(index)),solvents=np.array(list(colidx)))
plt.rcParams.update({'font.size':12,'axes.titlesize':12,'axes.labelsize':12,'xtick.labelsize':12,'ytick.labelsize':12,'legend.fontsize':12,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','font.family':'DejaVu Sans'})
colors=['#edf2f0','#c5ded6','#82bdb0','#389688','#146257'];cmap=ListedColormap(colors);cmap.set_bad('#dddddd');norm=BoundaryNorm([-np.inf,0,2,5,10,np.inf],5)
H=5.145+(5803+2*8)/300+8*.45+1.9
fig=plt.figure(figsize=(7,H),facecolor='white');left=.12;right=.95;width=right-left
def topcoord(y):return 1-(1-y)*17.5/H
texts=[]
def text(y,t,**kw):
 a=fig.text(left,(topcoord(y) if y>.1 else y*17.5/H),t,ha='left',va='top',fontsize=12,color='black',**kw);texts.append(a);return a
text(.988,f"{s['displayed_predictions']:,} computed partition predictions",fontweight='bold')
text(.969,'5,803 ORCA-accepted contaminants · draft snapshot')
text(.950,'25°C only · liquid-reference log₁₀Kₓ against water')
text(.931,'Columns: 69 common solvents + acetic acid')
leg=fig.legend(handles=[Patch(facecolor=c,label=l) for c,l in zip(colors,['<0','0–2','2–5','5–10','≥10'])]+[Patch(facecolor='#dddddd',label='Unavailable')],loc='upper left',bbox_to_anchor=(left-.012,topcoord(.915)),ncol=3,frameon=False,handlelength=1,columnspacing=1.25)
text(.875,'Worked example: DEP in ethanol/water',fontweight='bold')
# Black-only molecular sketch from the accepted input SMILES.
dep=next(r for r in rows if r['input_inchikey']=='FLKPEMZONWLCSK-UHFFFAOYSA-N');drawer=rdMolDraw2D.MolDraw2DCairo(650,330);drawer.drawOptions().useBWAtomPalette();drawer.drawOptions().fixedFontSize=48;drawer.DrawMolecule(Chem.MolFromSmiles(dep['smiles']));drawer.FinishDrawing();im=Image.open(io.BytesIO(drawer.GetDrawingText()));im.save(D/'dep-structure.png')
axmol=fig.add_axes([left,topcoord(.778),.32,.085*17.5/H]);axmol.imshow(im);axmol.axis('off')
example_source=D/'worked-example.csv' if (D/'worked-example.csv').exists() else Path('/mnt/r/plastchem-euler/common69-pilot-20260917/composition-grid.csv')
example=list(csv.DictReader(example_source.open()));example=[r for r in example if r['solvent']=='ethanol'];example.sort(key=lambda r:float(r['organic_cosolvent_fraction']));assert len(example)==11 and all(r['status']=='converged' for r in example)
ax=fig.add_axes([.56,topcoord(.784),.39,.071*17.5/H]);x=[100*float(r['organic_cosolvent_fraction']) for r in example];y=[float(r['log_activity_ratio_vs_pure_water']) for r in example];ax.plot(x,y,'o-',color='#217b70',markersize=3,linewidth=1.2);ax.set_xticks([0,50,100]);ax.set_xlabel('Ethanol (mol%)');ax.set_ylabel('log₁₀ ratio');ax.spines[['top','right']].set_visible(False)
text(.754,'11 compositions · prescribed liquid branch')
text(.736,'Dilute DEP activity ratio; no miscibility claim')
labels=['≤20 atoms','21–30 atoms','31–40 atoms','41–50 atoms','51–60 atoms','61–70 atoms','71–80 atoms','>80 atoms'];counts=[];cursor=5.145;row_axes=[]
for b,label in enumerate(labels):
 ids=[i for i,r in enumerate(rows) if int(r['size_bin'])==b];a=matrix[ids];n=len(ids);counts.append({'size_bin':b,'label':label,'contaminants':n,'predictions':int(np.isfinite(a).sum())});top=1-cursor/H
 labeltext=fig.text(left,top,f'{label} · {n:,} contaminants',ha='left',va='top',fontsize=12,color='black',fontweight='bold');texts.append(labeltext)
 heatheight=(n+2)/300;heatbottom=1-(cursor+.25+heatheight)/H
 ax=fig.add_axes([left,heatbottom,width,heatheight/H]);ax.imshow(np.ma.masked_invalid(a),aspect='auto',interpolation='nearest',cmap=cmap,norm=norm,extent=[.5,70.5,n+.5,.5]);ax.set_yticks([]);ax.set_xticks([1,23,46,69,70] if b==7 else []);ax.set_xlim(.5,70.5);ax.spines[:].set_visible(False);row_axes.append((ax,n));cursor+=heatheight+.45
 if b==7:
  ax.set_xticks([1,23,46,70]);ax.set_xlabel('Solvent index (column identities in CSV)')
assert sum(r['contaminants'] for r in counts)==5803
text(.066,'Each contaminant appears once; rows ordered by atom count.')
text(.048,'Atoms include H. Gray = no accepted prediction at capture.')
text(.030,'Calculated snapshot; engine integration is a separate step.')
text(.012,'21 campaign failures; nine isotope exclusions upstream.')
fig.canvas.draw();renderer=fig.canvas.get_renderer();overflow=[]
for t in texts:
 box=t.get_window_extent(renderer)
 if box.x1>fig.bbox.width*right+1:overflow.append(t.get_text())
assert not overflow,overflow
assert texts[-4].get_window_extent(renderer).y1 < row_axes[-1][0].xaxis.label.get_window_extent(renderer).y0-3,'Footer overlaps solvent-axis label'
assert all(ax.get_window_extent(renderer).height*300/fig.dpi>=n for ax,n in row_axes),'Each contaminant needs at least one output pixel row'
fig.savefig(D/'contaminant-data-overview-draft.png',dpi=300);fig.savefig(D/'contaminant-data-overview-draft.pdf');plt.close(fig)
with (D/'size-bin-summary.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(counts[0]));w.writeheader();w.writerows(counts)
shutil.copyfile(D/'contaminant-data-overview-draft.png',R/'contaminant-data-overview-draft-2026-09-17.png')
print(json.dumps({'matrix_shape':list(matrix.shape),'finite_cells':int(np.isfinite(matrix).sum()),'rows':len(index),'bins':counts,'text_overflow':overflow,'height_inches':H,'minimum_one_pixel_per_contaminant_at_300dpi':True,'output':str(D/'contaminant-data-overview-draft.png')}),flush=True)
