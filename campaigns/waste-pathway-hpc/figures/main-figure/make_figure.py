#!/usr/bin/env python3
"""Portable campaign-table renderer. No previous drawings are read."""
# ================= USER-EDITABLE SETTINGS =================
POLYMER = 'LDPE'
SOLVENT = 'Toluene'
ENERGY_CASE = 'C1'
SOBOL_ENERGY_CASE = 'C1'  # Sobol table has no energy-case column.
FEED = 'published'
LOCATION = 'A'
BASE_PLANT = 'dedicated'
PLANTS = [('dedicated', '8,000 t/year plant'), ('shared20k', 'Shared 20,000 t/year plant')]
FEED_DESCRIPTION = '60% LDPE, 20% PET, 10% nylon 6, 10% EVOH'
EXCLUDE_RESALE = True
SIMPLE_WASHES = (0, 1)
MULTIPLE_WASHES = (2, 3)
PERCENTILES = (.05, .95)
FIGURE_WIDTH_IN = 6.3
BODY_FONT_PT = 7.65       # At FIGURE_WIDTH_IN; no text rasterization.
HEADING_FONT_PT = 7.92
EMISSIONS_LIMIT_KT = 22  # Deliberate crop; omitted rows retained in CSV/report.
PARETO_Y_LIMITS = (-5.2, 4.4)  # Expanded automatically for updated visible values.
DELTA_X_LIMITS = (-800, 500)   # Expanded automatically for updated values.
HEATMAP_LIMITS = (.5, 30)     # Log limits; expand for new positive extrema.
HEATMAP_TICKS = [.5, 1, 2, 5, 10, 30]
TOP_SOBOL_FACTORS = 4
JITTER_SEED = 41012
# Tuned label offsets FROM the corresponding route point (kt CO2e, million USD).
# New front members receive automatic offset-point labels. Long labels may need adjustment.
ROUTE_LABELS = {
 'no wash + lf': ('No wash, landfill', (2.508349136345275, 1.57191992), (0, .5)),
 'EVOH/Ethylene glycol + lf': ('EVOH wash, landfill', (2.034902180892228, .8286121655260945), (0, .5)),
 'EVOH/Ethylene glycol + we': ('EVOH wash, incineration', (-8.369837863870682, .9326853038878418), (.5, .5)),
}
MAX_FRONT_LABELS = 6
QUERY = ('My film waste is 60% LDPE, 20% PET, 10% nylon 6 and 10% EVOH, at 8,000 t/year. Compare up to three solvent washes and residual destinations for profit, emissions and circularity, using the published default distances. Compare a dedicated plant with a shared 20,000 t/year plant, and show cost sensitivity for LDPE in toluene.')
DAG_LABELS = [['Solubility', 'screen'], ['BioSTEAM', 'TEA/LCA'], ['Enumerate routes', '0–3 washes', '+ destination'], ['Exact Pareto', 'fronts']]
TOOL_LABEL = 'optimize_waste_pathway'
SENSITIVITY_TOOL_LABEL = 'tea_tornado'
SENSITIVITY_DESCRIPTION = 'One-at-a-time wash sensitivity'
OUTPUT_LABELS = ['Trade-offs', '+ sensitivity']
FORMULA = 'ΔP = Pₘ − Pₛ'
FACTOR_LABELS = {'processing_capacity_mt_per_yr':'Plant capacity','target_mass_percent':'LDPE share','irr':'Rate of return','dissolution_capacity':'Polymer loading','feedstock_distance_km':'Feedstock distance','centrifuged_plastic_solvent_content_pct':'Solvent in wet plastic'}
BASE_RECOVERY_PERCENT = 99.9999  # Assumption, not inferred from Parquet. Review after model updates.
RECOVERY_RANGE_PERCENT = (99.99, 99.9999)
# ================= END SETTINGS =================
import os, sys, argparse, json, hashlib, copy, re, textwrap
sys.dont_write_bytecode = True
from pathlib import Path
A = argparse.ArgumentParser(description=__doc__)
A.add_argument('--data', type=Path, required=True)
A.add_argument('--out', type=Path, required=True)
args = A.parse_args()
ROOT = args.out.resolve(); ROOT.mkdir(parents=True, exist_ok=True)
PACKAGE = Path(__file__).resolve().parent
ASSETS = PACKAGE/'assets'
DATA = args.data.resolve()
# Force library caches and temporary files inside --out; never modify data/package.
for name in ['mpl', 'fontconfig', 'tmp']: (ROOT/'.cache'/name).mkdir(parents=True,exist_ok=True)
os.environ['MPLCONFIGDIR'] = str(ROOT/'.cache/mpl')
os.environ['XDG_CACHE_HOME'] = str(ROOT/'.cache')
os.environ['TMPDIR'] = str(ROOT/'.cache/tmp')
from xml.sax.saxutils import escape
fc = ROOT/'.cache/fonts.conf'
fc.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>'+escape(str(ASSETS/'fonts'))+'</dir><cachedir>'+escape(str(ROOT/'.cache/fontconfig'))+'</cachedir></fontconfig>')
os.environ['FONTCONFIG_FILE'] = str(fc)
import numpy as np
import pandas as pd
import duckdb
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, LogNorm
from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator, MaxNLocator
from matplotlib.text import Text
from lxml import etree as ET
import cairosvg, fitz
from PIL import Image
R=ROOT; W=1008; H=984; SCALE=FIGURE_WIDTH_IN/14
FS=round(BODY_FONT_PT/SCALE,12); HEAD=round(HEADING_FONT_PT/SCALE,12)
FONT=fitz.Font(fontfile=str(ASSETS/'fonts/DejaVuSans.ttf'))
NS='http://www.w3.org/2000/svg'
GREEN='#19715F'; SLATE='#526B91'; SAGE='#8FBCAE'; PALE='#E7F3F0'; GRAY='#C8CECB'
CMAP=LinearSegmentedColormap.from_list('dissolve',['#F1F7F3','#B8D8CB','#629B87','#123C36'])
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':FS,'axes.labelsize':FS,'xtick.labelsize':FS,'ytick.labelsize':FS,'legend.fontsize':FS,'svg.fonttype':'none','pdf.fonttype':42,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#82968C','axes.linewidth':.8,'svg.hashsalt':'hpc-main-v1'})
TABLES={}
for name in ['washes_surface','sobol_indices','mc_candidates','mc_scenarios']:
 p=DATA/(name+'.parquet')
 if not p.exists():p=DATA/name
 files=sorted(p.rglob('*.parquet')) if p.is_dir() else [p]
 if not files or not all(f.is_file() for f in files):raise FileNotFoundError(f'Missing table {name}: expected file or directory of Parquet parts')
 TABLES[name]=files
