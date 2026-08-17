"""DISSOLVE v12 — clean-room routing/validation experiment.

Inherits the thermodynamic data asset and nothing else. No routing, no
validation, no answer layer carried over from v11.
"""

__version__ = "0.1.0.dev0"

from .registry import REGISTRY, BY_NAME, call  # noqa: F401
