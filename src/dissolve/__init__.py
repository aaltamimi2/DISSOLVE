"""DISSOLVE: a tool-using agent for solvent-based plastic separation (STRAP).

The model calls deterministic engines (thermodynamics, separation, safety, TEA/LCA,
contaminants, and the literature corpus) through one flat registry, `agent.REGISTRY`.
`corpus.py` rebuilds a literature index from your own PDFs with the recipe the served
corpus was built with.
"""

__version__ = "0.1"

#: The release identity. Use this wherever the system names itself — CLI banner,
#: provenance tags, subprocess user-agents, emitted artifacts. One constant so a
#: rename is one edit, and so a v11 artifact is never mistaken for a v12 one.
RELEASE = "dissolve-v12-0.1"

from .agent import BY_NAME, REGISTRY  # noqa: F401
