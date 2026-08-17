"""DISSOLVE v12 — clean-room routing/validation experiment.

Inherits the thermodynamic data asset and nothing else. No routing, no
validation, no answer layer carried over from v11.
"""

__version__ = "0.1"

#: The release identity. Use this wherever the system names itself — CLI banner,
#: provenance tags, subprocess user-agents, emitted artifacts. One constant so a
#: rename is one edit, and so a v11 artifact is never mistaken for a v12 one.
RELEASE = "dissolve-v12-0.1"

from .registry import REGISTRY, BY_NAME, call  # noqa: F401
