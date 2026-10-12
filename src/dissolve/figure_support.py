"""Shared by the tools that draw their own figures: where saved requests go, which Python can draw, and showing the result.

The agent's .venv does not include matplotlib, so a figure is drawn by the first Python on the machine that has it. Opening the
figure in the user's viewer can be switched off with DISSOLVE_NO_OPEN (the tests do).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Callable

RESULTS_DIR_ENV = "DISSOLVE_PATHWAY_RESULTS_DIR"


def _matplotlib_here() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _python_with_matplotlib() -> str | None:
    """A Python on this machine that can draw the figure: the agent's own, else the first one found with matplotlib."""
    if _matplotlib_here():
        return sys.executable
    for name in ("python3", "python"):
        exe = shutil.which(name)
        if exe and subprocess.run([exe, "-c", "import matplotlib"], capture_output=True).returncode == 0:
            return exe
    return None


def _open_file(path: Path) -> bool:
    """Show a file in the user's default viewer. Never raises; False when it could not be opened."""
    if os.environ.get("DISSOLVE_NO_OPEN"):
        return False
    try:
        if sys.platform == "darwin":
            command = ["open", str(path)]
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        else:
            command = ["xdg-open", str(path)]
        return subprocess.run(command, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False



def _results_dir() -> Path:
    """The folder saved requests and their figures go to."""
    return Path(os.environ.get(RESULTS_DIR_ENV) or Path.home() / ".dissolve" / "pathway_results")


def run_plot(draw_here: Callable[[], None], module_file: str, args: list[str]) -> str | None:
    """Draw a figure with a Python that has matplotlib: in this process when it can, else by running the plotting module's file
    with ``args`` in another Python. Returns None on success, or the reason it could not be drawn."""
    exe = _python_with_matplotlib()
    if exe is None:
        return ("no Python with matplotlib was found: install it (python3 -m pip install matplotlib) and ask again")
    try:
        if exe == sys.executable:
            draw_here()
            return None
        done = subprocess.run([exe, module_file, *args], capture_output=True, text=True, timeout=180)
        return None if done.returncode == 0 else f"the plot could not be drawn: {done.stderr.strip()[-300:]}"
    except Exception as error:  # a plotting aid must never fail the analysis
        return f"the plot could not be drawn: {type(error).__name__}: {error}"
