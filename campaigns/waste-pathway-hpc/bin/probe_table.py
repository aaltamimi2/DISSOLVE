import json, time
from dissolve import waste_pathway_data as d, tea
from dissolve.waste_pathway_milp import Feed
print("admitted solvents", len(d.admitted_solvents()), "outside scope", len(d.solvents_outside_scope()))
for name, fr in {"published": {"LDPE": .6, "PET": .2, "NYLON6": .1, "EVOH": .1}, "second": {"HDPE": .4, "LDPE": .25, "PET": .2, "EVOH": .15},
                 "five": {"LDPE": .3, "HDPE": .2, "PET": .2, "NYLON6": .15, "EVOH": .15}}.items():
    for washes, opts in ((2, 3), (3, 8)):
        t = time.monotonic(); feed = Feed(8000.0, fr)
        table = d.build_stage_table(feed, max_washes=washes, max_options=opts, confirm_live_tea=False)
        print(name, "washes", washes, "options", opts, "pending", len(table.pending), "stored-costed", len(table.stages), "failed", len(table.failed), "no_option", table.no_option, round(time.monotonic() - t, 1), "s")
        if washes == 3:
            for s in table.pending: print("   ", sorted(s.removed_before), s.polymer, s.solvent, s.temperature_c, round(s.mass_in_t), round(s.target_mass_percent, 2))
            for f in table.failed[:4]: print("   failed", {k: str(v)[:90] for k, v in f.items()})
