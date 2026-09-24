"""Measured common-solvent DFT cost; licensed-derived records remain in bulk."""
import collections,csv,datetime,hashlib,json,re,statistics
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    registry=json.loads((D/'retrieved.json').read_text());rows=[]
    assert len(registry)==69 and all(r['status']=='converged' for r in registry.values())
    for key,pin in registry.items():
        path=D/'results'/key/'verified-result.json';assert sha(path)==pin['result_sha256']
        r=json.loads(path.read_text());assert r['connectivity_match'] and r['identity_verified']
        assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor' and r['resources']['cpus']==1
        assert r['resources']['request_memory']=='4G' and r['resources']['maxcore_mb']==1500
        assert all(r['stages'][stage]['exit_code']==0 for stage in ['opt','cosmo'])
        rss=r['batch_accounting'][4];match=re.fullmatch(r'([\d.]+)([KMGT]?)',rss);assert match,rss
        ram=float(match[1])*{'':1,'K':1024,'M':1024**2,'G':1024**3,'T':1024**4}[match[2]]
        rows.append(dict(name=r['input']['name'],inchikey=key,atoms=r['input']['atoms'],elapsed_seconds=r['elapsed_seconds'],
                         opt_seconds=r['stages']['opt']['wall_seconds'],cosmo_seconds=r['stages']['cosmo']['wall_seconds'],
                         allocated_cpu_seconds=r['allocated_cpu_seconds'],maxrss_bytes=ram,surface_bytes=r['surface_bytes'],
                         started_utc=r['started_utc'],finished_utc=r['finished_utc'],record_sha256=pin['result_sha256']))
    with (D/'common-solvent-measured-cost.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(sorted(rows,key=lambda r:r['name']))
    def stats(field):
        vals=sorted(r[field] for r in rows)
        return dict(minimum=min(vals),median=statistics.median(vals),mean=statistics.mean(vals),maximum=max(vals),sum=sum(vals))
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),denominator=69,converged=69,failed=0,
                 allocated_cpu_hours=sum(r['allocated_cpu_seconds'] for r in rows)/3600,
                 elapsed_seconds=stats('elapsed_seconds'),opt_seconds=stats('opt_seconds'),cosmo_seconds=stats('cosmo_seconds'),
                 peak_maxrss_gib=max(r['maxrss_bytes'] for r in rows)/1024**3,surface_bytes=stats('surface_bytes'),
                 atom_counts=stats('atoms'),first_start=min(r['started_utc'] for r in rows),last_finish=max(r['finished_utc'] for r in rows),
                 cpu_model='AMD EPYC 7763 64-Core Processor',cpus_per_task=1,requested_memory_gib=4,
                 cost_csv=str(D/'common-solvent-measured-cost.csv'),cost_csv_sha256=sha(D/'common-solvent-measured-cost.csv'),
                 scope='Measured terminal costs for this deliberately selected 69-solvent set only. No extrapolation to the remaining grid or experimental accuracy claim.')
    (D/'common-solvent-measured-cost.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
