"""Image build: live TEA works in this image and reproduces the stored results exactly, one record per energy case
(the known-answer check of tests/test_tea.py), which also fills numba's cache so the site's first live TEA does not
compile. Any difference beyond 1e-12 relative fails the build.

    DISSOLVE_TEA_PYTHON=... DISSOLVE_PLASTICS_PATH=... python check_live_tea.py
"""
from dissolve import tea

status = tea.live_engine_status()
if not status.get("available"):
    raise SystemExit(f"live TEA is unavailable in this image: {status.get('reason')}: {status.get('detail')}")
for label in ("ldpe-route-c1", "ldpe-route-c2", "ldpe-route-c3"):
    record = next(r for r in tea._records() if str(r.get("label") or "").casefold() == label)
    live = tea._live(record["config"], 900)
    if "tea" not in live or "lca" not in live:
        raise SystemExit(f"{label}: live TEA failed: {live.get('error')}")
    for block in ("tea", "lca"):
        for key, stored in record["result"][block].items():
            if isinstance(stored, (int, float)) and not isinstance(stored, bool):
                if abs(live[block][key] - stored) > 1e-12 * max(1.0, abs(stored)):
                    raise SystemExit(f"{label} {block}.{key}: live {live[block][key]!r}, stored {stored!r}")
    print(f"{label}: reproduces the stored result (GWP {live['lca']['gwp_kg_co2e_per_kg']!r})", flush=True)
