"""
Fingent Arbitrage Module.

Polymarket term structure arbitrage detection.

Components:
- strategy: Term structure detection algorithm
- risk: Risk filtering (volume, spread, depth)
- engine: Main arbitrage engine
- snapshot: Market snapshot storage
"""

from fingent.arb.strategy import TermStructureStrategy
from fingent.arb.risk import RiskManager
from fingent.arb.engine import ArbEngine

__all__ = [
    "TermStructureStrategy",
    "RiskManager",
    "ArbEngine",
]
