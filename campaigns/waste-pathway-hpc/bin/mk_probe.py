import json, sys
from dissolve import waste_pathway_data as d
cases = [("LDPE", "toluene", 110.0, 20000.0, 60.0), ("EVOH", "ethylene glycol", 130.0, 8000.0, 10.0), ("LDPE", "toluene", 110.0, 5000.0, 35.0)]
with open(sys.argv[1], "w") as out:
    for i, (p, s, t, cap, share) in enumerate(cases):
        try:
            cfg = d.process_config(d.StageSpec(frozenset(), p, s, t, cap, share))
            out.write(json.dumps({"id": f"probe-{i}", "config": cfg}) + "\n")
        except Exception as e:
            print("skip", p, s, repr(e)[:200])
    cfg = d.process_config(d.StageSpec(frozenset(), "LDPE", "toluene", 110.0, 20000.0, 60.0), overrides={"irr": 0.13, "solvent_loss_pct": 0.5})
    out.write(json.dumps({"id": "probe-ovr", "config": cfg}) + "\n")
print(open(sys.argv[1]).read()[:900])
