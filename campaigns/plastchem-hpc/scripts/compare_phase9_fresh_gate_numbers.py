"""Quantify fresh-gate numerical differences without rerunning COSMO or fitting."""
import collections,gzip,json,math,sys
from pathlib import Path
from analyze_phase9_gate import collected_records
D=Path('/mnt/r/plastchem-euler/phase9-v1')

def main():
    summary=json.loads((D/'recovery-gate-fresh-local/summary.json').read_text())
    assert summary['status']=='passed' and summary['systems']==2240
    expected={}
    for path,value in collected_records('gate-results-v1'):
        if '/lle/' in path:expected[(value['unit'],value['solvent'],value['regime'])]=value
    maxima=collections.defaultdict(float);counts=collections.Counter();missing=[];worst={}
    for path in sorted((D/'recovery-gate-fresh-local').glob('*.jsonl.gz')):
        with gzip.open(path,'rt') as f:
            for line in f:
                row=json.loads(line);key=(row['unit'],row['solvent'],row['regime']);a=expected.pop(key);b=row['result']
                for field in ['status','above_15_mol_percent','above_15_wt_percent']:assert a.get(field)==b.get(field)
                for field in ['solute_mole_fraction_solubility','solute_wt_percent_solubility']:
                    x,y=a.get(field),b.get(field)
                    if x is None or y is None:assert x==y;continue
                    diff=abs(x-y);counts[field]+=1
                    if diff>maxima[field]:maxima[field]=diff;worst[field]=key
                aa=a.get('activities',{});bb=b.get('activities',{})
                if not aa:
                    counts['systems_without_archived_activity_vectors']+=1
                elif set(aa)!=set(bb):missing.append(dict(system=key,reference_only=len(set(aa)-set(bb)),fresh_only=len(set(bb)-set(aa))))
                for x in aa.keys() & bb.keys():
                    vals=aa[x]
                    for va,vb in zip(vals,b['activities'][x]):
                        assert math.isfinite(va) and math.isfinite(vb)
                        delta=abs(va-vb);counts['ln_gamma']+=1
                        if delta>maxima['ln_gamma']:maxima['ln_gamma']=delta;worst['ln_gamma']=key+(x,)
    assert not expected
    result=dict(status='completed_comparison',systems=2240,maximum_absolute_differences=dict(maxima),numeric_values_compared=dict(counts),worst_systems=worst,activity_grid_coordinate_differences=missing,
                scope='Fresh local wrapped solver versus original Milan gate. Status and both verdict bases exactly identical. Solubility differences measured without calibration or correction. Compact collected references omit activity vectors; no fresh-versus-reference ln-gamma comparison is claimed for such systems.')
    (D/'recovery-gate-fresh-numeric-comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':main()