CON=duckdb.connect()
CON.execute('SET threads=2')
CON.execute('SET temp_directory = ?', [str(ROOT/'.cache/tmp')])
for name,files in TABLES.items():
 CON.read_parquet([str(p) for p in files], union_by_name=True).create_view(name)
EXCLUDED = "AND c.downstream_key <> 'resale'" if EXCLUDE_RESALE else ''
frames={}
def extract(stem,query):
 (ROOT/(stem+'.sql')).write_text(query+';\n')
 d=CON.execute(query).df()
 if d.empty:raise ValueError(f'No rows for {stem}: check filters/settings')
 d.to_csv(ROOT/(stem+'.data.csv'),index=False,float_format='%.17g')
 frames[stem]=d
 return d

def literal(value):return "'"+str(value).replace("'","''")+"'"
def data(stem):return frames[stem].copy()
def frontier(d):
 v=d.sort_values(['emissions_t_co2e_per_yr','profit_usd_per_yr'],ascending=[True,False]);best=None;ix=[]
 for i,row in v.iterrows():
  if best is None or row.profit_usd_per_yr>best+1e-9*max(1,abs(best)):ix.append(i);best=row.profit_usd_per_yr
 return d.loc[ix].sort_values('emissions_t_co2e_per_yr')

def load_data():
 extract('wash-cost-surface',f"SELECT id,ok,cfg_target_mass_percent AS share,cfg_processing_capacity_mt_per_yr AS capacity,msp_usd_per_kg AS value,lca_status,error FROM washes_surface WHERE cfg_target_polymer={literal(POLYMER)} AND cfg_solvent={literal(SOLVENT)} AND cfg_energy_case={literal(ENERGY_CASE)} ORDER BY capacity,share")
 if ENERGY_CASE!=SOBOL_ENERGY_CASE:raise ValueError('Sobol table has no energy-case column. Confirm SOBOL_ENERGY_CASE before drawing a different case.')
 for stem,metric in [('sobol-msp','msp_usd_per_kg'),('sobol-gwp','gwp_kg_co2e_per_kg')]:
  extract(stem,f"SELECT * FROM sobol_indices WHERE polymer={literal(POLYMER)} AND solvent={literal(SOLVENT)} AND metric={literal(metric)} ORDER BY ST DESC,factor")
 d=extract('pathway-pareto',f"SELECT c.* FROM mc_candidates c JOIN mc_scenarios s USING(scenario_id,location) WHERE s.feed={literal(FEED)} AND s.plant={literal(BASE_PLANT)} AND s.draw IS NULL AND s.location={literal(LOCATION)} AND s.energy_case={literal(ENERGY_CASE)} {EXCLUDED} ORDER BY c.label")
 fr=frontier(d);d=d.drop(columns=[c for c in d if c.startswith('on_')]);d['on_restricted_emissions_vs_profit']=d.label.isin(fr.label)
 d.to_csv(ROOT/'pathway-pareto.data.csv',index=False,float_format='%.17g');frames['pathway-pareto']=d
 lo,hi=SIMPLE_WASHES;ml,mh=MULTIPLE_WASHES
 q=f"""WITH best AS (
 SELECT s.scenario_id, s.location, s.plant, s.draw, s.tonnes_per_yr,
 count(*) FILTER (WHERE c.n_washes BETWEEN {lo} AND {hi}) AS simple_candidates,
 count(*) FILTER (WHERE c.n_washes BETWEEN {ml} AND {mh}) AS extra_candidates,
 max(c.profit_usd_per_yr) FILTER (WHERE c.n_washes BETWEEN {lo} AND {hi}) AS simple_profit_usd_per_yr,
 max(c.profit_usd_per_yr) FILTER (WHERE c.n_washes BETWEEN {ml} AND {mh}) AS extra_profit_usd_per_yr
 FROM mc_candidates c JOIN mc_scenarios s USING(scenario_id,location)
 WHERE s.feed={literal(FEED)} AND s.location={literal(LOCATION)} AND s.energy_case={literal(ENERGY_CASE)} AND s.draw IS NOT NULL
 AND s.plant IN ({','.join(literal(p) for p,_ in PLANTS)}) {EXCLUDED}
 GROUP BY s.scenario_id,s.location,s.plant,s.draw,s.tonnes_per_yr)
 SELECT *,extra_profit_usd_per_yr-simple_profit_usd_per_yr AS advantage_usd_per_yr,
 (extra_profit_usd_per_yr-simple_profit_usd_per_yr)/tonnes_per_yr AS advantage_usd_per_t_feed
 FROM best ORDER BY plant,draw"""
 d=extract('profit-advantage',q)
 if d[['simple_profit_usd_per_yr','extra_profit_usd_per_yr']].isna().any().any():raise ValueError('A sampled scenario lacks one wash-count group; cannot form a paired profit difference.')
 if not d.tonnes_per_yr.gt(0).all():raise ValueError('Feed throughput must be positive.')
 # Partition order and the supplied draw IDs are stable; no hard-coded draw count.

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def jd(p,d):Path(p).write_text(json.dumps(d,indent=2,ensure_ascii=False,default=str)+'\n')
def table(n):return "'"+str(DATA/(n+'.parquet'))+"'"
def saveplot(stem,f,caption):
 for axis in f.axes:
  for collection in axis.collections:collection.set_rasterized(False)
 f.canvas.draw()
 texts=[t for t in f.findobj(Text) if t.get_visible() and t.get_text()]
 assert all(t.get_fontsize()>=min(FS,HEAD)-.001 and matplotlib.colors.to_hex(t.get_color())=='#000000' for t in texts)
 for ext in ['svg','pdf','png']:f.savefig(ROOT/(stem+'.'+ext),dpi=220,facecolor='white')
 (ROOT/(stem+'.caption.txt')).write_text(caption+'\n');plt.close(f)
 print('saved',stem,flush=True)

