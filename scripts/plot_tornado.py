"""Draw the tornado plot of the latest tea_tornado result (see src/dissolve/tea_tornado_plot.py).

    python3 scripts/plot_tornado.py [saved_tornado.json] [--out tornado.png]

Runs with any Python that has matplotlib; it needs no installed dissolve package.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "src" / "dissolve" / "tea_tornado_plot.py"), run_name="__main__")
