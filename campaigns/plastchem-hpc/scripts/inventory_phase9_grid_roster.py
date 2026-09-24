"""Read-only full product grid name mapping. No DFT and no licensed copies."""
import datetime,hashlib,json,re
from pathlib import Path
import duckdb
from inventory_phase9_solvents import SOURCE,D

p=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/data/thermodynamics.duckdb')
c=duckdb.connect(str(p),read_only=True)
keys=[r[0] for r in c.execute('select distinct solvent from solubility_grid order by 1').fetchall()];c.close()
names={}
for folder in SOURCE.iterdir():
    if folder.is_dir():
        for f in folder.glob('*.cosmo'):
            names.setdefault(re.sub(r'_c\d+$','',f.stem).casefold(),[]).append(str(f))
rows=[dict(solvent_key=k,match_basis='exact basename; geometry identity not yet checked',candidate_files=sorted(names.get(k.casefold(),[]))) for k in keys]
out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),product_asset=str(p),product_asset_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),denominator=len(keys),name_mapped=sum(bool(r['candidate_files']) for r in rows),source_root=str(SOURCE),rows=rows,scope='inventory only; remainder DFT awaits common-set measured cost report')
(D/'full-grid-name-inventory.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k!='rows'}))
