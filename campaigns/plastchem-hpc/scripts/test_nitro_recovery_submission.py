"""Offline retry-submission guards; no scheduler or network calls."""
import contextlib, hashlib, io, json, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

CODE=Path(__file__).with_name('submit_nitro_retry_remote.py').read_text()
NAME='contam-polymer24a-nitro-retry1'


def scenario(mode):
    with tempfile.TemporaryDirectory(prefix='nitro-submit-test-') as temp:
        home=Path(temp);root=home/'plastchem-euler';r=root/'polymer-v1/nitro-retry1'
        for path in [r/'body',root/'phase83-v1',root/'phase9-v1',root/'phase9-solvent-library-v1']:path.mkdir(parents=True,exist_ok=True)
        (root/'phase9-v1/production-submission.json').write_text(json.dumps(dict(job_id='700',launch_utc='test-only')))
        if mode!='B_missing':(root/'phase9-solvent-library-v1/submission.json').write_text(json.dumps(dict(job_id='701')))
        molecules=[]
        for i in range(7):
            p=r/'prepared'/f'entry{i}';p.mkdir(parents=True);xyz='1\ntest-only\nHe 0 0 0\n';h=hashlib.sha256(xyz.encode()).hexdigest()
            (p/'input.xyz').write_text(xyz);(p/'preparation.json').write_text(json.dumps(dict(xyz_sha256=h)))
            molecules.append(dict(entry_id=f'entry{i}',inchikey='test-key',restart_provenance=dict(task=f'65676_{166+i}',source_failure_mode='scheduler_signal_10',xyz_sha256=h,geometry_identity=dict(identity_verified=True,connectivity_match=True,input_inchikey='test-key',geometry_sha256=h))))
        (r/'body/manifest.json').write_text(json.dumps(dict(name=NAME,molecules=molecules,walltime='48:00:00')))
        if mode=='unconfirmed':(r/'submission.started').write_text('earlier ambiguous attempt')
        calls=[];submitted=False
        def fake(args,**kwargs):
            nonlocal submitted
            calls.append(args)
            if args[0]=='sbatch':submitted=True;out='9001;test\n'
            elif args[0]=='squeue' and any(a.startswith('--name=') for a in args):
                out=f'9001_0|{NAME}|PENDING\n' if mode=='existing' else ''
            elif args[0]=='sacct' and '--name='+NAME in args:
                out=f'9001_0|{NAME}|PENDING|0:00|\n' if mode=='existing' else ''
            elif args[0]=='sacct':
                out=''.join(f'65676_{166+i}|'+('RUNNING' if mode=='original_running' else 'FAILED')+'|\n' for i in range(7))
            elif args[0]=='squeue':
                out='700_0|RUNNING|1|contam-phase9-production-v1\n701_0|PENDING|1|contam-phase9-common-solvents-v1\n63873_0|PENDING|1|contam-tier2-chno500700-v1\n65677_0|RUNNING|1|contam-polymer24a-large-v1\n'
                if submitted or mode=='existing':out+=''.join(f'9001_{i}|PENDING|1|{NAME}|(JobHeldUser)\n' for i in range(7))
            elif args[:3]==['scontrol','show','job']:out='JobId=9001 JobState=PENDING Reason=JobHeldUser\n'
            else:raise AssertionError(('Unexpected scheduler mutation',args))
            return SimpleNamespace(returncode=0,stdout=out,stderr='')
        error=None
        with patch('pathlib.Path.home',return_value=home),patch('subprocess.run',side_effect=fake),contextlib.redirect_stdout(io.StringIO()):
            try:exec(compile(CODE,'submit_nitro_retry_remote.py','exec'),{})
            except Exception as exc:error=type(exc).__name__+': '+str(exc)
        sbatch=[c for c in calls if c[0]=='sbatch']
        if mode=='new':
            assert error is None,error
            assert len(sbatch)==1
            command=sbatch[0]
            for required in ['--hold','--array=0-6%7','--cpus-per-task=1','--mem=4G','--time=48:00:00','--constraint=milan&cpu']:assert required in command
            assert '--dependency=afterany:63873:65677:700:701' in command
        elif mode=='existing':assert error is None and not sbatch,(error,sbatch)
        else:assert error is not None and not sbatch,(mode,error,sbatch)
        assert not any(c[:2]==['scontrol','release'] for c in calls)
        return dict(scenario=mode,submitted=len(sbatch),error=error)


results=[scenario(mode) for mode in ['new','existing','unconfirmed','original_running','B_missing']]
print(json.dumps(dict(status='passed',cases=len(results),scope='Offline scheduler mocks only',results=results),indent=2))