def root(w,h):
 r=ET.Element('{%s}svg'%NS,nsmap={None:NS},width=f'{w}pt',height=f'{h}pt',viewBox=f'0 0 {w} {h}')
 ET.SubElement(r,'{%s}rect'%NS,width=str(w),height=str(h),fill='white')
 return r
def el(parent,tag,**attrs):return ET.SubElement(parent,'{%s}%s'%(NS,tag),{k.replace('_','-'):str(v) for k,v in attrs.items()})
def txt(parent,x,y,s,size=12,bold=False,anchor='start'):
 n=el(parent,'text',x=x,y=y,font_family='DejaVu Sans',font_size=size,fill='#000000',text_anchor=anchor,font_weight='bold' if bold else 'normal');n.text=s;return n
def box(parent,x,y,w,h,fill=PALE,stroke='#8FBCAE',r=6):return el(parent,'rect',x=x,y=y,width=w,height=h,rx=r,fill=fill,stroke=stroke,stroke_width=1)
def path(parent,d,color=GREEN,width=1.7):return el(parent,'path',d=d,fill='none',stroke=color,stroke_width=width,stroke_linecap='round',stroke_linejoin='round')
def svgexport(stem,r,caption=None):
 b=ET.tostring(r,xml_declaration=True,encoding='UTF-8');(ROOT/(stem+'.svg')).write_bytes(b)
 cairosvg.svg2pdf(bytestring=b,write_to=str(ROOT/(stem+'.pdf')))
 cairosvg.svg2png(bytestring=b,write_to=str(ROOT/(stem+'.png')),scale=220/96)
 # SVG pt units are converted to 96 px/in by Cairo; scale yields 220 dpi.
 with Image.open(ROOT/(stem+'.png')) as im:im.save(ROOT/(stem+'.png'),dpi=(220,220))
 if caption:(ROOT/(stem+'.caption.txt')).write_text(caption+'\n')
 print('saved',stem,flush=True)
