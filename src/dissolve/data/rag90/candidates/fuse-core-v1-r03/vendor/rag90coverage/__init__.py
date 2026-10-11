"""Query-aspect coverage packing. Tier 0 helpers: no torch.
"""
from .aspects import decompose
from .weights import WEIGHTS, reweighted
__all__ = ["decompose", "WEIGHTS", "reweighted"]
