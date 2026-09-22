"""Original-campaign overview from independently hash-verified final aggregate CSV."""
import os
os.environ['MPLBACKEND']='Agg'
import csv,json,hashlib,collections,datetime
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,BoundaryNorm
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/reports/final-panel-2026-09-21');D.mkdir(parents=True,exist_ok=True)
proof=json.loads((R/'state/final-panel-csv-verification-20260921.json').read_text());source=Path(proof['file']);digest=hashlib.sha256()
with source.open('rb') as f:
 for b in iter(lambda:f.read(1024*1024),b''):digest.update(b)
assert digest.hexdigest()==proof['sha256']
accepted={p.stem for p in (R/'state/campaign-v1/records').glob('*.json') if json.loads(p.read_text())['status']=='converged'}
metadata=[m for m in json.loads((R/'state/campaign-v1/eligible.json').read_text()) if m['inchikey'] in accepted];assert len(metadata)==len(accepted)==5803
metadata.sort(key=lambda m:(m['atoms'],m['inchikey']));keys=[m['inchikey'] for m in metadata];index={k:i for i,k in enumerate(keys)}
lib=json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text());solvents=[s['solvent_key'] for s in lib['solvents'] if s['solvent_key']!='water'];assert len(solvents)==32
col={k:i for i,k in enumerate(solvents)};matrix=np.full((5803,32),np.nan);seen=set()
with source.open(newline='') as f:
 for r in csv.DictReader(f):
  key=r['input_inchikey']
  if key not in index:continue
  cell=(index[key],col[r['solvent']]);assert cell not in seen;seen.add(cell)
  if r['status']=='predicted':matrix[cell]=float(r['log10_K_mole_fraction'])
assert len(seen)==5803*32 and np.isfinite(matrix).sum()==179752
with (D/'overview-contaminant-rows.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['row','input_inchikey','name','smiles','cas','atoms_including_H']);w.writeheader()
 for i,m in enumerate(metadata):w.writerow(dict(row=i,input_inchikey=m['inchikey'],name=m['name'],smiles=m['smiles'],cas=m['cas'],atoms_including_H=m['atoms']))
with (D/'overview-matrix.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['input_inchikey',*solvents]);w.writerows([k,*['' if not np.isfinite(x) else float(x) for x in matrix[i]]] for i,k in enumerate(keys))
bins=[(0,20,'≤20'),(21,30,'21–30'),(31,40,'31–40'),(41,50,'41–50'),(51,60,'51–60'),(61,70,'61–70'),(71,80,'71–80'),(81,999,'>80')];counts=[sum(lo<=m['atoms']<=hi for m in metadata) for lo,hi,_ in bins];assert sum(counts)==5803
plt.rcParams.update({'font.size':12,'axes.titlesize':12,'axes.labelsize':12,'xtick.labelsize':12,'ytick.labelsize':12,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','figure.facecolor':'white','savefig.facecolor':'white'})
cmap=ListedColormap(['#2166ac','#67a9cf','#d1e5f0','#fddbc7','#b2182b']);cmap.set_bad('#e0e0e0');norm=BoundaryNorm([-100,0,2,5,10,100],cmap.N)
fig=plt.figure(figsize=(12,32));gs=fig.add_gridspec(3,1,height_ratios=[.55,22,.55],left=.25,right=.975,top=.965,bottom=.055,hspace=.33);dep=fig.add_subplot(gs[0]);ax=fig.add_subplot(gs[1]);legend=fig.add_subplot(gs[2])
depkey='FLKPEMZONWLCSK-UHFFFAOYSA-N';dep.imshow(matrix[index[depkey]][None,:],aspect='auto',cmap=cmap,norm=norm,interpolation='nearest');dep.set_yticks([]);dep.set_xticks(range(32),solvents,rotation=90);dep.set_title('Diethyl phthalate (DEP): 30 atoms including H',loc='left',pad=10)
ax.imshow(matrix,aspect='auto',cmap=cmap,norm=norm,interpolation='nearest',rasterized=True);ax.set_xticks(range(32),solvents,rotation=90);offset=0;ticks=[];labels=[]
for (_,_,label),count in zip(bins,counts):
 ticks.append(offset+(count-1)/2);labels.append(f'{label} atoms\n{count:,} molecules');offset+=count
 if offset<5803:ax.axhline(offset-.5,color='white',linewidth=1)
ax.set_yticks(ticks,labels);ax.tick_params(axis='y',length=0,pad=12);ax.set_title('5,803 accepted contaminants · each appears exactly once',loc='left',pad=12);ax.set_xlabel('Solvent / water at 298.15 K; mole-fraction partition coefficient')
legend.axis('off');labels=['<0','0–2','2–5','5–10','≥10','Unavailable']
for i,(color,label) in enumerate(zip([*cmap.colors,'#e0e0e0'],labels)):
 x=i/6;legend.add_patch(plt.Rectangle((x,.40),.04,.3,color=color,transform=legend.transAxes));legend.text(x+.047,.55,label,va='center',transform=legend.transAxes,color='black')
legend.text(0,-.12,'Colour: log₁₀ Kₓ. Grey: numerical failure or unresolved xylene identity.',transform=legend.transAxes,color='black')
fig.suptitle('Contaminant partitioning overview',x=.25,ha='left',y=.988,fontsize=12,color='black')
fig.canvas.draw();renderer=fig.canvas.get_renderer();assert ax.title.get_window_extent(renderer).width<=ax.get_window_extent(renderer).width
png=D/'contaminant-panel-overview.png';fig.savefig(png,dpi=300);fig.savefig(D/'contaminant-panel-overview.pdf');plt.close(fig)
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'accepted_rows':5803,'unique_keys':len(set(keys)),'requested_columns':32,'predicted_cells':int(np.isfinite(matrix).sum()),'missing_cells':int(np.isnan(matrix).sum()),'atom_bin_counts':dict(zip([b[2] for b in bins],counts)),'source_csv_sha256':digest.hexdigest(),'png_sha256':hashlib.sha256(png.read_bytes()).hexdigest(),'dpi':300,'text_size_pt':12,'basis':'mole_fraction','audit_status':'Aggregate CSV independently verified; final all-record numerical seal remains pending at figure creation'};(D/'overview-verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