def embed(parent,p,x,y,prefix):
 r=ET.parse(str(p)).getroot();vb=list(map(float,r.get('viewBox').split()));r.set('x',str(x));r.set('y',str(y));r.set('width',str(vb[2]));r.set('height',str(vb[3]))
 # Ensure all IDs and their references remain unique in the composed document.
 ids={n.get('id'):prefix+'-'+n.get('id') for n in r.iter() if n.get('id')}
 for n in r.iter():
  for k,v in list(n.attrib.items()):
   if k=='id':n.set(k,ids[v])
   else:
    for old,new in ids.items():v=v.replace('url(#'+old+')','url(#'+new+')');v=('#'+new if v=='#'+old else v)
    n.set(k,v)
 parent.append(r)
def text(r,x,y,s,bold=False,anchor='start'):return txt(r,x,y,s,HEAD if bold else FS,bold,anchor)
def wrap(s,width):
 words=s.split();lines=[];line=''
 for word in words:
  trial=(line+' '+word).strip()
  if FONT.text_length(trial,fontsize=FS)>width and line:lines.append(line);line=word
  else:line=trial
 lines.append(line);return lines

def export_svg(stem,r,caption=None,word=False):
 if word:
  vb=list(map(float,r.get('viewBox').split()));r.set('width',f'{FIGURE_WIDTH_IN}in');r.set('height',f'{FIGURE_WIDTH_IN*vb[3]/vb[2]}in')
 svgexport(stem,r,caption)
 if word:
  cairosvg.svg2png(bytestring=ET.tostring(r),write_to=str(R/(stem+'.png')),output_width=round(FIGURE_WIDTH_IN*300))
  with Image.open(R/(stem+'.png')) as im:im.save(R/(stem+'.png'),dpi=(300,300))

