"""Draw the three Pareto fronts of the latest waste-pathway request (see src/dissolve/waste_pathway_plot.py).

    python3 scripts/plot_pareto.py [saved_request.json] [--out fronts.png]

Runs with any Python that has matplotlib; it needs no installed dissolve package.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "src" / "dissolve" / "waste_pathway_plot.py"), run_name="__main__")
