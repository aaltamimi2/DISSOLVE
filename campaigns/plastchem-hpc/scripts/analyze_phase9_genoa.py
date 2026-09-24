"""Compare the complete eight-molecule Genoa gate with its pinned Milan run."""
import collections,datetime,hashlib,json,math,shutil
from pathlib import Path
from analyze_phase9_gate import collected_records

D=Path('/mnt/r/plastchem-euler/phase9-v1')
R=Path(__file__).resolve().parents[1]


def main():
    plan=json.loads((D/'genoa-gate-plan.json').read_text());n=len(plan['chunks'][0]);assert n==8
    final=D/'genoa-comparison-final.json'
    if final.exists():
        sealed=json.loads(final.read_text())
        assert sealed['plan_sha256']==hashlib.sha256((D/'genoa-gate-plan.json').read_bytes()).hexdigest()
        assert sealed['worker_cpu_sha256']==hashlib.sha256((R/'scripts/phase9_worker_cpu.py').read_bytes()).hexdigest()
        registry=json.loads((D/'collection.json').read_text())
        for path,digest in sealed['complete_checkpoint_pins'].items():assert registry['files'][path]['sha256']==digest
        # Preserve the proof's UTC/hash across transport retries and launch pins.
        if not (D/'genoa-comparison.json').exists():shutil.copyfile(final,D/'genoa-comparison.json')
        else:assert (D/'genoa-comparison.json').read_bytes()==final.read_bytes()
        print(json.dumps(sealed,indent=2));return sealed
    reference={};new={}
    for prefix,chunk,dest in [('gate-results-v1','0003',reference),(plan['output'],'0000',new)]:
        for path,value in collected_records(prefix,include_activities=True,chunk_id=chunk):
            if path.split('/')[1]!=chunk:continue
            key='/'.join(path.split('/')[2:]);assert key not in dest;dest[key]=value
    errors=[];maximum=collections.defaultdict(float);counts=collections.Counter()
    def compare(label,a,b,where):
        delta=abs(float(a)-float(b));assert math.isfinite(delta)
        maximum[label]=max(maximum[label],delta);counts[label]+=1
        if delta>1e-9:errors.append(dict(kind=label,where=where,difference=delta,milan=a,genoa=b))
    for key,value in new.items():
        assert key in reference,key;old=reference[key]
        if key.startswith('activities/'):
            assert value['phase_sha256']==old['phase_sha256']
            assert value['solute_x']==old['solute_x']==0 and value['reference_state']==old['reference_state']=='pure_component'
            assert len(value['values'])==len(old['values'])==n
            for i,(a,b) in enumerate(zip(old['values'],value['values'])):compare('phase_ln_gamma',a,b,[key,i])
        elif key.startswith('partition/'):
            assert len(value)==len(old)==640
            oldrows={(r['unit'],r['polymer'],r['solvent'],r['convention']):r for r in old}
            seen=set()
            for r in value:
                k=(r['unit'],r['polymer'],r['solvent'],r['convention']);assert k not in seen;seen.add(k)
                o=oldrows[k]
                for field in ['inchikey','solute_surface_sha256','solvent_surface_sha256','status','temperature_K']:assert r[field]==o[field]
                for field in ['logP_x','logP_concentration','ln_gamma_polymer','ln_gamma_solvent']:compare(field,o[field],r[field],k)
        elif key.startswith('lle/'):
            for field in ['unit','inchikey','solvent','regime','temperature_K']:assert value[field]==old[field]
            for field in ['solute_sha256','solvent_sha256','temperature_K']:assert value['signature'][field]==old['signature'][field]
            counts['LLE_systems']+=1
            if any(value.get(k)!=old.get(k) for k in ['status','above_15_mol_percent','above_15_wt_percent']):
                errors.append(dict(kind='LLE_verdict',where=key,milan=old['status'],genoa=value['status']))
    required={'phase_ln_gamma':268*n,'logP_x':640*n,'logP_concentration':640*n,'ln_gamma_polymer':640*n,'ln_gamma_solvent':640*n,'LLE_systems':64*n}
    complete='complete.json' in new and all(counts[k]==v for k,v in required.items())
    models={}
    for label,data in [('milan',reference),('genoa',new)]:
        if 'complete.json' in data:models[label]=data['complete.json']['execution']
    if complete:
        assert new['complete.json']['unit_ids']==reference['complete.json']['unit_ids']
        assert 'EPYC 7763' in models['milan']['cpu_model']
        assert 'EPYC 9' in models['genoa']['cpu_model']
        assert models['genoa']['node'].split('.')[0] in [f'euler{i}' for i in range(146,154)]
    out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='failed' if errors else ('passed' if complete else 'incomplete'),
             tolerance=1e-9,counts=dict(counts),expected_counts=required,maximum_absolute_differences=dict(maximum),errors=errors,
             execution=models,scope='Every partition value on both bases, every pure-phase solute ln gamma, every recorded effective polymer/solvent ln gamma; every LLE status and both 15% verdicts. Same surfaces and environment.',
             plan_sha256=hashlib.sha256((D/'genoa-gate-plan.json').read_bytes()).hexdigest(),
             worker_cpu_sha256=hashlib.sha256((R/'scripts/phase9_worker_cpu.py').read_bytes()).hexdigest())
    if complete:
        registry=json.loads((D/'collection.json').read_text())
        out['complete_checkpoint_pins']={path:registry['files'][path]['sha256'] for path in ['gate-results-v1/0003/complete.json',plan['output']+'/0000/complete.json']}
    p=D/'genoa-comparison.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(out,indent=2)+'\n');tmp.replace(p)
    if complete:
        shutil.copyfile(p,final)
        report=R/'reports/phase9-2026-09-23';shutil.copyfile(p,report/'genoa-comparison-complete.json')
        path=report/'REPORT.md';text=path.read_text();marker='## Genoa usability result'
        if marker not in text:
            decision='Production may use `(milan|genoa)&cpu`.' if not errors else 'Production remains restricted to Milan; the discrepancies are recorded in the comparison JSON.'
            text+='\n'+marker+'\n\n'+f"At {out['utc']}, the eight-molecule comparison **{out['status']}**: {dict(counts)}. Maximum differences: {dict(maximum)}. Milan CPU: **{models['milan']['cpu_model']}**, {models['milan']['node']}; Genoa CPU: **{models['genoa']['cpu_model']}**, {models['genoa']['node']}. Tolerance is 1e-9 for logP and ln gamma; every LLE status and both 15% verdicts must match. {decision} Solvent DFT and every ORCA step stay on Milan. The global cap stays 64. Evidence: `genoa-comparison-complete.json`.\n"
            path.write_text(text);shutil.copyfile(path,D/'REPORT.md')
    print(json.dumps(out,indent=2));return out


if __name__=='__main__':main()