def plot(stem,f,caption):saveplot(stem,f,caption)
def schematic():
 r=root(W,324);embed(r,ASSETS/'icon-tea-lca-plant.svg',8,4,'robot');r[-1].set('width','110');r[-1].set('height','130.625')
 lines=wrap(QUERY,818);bottom=53+(len(lines)-1)*22+8
 box(r,144,7,852,bottom-7,'#EDF2FA','#8495B1');text(r,160,30,'Example query',True)
 for i,s in enumerate(lines):text(r,160,53+i*22,s)
 assert bottom<211,bottom
 # Clear junction above the container; enter its top at the midpoint, not the rounded corner.
 junction=bottom+15;top=149
 path(r,f'M500 {bottom} L500 {junction} L500 {top}')
 path(r,f'M500 {junction} L40 {junction} Q24 {junction} 24 {junction+16} L24 285 Q24 298 43 298')
 box(r,38,top,761,101,'#F4F8F5','#A6C5B7');text(r,51,171,TOOL_LABEL,True)
 nodes=[(51,190,158,DAG_LABELS[0]),(230,190,165,DAG_LABELS[1]),(416,185,166,DAG_LABELS[2]),(603,190,181,DAG_LABELS[3])]
 for x,y,w,ls in nodes:
  height=58 if len(ls)==2 else 64;box(r,x,y,w,height,'#DFEDE5','#6D9A85',4)
  for i,s in enumerate(ls):text(r,x+w/2,y+20+20*i,s,anchor='middle')
 for a,z in [(209,230),(395,416),(582,603)]:path(r,f'M{a} 220 L{z} 220')
 box(r,43,260,365,64,'#E7EBF3','#8495B1');text(r,57,284,SENSITIVITY_TOOL_LABEL,True);text(r,57,308,SENSITIVITY_DESCRIPTION)
 box(r,844,213,152,63,'#CEE4D8','#397E68');text(r,920,239,OUTPUT_LABELS[0],True,'middle');text(r,920,262,OUTPUT_LABELS[1],True,'middle')
 path(r,'M799 220 C818 220 816 244 834 244 L844 244');path(r,'M408 298 L798 298 C831 298 816 244 834 244')
 export_svg('schematic',r,captions['full'])
 return {'query_box_bottom':bottom,'junction_y':junction,'optimization_container_top':top,'connector_entry_x':500}
def surface():
 d=data('wash-cost-surface');g=d.pivot(index='capacity',columns='share',values='value')
 f=plt.figure(figsize=(5.3,3.6));a=f.add_axes([.20,.22,.53,.70]);ca=f.add_axes([.77,.22,.032,.70])
 mesh=a.pcolormesh(np.arange(len(g.columns)+1)-.5,np.arange(len(g.index)+1)-.5,g.to_numpy(),cmap=CMAP,norm=LogNorm(min(HEATMAP_LIMITS[0],g.min().min()),max(HEATMAP_LIMITS[1],g.max().max())),shading='flat',edgecolors='face',linewidth=.1)
 ix=list(range(1,len(g.columns),4)) if len(g.columns)==19 else np.unique(np.linspace(0,len(g.columns)-1,min(5,len(g.columns)),dtype=int));a.set_xticks(ix,[f'{g.columns[i]:g}' for i in ix]);a.set_yticks(range(len(g.index)),[f'{x/1000:g}' for x in g.index]);a.set_xlabel(f'{POLYMER} in feed (wt%)');a.set_ylabel('Capacity (1,000 t/year)');a.tick_params(length=2)
 cb=f.colorbar(mesh,cax=ca);cb.locator=FixedLocator(HEATMAP_TICKS);cb.formatter=FixedFormatter([f'{x:g}' for x in HEATMAP_TICKS]);cb.update_ticks();cb.ax.yaxis.set_minor_locator(NullLocator());cb.set_label('MSP (USD/kg; log scale)',labelpad=5);cb.solids.set_edgecolor('face');cb.solids.set_linewidth(.25)
 plot('wash-cost-surface',f,captions['full'])

