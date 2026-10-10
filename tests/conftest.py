"""Keep the pathway tool's saved figure data out of the user's home directory, and its figure viewer closed, while tests run."""
import pytest


@pytest.fixture(autouse=True)
def _pathway_results_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DISSOLVE_PATHWAY_RESULTS_DIR", str(tmp_path / "pathway_results"))
    monkeypatch.setenv("DISSOLVE_NO_OPEN", "1")  # a test must never open a window
