"""Observed 100-wt% rounding is preserved; genuine out-of-range values rejected."""
import datetime,json
from pathlib import Path
import duckdb
from verify_phase9_delivery import verify_qualified_ranges
D=Path('/mnt/r/plastchem-euler/phase9-v1')
r=json.loads((D/'observed-endpoint-roundoff.json').read_text())['value']
x=r['solute_mole_fraction_solubility'];w=r['solute_wt_percent_solubility']
assert x==1 and w==100.00000000000001
con=duckdb.connect();con.execute('CREATE TABLE lle_rows(value_validated BOOLEAN,solute_mole_fraction_solubility DOUBLE,solute_wt_percent_solubility DOUBLE)')
con.execute('INSERT INTO lle_rows VALUES (TRUE,?,?)',[x,w]);assert verify_qualified_ranges(con)==1
assert con.execute('SELECT solute_wt_percent_solubility FROM lle_rows').fetchone()[0]==w
checks=['Observed raw 100.00000000000001 wt% endpoint accepted without changing its stored value']
for bad in [100.0000000000001,100.0001,-1e-10,float('nan'),float('inf'),None]:
 con.execute('UPDATE lle_rows SET solute_wt_percent_solubility=?',[bad])
 try:verify_qualified_ranges(con)
 except AssertionError:checks.append('Rejected weight value '+repr(bad))
 else:raise AssertionError(bad)
con.execute('UPDATE lle_rows SET solute_wt_percent_solubility=100,solute_mole_fraction_solubility=1.0000000000001')
try:verify_qualified_ranges(con)
except AssertionError:checks.append('Rejected mole fraction above one')
else:raise AssertionError('Invalid mole fraction accepted')
con.close()
result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='passed',count=len(checks),checks=checks,
            scope='Observed raw endpoint and explicit out-of-range controls. No source data, model, solver, threshold or exported number changed.')
(D/'endpoint-roundoff-controls.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