def sobol(stem,color):
 d=data(stem).head(TOP_SOBOL_FACTORS);labels=FACTOR_LABELS
 f=plt.figure(figsize=(4.35,3.6));a=f.add_axes([.075,.22,.86,.70]);ys=np.arange(len(d))*1.6
 a.barh(ys,d.ST,height=.40,color=color);a.hlines(ys,d.ST_low,d.ST_high,color='black',lw=1);a.plot(d.ST_low,ys,'|',color='black',ms=5);a.plot(d.ST_high,ys,'|',color='black',ms=5)
 for y,r in zip(ys,d.itertuples()):a.text(0,y-.77,labels.get(r.factor,r.factor.replace("_"," ")),va='center',fontsize=FS);a.text(max(r.ST,r.ST_high)+.03,y,f'{r.ST:.3f}',va='center',fontsize=FS)
 a.set_ylim((len(d)-1)*1.6+.5,-1.2);a.set_xlim(min(0,d.ST_low.min()-.03) if d.ST_low.min()<0 else 0,max(1.32,d.ST_high.max()*1.15+.1));a.set_xticks([0,.5,1],['0','0.5','1']);a.set_yticks([]);a.spines['left'].set_visible(False);a.set_xlabel('Total-order Sobol index')
 plot(stem+'',f,captions['full'])
def pareto():
 d=data('pathway-pareto');fr=d[d.on_restricted_emissions_vs_profit].sort_values('emissions_t_co2e_per_yr')
 f=plt.figure(figsize=(8.4,4.8));a=f.add_axes([.12,.17,.85,.68])
 a.scatter(d.emissions_t_co2e_per_yr/1000,d.profit_usd_per_yr/1e6,s=18,c=GRAY,edgecolors='none',zorder=1,label='Candidates')
 a.plot(fr.emissions_t_co2e_per_yr/1000,fr.profit_usd_per_yr/1e6,'-o',color=GREEN,lw=2,ms=7,mfc='white',mew=1.6,zorder=3,label='Exact front')
 anns={}
 chosen=fr.iloc[np.unique(np.linspace(0,len(fr)-1,min(MAX_FRONT_LABELS,len(fr)),dtype=int))]
 for row in chosen.itertuples():
  point=(row.emissions_t_co2e_per_yr/1000,row.profit_usd_per_yr/1e6)
  if point[0]>EMISSIONS_LIMIT_KT:continue
  if row.label in ROUTE_LABELS:
   label,offset,relpos=ROUTE_LABELS[row.label];xy=(point[0]+offset[0],point[1]+offset[1]);coords='data'
  else:
   label=row.label.replace(' + lf',', landfill').replace(' + we',', incineration').replace(' > ',' → ')
   xy=(8,12);coords='offset points';relpos=(0,.5)
  anns[label]=a.annotate(label,point,xytext=xy,textcoords=coords,fontsize=FS,va='center',arrowprops=dict(arrowstyle='-',color='#243B53',lw=.85,relpos=relpos),bbox=dict(facecolor='white',edgecolor='none',pad=1.5),zorder=5)
 a.set_xlim(0,EMISSIONS_LIMIT_KT);a.xaxis.set_major_locator(MaxNLocator(nbins=5,steps=[1,2,5,10]));visible=d[d.emissions_t_co2e_per_yr/1000<=EMISSIONS_LIMIT_KT];a.set_ylim(min(PARETO_Y_LIMITS[0],visible.profit_usd_per_yr.min()/1e6-.2),max(PARETO_Y_LIMITS[1],visible.profit_usd_per_yr.max()/1e6+1));a.set_xlabel('Emissions (kt CO₂e/year)');a.set_ylabel('Profit (million USD/year)');a.axhline(0,color='#E0E6E2',lw=.7,zorder=0);a.legend(loc='lower left',bbox_to_anchor=(0,1.02),frameon=False,ncol=2,borderaxespad=0)
 plot('pathway-pareto',f,captions['full'])
 return []
