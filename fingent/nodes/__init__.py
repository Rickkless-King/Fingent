"""
Nodes module - LangGraph workflow nodes

Each node:
- Inherits from BaseNode
- Implements run(state) -> partial state update
- Produces signals and/or processes data
- Handles errors gracefully (writes to state["errors"])
"""

from fingent.nodes.base import BaseNode
from fingent.nodes.bootstrap import BootstrapNode
from fingent.nodes.macro_auditor import MacroAuditorNode
from fingent.nodes.cross_asset import CrossAssetNode
from fingent.nodes.news_impact import NewsImpactNode
from fingent.nodes.synthesize_alert import SynthesizeAlertNode

__all__ = [
    "BaseNode",
    "BootstrapNode",
    "MacroAuditorNode",
    "CrossAssetNode",
    "NewsImpactNode",
    "SynthesizeAlertNode",
]
