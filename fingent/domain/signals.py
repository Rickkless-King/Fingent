"""
Signal definitions for Fingent.

Signals are the standardized output of each analysis node.
They represent actionable insights extracted from raw data.
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from fingent.core.timeutil import format_timestamp, now_utc


class SignalDirection(str, Enum):
    """Direction of a signal."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    HAWKISH = "hawkish"     # For Fed/monetary policy
    DOVISH = "dovish"


class SignalName(str, Enum):
    """
    Predefined signal names.

    Each node produces specific signals that Synthesize can aggregate.
    """
    # Macro signals (from MacroAuditorNode)
    HAWKISH_BIAS = "hawkish_bias"
    DOVISH_BIAS = "dovish_bias"
    INFLATION_RISING = "inflation_rising"
    INFLATION_COOLING = "inflation_cooling"
    LABOR_STRONG = "labor_strong"
    LABOR_WEAK = "labor_weak"

    # Cross-asset signals (from CrossAssetNode)
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    FLIGHT_TO_SAFETY = "flight_to_safety"
    YIELD_CURVE_INVERSION = "yield_curve_inversion"
    ASSET_DIVERGENCE = "asset_divergence"
    CRYPTO_MOMENTUM = "crypto_momentum"

    # Sentiment signals (from NewsImpactNode)
    SENTIMENT_BULLISH = "sentiment_bullish"
    SENTIMENT_BEARISH = "sentiment_bearish"
    SENTIMENT_MIXED = "sentiment_mixed"

    # Volatility signals
    VIX_ELEVATED = "vix_elevated"
    VIX_SPIKE = "vix_spike"
    VIX_CALM = "vix_calm"


@dataclass
class Signal:
    """
    A single signal produced by an analysis node.

    Attributes:
        id: Unique identifier (node_name + signal_name + run_id)
        name: Signal name (from SignalName enum or custom)
        direction: Signal direction (bullish/bearish/neutral/etc.)
        score: Signal strength (-1 to 1, 0 = neutral)
        confidence: Confidence level (0 to 1)
        source_node: Node that produced this signal
        evidence: Supporting data/explanation
        timestamp: When the signal was generated
    """
    id: str
    name: str
    direction: str
    score: float                    # -1 to 1
    confidence: float = 0.5         # 0 to 1
    source_node: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    run_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_timestamp(now_utc())
        if not self.id:
            self.id = f"{self.source_node}_{self.name}_{self.run_id}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict for GraphState."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Signal":
        """Create Signal from dict."""
        return cls(**data)

    @property
    def is_significant(self) -> bool:
        """Check if signal is significant enough to report."""
        return abs(self.score) >= 0.3 and self.confidence >= 0.5


def create_signal(
    name: str,
    direction: str,
    score: float,
    *,
    source_node: str,
    run_id: str = "",
    confidence: float = 0.5,
    evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Factory function to create a signal dict.

    This returns a dict directly for easy insertion into GraphState.

    Args:
        name: Signal name (use SignalName enum values)
        direction: Signal direction (use SignalDirection enum values)
        score: Signal strength (-1 to 1)
        source_node: Name of the node producing this signal
        run_id: Current run ID
        confidence: Confidence level (0 to 1)
        evidence: Supporting data

    Returns:
        Signal as dict (JSON-serializable)

    Example:
        signal = create_signal(
            name=SignalName.HAWKISH_BIAS.value,
            direction=SignalDirection.HAWKISH.value,
            score=0.7,
            source_node="macro_auditor",
            run_id="run_20260124_070000",
            confidence=0.8,
            evidence={"fed_funds_rate": 5.25, "cpi_yoy": 3.2}
        )
    """
    signal_id = f"{source_node}_{name}_{run_id}"

    return {
        "id": signal_id,
        "name": name,
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "source_node": source_node,
        "evidence": evidence or {},
        "timestamp": format_timestamp(now_utc()),
        "run_id": run_id,
    }


def aggregate_signals(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate multiple signals into a summary.

    Args:
        signals: List of signal dicts

    Returns:
        Aggregated summary with overall direction and key signals
    """
    if not signals:
        return {
            "overall_direction": SignalDirection.NEUTRAL.value,
            "overall_score": 0,
            "signal_count": 0,
            "key_signals": [],
        }

    # Calculate weighted average score
    total_weight = sum(s.get("confidence", 0.5) for s in signals)
    if total_weight == 0:
        avg_score = 0
    else:
        avg_score = sum(
            s.get("score", 0) * s.get("confidence", 0.5) for s in signals
        ) / total_weight

    # Determine overall direction
    if avg_score > 0.2:
        overall_direction = SignalDirection.BULLISH.value
    elif avg_score < -0.2:
        overall_direction = SignalDirection.BEARISH.value
    else:
        overall_direction = SignalDirection.NEUTRAL.value

    # Get significant signals
    significant = [s for s in signals if abs(s.get("score", 0)) >= 0.3]
    key_signals = sorted(
        significant,
        key=lambda x: abs(x.get("score", 0)) * x.get("confidence", 0.5),
        reverse=True,
    )[:5]

    return {
        "overall_direction": overall_direction,
        "overall_score": round(avg_score, 3),
        "signal_count": len(signals),
        "significant_count": len(significant),
        "key_signals": key_signals,
    }