def advantage():
    d = data('profit-advantage')
    assert d[['simple_candidates', 'extra_candidates']].gt(0).all().all()
    assert not d.isna().any().any()
    assert not d.duplicated(['plant', 'draw']).any()
    f = plt.figure(figsize=(5.6, 4.8))
    a = f.add_axes([.09, .17, .86, .65])
    a.set_xlim(min(DELTA_X_LIMITS[0],d.advantage_usd_per_t_feed.min()-30),max(DELTA_X_LIMITS[1],d.advantage_usd_per_t_feed.max()+30)); a.set_ylim(0, 2.9)
    a.axvline(0, color='#71867C', lw=1, ls=(0, (3, 3)), zorder=0)
    f.text(.05, .92, FORMULA, fontsize=FS)
    summary = []
    rng = np.random.default_rng(JITTER_SEED)
    for (plant,label),y,color in zip(PLANTS,[2.04,.74],[GREEN,SLATE]):
        values = d.loc[d.plant == plant, 'advantage_usd_per_t_feed'].to_numpy()
        lo, med, hi = np.quantile(values, [PERCENTILES[0], .5, PERCENTILES[1]])
        a.text(a.get_xlim()[0], y+.43, label, fontsize=FS, va='center',
               bbox=dict(facecolor='white', edgecolor='none', pad=2), zorder=5)
        a.scatter(values, y+rng.uniform(-.20, .20, len(values)), s=15,
                  color=color, alpha=.27, edgecolors='none', zorder=1)
        a.hlines(y, lo, hi, color=color, lw=3, zorder=2)
        a.vlines([lo, hi], y-.07, y+.07, color=color, lw=1.6, zorder=3)
        a.scatter([med], [y], marker='D', s=65, color=color,
                  edgecolors='white', linewidths=1, zorder=4)
        summary.append(dict(plant=plant, draws=len(values), median_usd_per_t_feed=med,
                            p05_usd_per_t_feed=lo, p95_usd_per_t_feed=hi,
                            min_usd_per_t_feed=values.min(), max_usd_per_t_feed=values.max(),
                            additional_washes_better=int((values > 0).sum())))
    a.xaxis.set_major_locator(MaxNLocator(nbins=5,steps=[1,1.5,3,5,10])); a.set_yticks([])
    a.spines['left'].set_visible(False)
    a.set_xlabel('Extra profit (USD/t feed)')
    saveplot('profit-advantage', f, captions['full'])
    jd(ROOT/'profit-summary.json', summary)


def compose():
 r=root(W,289.2);text(r,10,22,f'{POLYMER} in {SOLVENT.lower()} · {ENERGY_CASE}',True);text(r,400,22,'MSP sensitivity',True);text(r,713,22,'GWP sensitivity',True)
 for stem,x in [('wash-cost-surface',0),('sobol-msp',381.6),('sobol-gwp',694.8)]:embed(r,R/(stem+'.svg'),x,30,stem)
 export_svg('panel-1-wash-cost-sensitivity',r,captions['full'])
 r=root(W,H);embed(r,R/'schematic.svg',0,0,'schematic');embed(r,R/'panel-1-wash-cost-sensitivity.svg',0,338,'wash')
 embed(r,R/'pathway-pareto.svg',0,638,'pareto');embed(r,R/'profit-advantage.svg',604.8,638,'advantage')
 text(r,10,641,'Profit–emissions trade-off',True);text(r,620,641,'Value of Additional Washes',True)
 export_svg('hpc-pathway-main',r,captions['full'],word=True)
 (ROOT/'hpc-pathway-main.caption.txt').write_text(captions['full'])
 (ROOT/'hpc-pathway-main.manuscript-caption.txt').write_text(captions['manuscript'])

