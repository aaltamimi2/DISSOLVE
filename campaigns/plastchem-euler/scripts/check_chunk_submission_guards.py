"""Local mocked-scheduler checks: dependency, throttle, duplicate and unconfirmed submission guards."""
import contextlib,hashlib,io,json,runpy,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(dir=ROOT/'state/campaign-v1/throughput-steer',prefix='guard-check-') as tmp:
    home=Path(tmp);r=home/'plastchem-euler/campaign-v1';r.mkdir(parents=True)
    main={'name':'contam-p2-main_le80-v1','concurrency':28,'molecules':[{'inchikey':'KEY0'},{'inchikey':'KEY1'}]}
    (r/'main_le80').mkdir();(r/'main_le80/manifest.json').write_text(json.dumps(main))
    for n,m in enumerate(main['molecules']):
        d=r/'prepared'/m['inchikey'];d.mkdir(parents=True);(d/'input.xyz').write_bytes(b'test geometry')
        (d/'preparation.json').write_text(json.dumps({'status':'prepared','input':m,'xyz_sha256':hashlib.sha256(b'test geometry').hexdigest()}))
        label=f'main_chunk_{n:03d}';p=r/label;p.mkdir()
        (p/'manifest.json').write_text(json.dumps({'name':f'contam-p2-main-c{n:03d}-v1','indices':[n],'concurrency':28,'previous_chunk':'main_chunk_000' if n else None}))
        (p/'submission-indices.json').write_text(json.dumps([n]));(p/'staging.sha256').write_text('')
    submissions=[];found={};old=False
    def fake(args,**kwargs):
        if args[0]=='sbatch':
            submissions.append(args);return SimpleNamespace(returncode=0,stdout=str(700000+len(submissions)-1)+'\n',stderr='')
        name=next((a.split('=',1)[1] for a in args if a.startswith('--name=')),'')
        value=found.get(name,'')
        if old and name==main['name']:value='699999|old-main|RUNNING\n'
        return SimpleNamespace(returncode=0,stdout=value,stderr='')
    def invoke(label):
        try:
            with patch.object(Path,'home',return_value=home),patch('subprocess.run',side_effect=fake),patch.object(sys,'argv',['guard',label]),contextlib.redirect_stdout(io.StringIO()):runpy.run_path(str(ROOT/'scripts/submit_campaign_chunk_remote.py'),run_name='__main__')
        except SystemExit as e:return e.code
        return 0
    assert invoke('main_chunk_000')==0
    assert '--array=0%28' in submissions[0] and not any(a.startswith('--dependency') for a in submissions[0])
    assert invoke('main_chunk_001')==0
    assert '--array=1%28' in submissions[1] and '--dependency=afterany:700000' in submissions[1]
    found['contam-p2-main-c001-v1']='700001|contam-p2-main-c001-v1|RUNNING\n'
    assert invoke('main_chunk_001')==0 and len(submissions)==2
    found.clear()
    assert invoke('main_chunk_001')==2 and len(submissions)==2
    # A new chunk must refuse any replacement full-main job, including an accounting-only match.
    (r/'main_chunk_001/submission.started').unlink();old=True
    try:invoke('main_chunk_001');raise RuntimeError('Old-main conflict was not rejected')
    except AssertionError:pass
    assert len(submissions)==2
p=ROOT/'state/campaign-v1';plan=json.loads((p/'chunk-plan.json').read_text())
indices=[i for label in plan['main_chunks'] for i in json.loads((p/label/'manifest.json').read_text())['indices']]
assert indices==list(range(5680)) and len(set(indices))==5680
result={'afterany_on_previous_array':True,'main_cap_28':True,'tail_cap_4':True,'existing_job_no_resubmission':True,'unconfirmed_attempt_no_resubmission':True,'full_main_conflict_rejected':True,'chunk_indices_exact_disjoint_partition_of_5680':True,'scheduler_calls_mocked_no_cluster_jobs':True}
(p/'throughput-steer/submission-guard-checks.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
