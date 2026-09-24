"""Ownership and process checks for a paused, checkpoint-preserving tail split."""
import hashlib,json,os,time
from pathlib import Path


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    with temp.open('w') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    temp.replace(path)


def process(pid):
    p=Path('/proc')/str(pid)
    stat=(p/'stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=pid,start_ticks=stat[19],state=stat[0],uid=p.stat().st_uid,
                command=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode().strip(),
                cwd=str((p/'cwd').resolve()),
                threads=[(x/'stat').read_text().rsplit(')',1)[1].split()[0] for x in (p/'task').iterdir()])


def checked_process(identity,stopped=False):
    actual=process(identity['pid'])
    for k in ['pid','start_ticks','uid','command','cwd']:assert actual[k]==identity[k],(k,actual)
    if stopped:assert actual['state']=='T' and all(x=='T' for x in actual['threads']),actual
    return actual


def keys_for(units,solvents):
    return [(u['id'],s['name'],r) for s in solvents for u in units for r in ['RT','high']]


def keyname(key):return '__'.join(key)+'.json'


def partition_ownership(units,solvents,sealed,groups=3):
    """One stopped sequential writer implies a sealed prefix and one in-flight unit.

    Keep the first missing unit with that writer. No helper can enter its unit.
    All other units' missing systems are assigned to exactly one helper.
    """
    keys=keys_for(units,solvents);known=set(sealed)
    assert known<=set(keys)
    missing=[key for key in keys if key not in known]
    assert missing,'Chunk already complete'
    first=keys.index(missing[0])
    assert known==set(keys[:first]),'Original must have an exact sealed prefix'
    retained=missing[0][0]
    work={u['id']:[k for k in missing if k[0]==u['id']] for u in units if u['id']!=retained}
    buckets=[[] for _ in range(groups)];sizes=[0]*groups
    for unit,todo in sorted(work.items(),key=lambda p:(-len(p[1]),p[0])):
        if not todo:continue
        i=min(range(groups),key=lambda i:sizes[i]);buckets[i].append(unit);sizes[i]+=len(todo)
    assigned={u for group in buckets for u in group}
    assert retained not in assigned and sum(map(len,buckets))==len(assigned)
    assert len(known)+sum(sizes)+sum(k[0]==retained for k in missing)==len(keys)
    return dict(retained_unit=retained,first_missing=list(missing[0]),groups=buckets,
                helper_systems=sizes,sealed_count=len(known),total_systems=len(keys),
                retained_missing=sum(k[0]==retained for k in missing))