def build_captions():
 expected=json.loads((ASSETS/'reference-data-hashes.json').read_text())
 reference_settings=json.loads((ASSETS/'reference-settings.json').read_text())
 current_settings=json.loads(json.dumps({n:globals()[n] for n in reference_settings}))
 unchanged=all(sha(ROOT/n)==h for n,h in expected.items()) and reference_settings==current_settings
 if unchanged:
  return {'full':(ASSETS/'reference-caption.txt').read_text(),'manuscript':(ASSETS/'reference-manuscript-caption.txt').read_text()}
 # Never silently retain old numerical claims when the extracted data changes.
 surface=data('wash-cost-surface');p=data('pathway-pareto');d=data('profit-advantage')
 stats=[]
 for plant,label in PLANTS:
  values=d.loc[d.plant==plant,'advantage_usd_per_t_feed'];q=np.quantile(values,[PERCENTILES[0],.5,PERCENTILES[1]])
  stats.append(f'{label}: {len(values)} scenarios; median {q[1]:+.1f} USD/t feed; {100*PERCENTILES[0]:g}th–{100*PERCENTILES[1]:g}th percentiles {q[0]:+.1f} to {q[2]:+.1f}; additional washes win in {int((values>0).sum())} scenarios.')
 caption=(f'Agent-driven techno-economic and life-cycle optimization. Top: illustrative query and source-derived tool workflow; not a recorded execution. Feed: {FEED_DESCRIPTION}. Middle: {POLYMER}/{SOLVENT}, energy case {ENERGY_CASE}, {len(surface)} sampled capacity/composition settings on a logarithmic MSP colour scale. Sobol bars show the {TOP_SOBOL_FACTORS} largest total-order indices from a separately supplied campaign analysis, with supplied bootstrap intervals. '
 +f'Bottom left: {len(p)} candidate routes after the configured resale exclusion ({EXCLUDE_RESALE}); {int(p.on_restricted_emissions_vs_profit.sum())} points on the recomputed emissions–profit front. {int((p.emissions_t_co2e_per_yr/1000>EMISSIONS_LIMIT_KT).sum())} candidates lie beyond the {EMISSIONS_LIMIT_KT:g} kt CO₂e/year view. '
 +f'Bottom right: best profit among {MULTIPLE_WASHES[0]}–{MULTIPLE_WASHES[1]} washes minus best profit among {SIMPLE_WASHES[0]}–{SIMPLE_WASHES[1]} washes, divided by annual feed tonnes. Dots are sampled scenarios, diamonds are medians, lines are sample percentile intervals, not confidence intervals. '+' '.join(stats)
 +' The shared-capacity case charges the feed its throughput share of costs, assuming other feeds utilize remaining capacity. These are model predictions, not experimental results or empirical probabilities. Gate-to-gate emissions give no credit for displaced virgin resin. Resale exclusion is motivated by unestablished residual purity. Reconstructed downstream costs, including zero incineration/gasification operating costs in the original campaign, require review after model updates. '
 +f'The recorded base recovery assumption is {BASE_RECOVERY_PERCENT:.6f}%; the recorded sensitivity recovery range is {RECOVERY_RANGE_PERCENT}. These assumptions are configuration metadata, not inferred from these four tables. Review them, the illustrative query, feed/plant labels and DAG after model changes.\n')
 (ROOT/'CAPTION_REVIEW_REQUIRED.txt').write_text('Input extraction differs from the frozen reference. Captions were regenerated from new values. Review scientific assumptions, query, labels, Sobol sampling design and DAG against the updated model.\n')
 return {'full':caption,'manuscript':caption}

if __name__=='__main__':
 load_data();captions=build_captions()
 schematic();surface();sobol('sobol-msp',GREEN);sobol('sobol-gwp',SLATE);pareto();advantage();compose()
 jd(ROOT/'run.json',{'data_files':{n:[{'path':str(p),'sha256':sha(p)} for p in files] for n,files in TABLES.items()},'word_size_inches':[FIGURE_WIDTH_IN,FIGURE_WIDTH_IN*H/W],'data_rows':{n:len(d) for n,d in frames.items()},'settings':{n:v for n,v in globals().copy().items() if n.isupper() and isinstance(v,(str,int,float,list,tuple,dict)) and n not in ['TABLES']}})
 CON.close()
