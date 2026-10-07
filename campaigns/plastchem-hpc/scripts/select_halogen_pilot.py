"""A-11 pilot cohort, chosen locally and staged: 30 tasks evenly spaced by atom count, topped up so every halogen
class has at least two members and iodine at least three, at most 40 in all. Deterministic."""
import json
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'state/halogen-v1'/'halogen'
manifest=json.loads((P/'manifest.json').read_text());molecules=manifest['molecules']
indices=json.loads((P/'submission-indices.json').read_text())
ordered=sorted(indices,key=lambda i:(molecules[i]['atoms'],i))
chosen=[ordered[round(k*(len(ordered)-1)/29)] for k in range(30)]
def classes(i):return set(molecules[i]['halogens'].split(';'))
need={'F':2,'Cl':2,'Br':2,'I':3}
for element,count in need.items():
    have=[i for i in chosen if element in classes(i)]
    pool=[i for i in ordered if element in classes(i) and i not in chosen]
    while len(have)<count and pool and len(chosen)<40:
        pick=pool[len(pool)//2] if not have else pool[0] if len(have)==1 else pool[-1]
        chosen.append(pick);have.append(pick);pool.remove(pick)
assert len(chosen)==len(set(chosen))<=40
(P/'pilot-indices.json').write_text(json.dumps(chosen)+'\n')
print(json.dumps({'pilot':len(chosen),'atoms':sorted(molecules[i]['atoms'] for i in chosen),'by_halogen':{e:sum(e in classes(i) for i in chosen) for e in need}}))
