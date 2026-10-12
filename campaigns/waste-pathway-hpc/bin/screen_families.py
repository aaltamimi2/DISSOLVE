"""Local screen: which (polymer, solvent, temperature) washes exist across polymer families (equal fractions)."""
import itertools, json, sys, collections
from dissolve import waste_pathway_data as d
from dissolve.waste_pathway_milp import Feed
polymers = ["LDPE", "HDPE", "PET", "NYLON6", "EVOH"]
seen = collections.Counter(); temps = collections.defaultdict(set)
for n in (2, 3, 4, 5):
    for fam in itertools.combinations(polymers, n):
        feed = Feed(10000.0, {p: 1.0 / n for p in fam})
        table = d.build_stage_table(feed, max_washes=3, max_options=6, confirm_live_tea=False)
        for s in table.pending:
            seen[(s.polymer, s.solvent)] += 1; temps[(s.polymer, s.solvent)].add(s.temperature_c)
        print(fam, len(table.pending), flush=True)
json.dump([{"polymer": p, "solvent": s, "count": c, "temperatures_c": sorted(temps[(p, s)])} for (p, s), c in seen.most_common()], open(sys.argv[1], "w"), indent=1)
