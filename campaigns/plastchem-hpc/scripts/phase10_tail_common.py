"""Exact RT-only ownership for a stopped A-10 sequential LLE writer."""
from phase9_tail_common import process, checked_process, sha, save, keyname


def keys_for(units,solvents):
    return [(u['id'],s['name'],'RT') for s in solvents for u in units]


def partition_ownership(units,solvents,sealed,groups=3):
    keys=keys_for(units,solvents)
    known=set(sealed);assert known<=set(keys)
    missing=[k for k in keys if k not in known];assert missing,'Already complete'
    first=keys.index(missing[0]);assert known==set(keys[:first]),'Exact original sealed prefix required'
    retained=missing[0][0]
    work={u['id']:[k for k in missing if k[0]==u['id']] for u in units if u['id']!=retained}
    buckets=[[] for _ in range(groups)];sizes=[0]*groups
    for unit,todo in sorted(work.items(),key=lambda r:(-len(r[1]),r[0])):
        if not todo:continue
        i=min(range(groups),key=lambda j:sizes[j]);buckets[i].append(unit);sizes[i]+=len(todo)
    assigned={u for group in buckets for u in group}
    assert retained not in assigned and sum(map(len,buckets))==len(assigned)
    assert len(known)+sum(sizes)+sum(k[0]==retained for k in missing)==len(keys)
    return dict(retained_unit=retained,first_missing=list(missing[0]),groups=buckets,
        helper_systems=sizes,sealed_count=len(known),total_systems=len(keys),
        retained_missing=sum(k[0]==retained for k in missing))
