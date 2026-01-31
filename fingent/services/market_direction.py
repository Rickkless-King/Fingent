"""
Market Direction Service.

Calculates market direction based on ACTUAL MARKET DATA, not news sentiment.

Inspired by CNN Fear & Greed Index methodology:
- Market Momentum (S&P 500 vs moving average)
- VIX level (volatility)
- Price changes (actual returns)

News sentiment is only a supplementary factor with reduced weight.
"""

from dataclasses import dataclass
from typing import Any, Optional
from enum import Enum

from fingent.core.logging import LoggerMixin


class MarketDirection(str, Enum):
    """Market direction classifications."""
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"


@dataclass
class DirectionResult:
    """Result of market direction calculation."""
    direction: str
    score: float  # -1 to 1
    confidence: float  # 0 to 1
    components: dict[str, float]  # Individual component scores
    primary_driver: str  # What drove the direction
    explanation: str


# Weight configuration for different signal sources
# Market data should have much higher weight than news sentiment
SIGNAL_WEIGHTS = {
    # Primary factors (actual market data) - 70% total
    "cross_asset": 0.35,      # Actual price movements (S&P, VIX, etc.)
    "macro_auditor": 0.35,    # Macro indicators (Fed, inflation, etc.)

    # Secondary factors - 30% total
    "news_impact": 0.15,      # News sentiment (reduced from equal weight)
    "default": 0.15,          # Other signals
}

# VIX thresholds
VIX_FEAR_THRESHOLD = 25      # Above this = fear/bearish
VIX_EXTREME_FEAR = 35        # Above this = extreme fear
VIX_GREED_THRESHOLD = 15     # Below this = greed/bullish
VIX_EXTREME_GREED = 12       # Below this = extreme greed


class MarketDirectionCalculator(LoggerMixin):
    """
    Calculates market direction from actual market data.

    Unlike the old approach (averaging all signals equally),
    this prioritizes actual market data over news sentiment.
    """

    def __init__(self):
        pass

    def calculate_direction(
        self,
        signals: list[dict[str, Any]],
        market_data: Optional[dict[str, Any]] = None,
    ) -> DirectionResult:
        """
        Calculate market direction with proper weighting.

        Args:
            signals: All signals from nodes
            market_data: Raw market data (prices, VIX, etc.)

        Returns:
            DirectionResult with direction, score, and explanation
        """
        components = {}

        # 1. Calculate component scores from signals by source
        source_scores = self._aggregate_by_source(signals)

        # 2. If we have raw market data, use it directly for validation
        market_score = None
        if market_data:
            market_score = self._calculate_from_market_data(market_data)
            components["market_data_direct"] = market_score

        # 3. Calculate weighted score
        weighted_score = 0
        total_weight = 0

        for source, score_data in source_scores.items():
            weight = SIGNAL_WEIGHTS.get(source, SIGNAL_WEIGHTS["default"])
            weighted_score += score_data["score"] * weight
            total_weight += weight
            components[source] = score_data["score"]

        if total_weight > 0:
            weighted_score = weighted_score / total_weight

        # 4. CRITICAL: Market data is the PRIMARY source of truth
        # If we have actual market data, it should dominate the direction
        if market_score is not None:
            # Market data gets 80% weight, signals get 20%
            # This ensures direction follows actual market, not news sentiment
            final_score = market_score * 0.8 + weighted_score * 0.2
            self.logger.info(
                f"Direction blend: market_data={market_score:.2f} (80%) + "
                f"signals={weighted_score:.2f} (20%) = {final_score:.2f}"
            )
            weighted_score = final_score

        # 5. Determine direction and confidence
        direction, confidence = self._score_to_direction(weighted_score)

        # 6. Find primary driver
        primary_driver = self._find_primary_driver(source_scores, market_score)

        # 7. Generate explanation
        explanation = self._generate_explanation(
            direction, weighted_score, components, primary_driver
        )

        return DirectionResult(
            direction=direction,
            score=round(weighted_score, 3),
            confidence=confidence,
            components=components,
            primary_driver=primary_driver,
            explanation=explanation,
        )

    def _aggregate_by_source(
        self,
        signals: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Aggregate signals by their source node."""
        by_source = {}

        for signal in signals:
            source = signal.get("source_node", "unknown")
            if source not in by_source:
                by_source[source] = {
                    "signals": [],
                    "score": 0,
                    "confidence": 0,
                }
            by_source[source]["signals"].append(signal)

        # Calculate average score per source
        for source, data in by_source.items():
            if data["signals"]:
                total_conf = sum(s.get("confidence", 0.5) for s in data["signals"])
                if total_conf > 0:
                    weighted_score = sum(
                        s.get("score", 0) * s.get("confidence", 0.5)
                        for s in data["signals"]
                    ) / total_conf
                    data["score"] = weighted_score
                    data["confidence"] = total_conf / len(data["signals"])

        return by_source

    def _calculate_from_market_data(
        self,
        market_data: dict[str, Any],
    ) -> float:
        """
        Calculate direction score directly from market data.

        This is the "ground truth" - actual market movements.

        IMPORTANT: This is inspired by CNN Fear & Greed Index methodology.
        We use actual price changes, not news sentiment.
        """
        score = 0
        factors = 0
        changes = market_data.get("changes", {})

        # === 1. S&P 500 / SPY change (PRIMARY INDICATOR - 50% weight) ===
        spy_change = changes.get("SPY", {}).get("change_24h")
        if spy_change is not None:
            # SPY is the most important indicator
            # -2% or worse = strong bearish
            # -1% to -2% = bearish
            # -0.5% to -1% = slightly bearish
            # +0.5% to +1% = slightly bullish
            # +1% to +2% = bullish
            # +2% or more = strong bullish
            if spy_change <= -0.02:
                spy_score = -0.9
            elif spy_change <= -0.01:
                spy_score = -0.6
            elif spy_change <= -0.005:
                spy_score = -0.3
            elif spy_change >= 0.02:
                spy_score = 0.9
            elif spy_change >= 0.01:
                spy_score = 0.6
            elif spy_change >= 0.005:
                spy_score = 0.3
            else:
                spy_score = spy_change * 50  # Small moves map to small scores

            score += spy_score * 0.5  # 50% weight - SPY is king
            factors += 0.5
            self.logger.info(f"SPY 24h: {spy_change*100:+.2f}% -> score: {spy_score:+.2f}")

        # === 2. QQQ (Nasdaq) as additional equity signal (15% weight) ===
        qqq_change = changes.get("QQQ", {}).get("change_24h")
        if qqq_change is not None:
            if qqq_change <= -0.02:
                qqq_score = -0.9
            elif qqq_change <= -0.01:
                qqq_score = -0.5
            elif qqq_change >= 0.02:
                qqq_score = 0.9
            elif qqq_change >= 0.01:
                qqq_score = 0.5
            else:
                qqq_score = qqq_change * 30

            score += qqq_score * 0.15
            factors += 0.15
            self.logger.info(f"QQQ 24h: {qqq_change*100:+.2f}% -> score: {qqq_score:+.2f}")

        # === 3. VIX level (20% weight if available) ===
        vix = market_data.get("vix_level")
        if vix is not None:
            if vix > VIX_EXTREME_FEAR:
                vix_score = -0.9
            elif vix > VIX_FEAR_THRESHOLD:
                vix_score = -0.5
            elif vix < VIX_EXTREME_GREED:
                vix_score = 0.6
            elif vix < VIX_GREED_THRESHOLD:
                vix_score = 0.3
            else:
                # Normal range (15-25): slight negative bias
                vix_score = (20 - vix) / 20  # VIX 20 = 0, VIX 15 = 0.25, VIX 25 = -0.25

            score += vix_score * 0.2
            factors += 0.2
            self.logger.info(f"VIX: {vix:.1f} -> score: {vix_score:+.2f}")

        # === 4. Gold (GLD) - SPECIAL HANDLING for extreme moves (15% weight) ===
        gold_change = changes.get("GLD", {}).get("change_24h")
        if gold_change is not None:
            # IMPORTANT: A gold crash (-5% or more) is NOT "risk on"
            # It indicates panic selling, forced liquidation, or margin calls
            # This is BEARISH for overall market sentiment

            if gold_change <= -0.05:
                # Extreme gold crash = panic in markets = BEARISH
                gold_score = -0.7  # Bearish signal
                self.logger.warning(f"GOLD CRASH detected: {gold_change*100:.1f}%! This is bearish (panic selling)")
            elif gold_change <= -0.02:
                # Significant gold drop = uncertainty = slightly bearish
                gold_score = -0.3
            elif gold_change >= 0.02:
                # Gold rally = flight to safety = bearish for stocks
                gold_score = -0.4
            elif gold_change >= 0.01:
                # Gold up slightly = mild risk-off
                gold_score = -0.2
            elif gold_change <= -0.01:
                # Gold down slightly = mild risk-on
                gold_score = 0.2
            else:
                gold_score = 0

            score += gold_score * 0.15
            factors += 0.15
            self.logger.info(f"GLD 24h: {gold_change*100:+.2f}% -> score: {gold_score:+.2f}")

        if factors > 0:
            final_score = score / factors
            self.logger.info(f"Market data final score: {final_score:+.3f} (from {factors:.0%} of indicators)")
            return final_score
        return 0

    def _score_to_direction(self, score: float) -> tuple[str, float]:
        """Convert score to direction and confidence."""
        abs_score = abs(score)

        if score > 0.4:
            direction = MarketDirection.STRONG_BULLISH.value
            confidence = min(0.9, 0.6 + abs_score * 0.3)
        elif score > 0.15:
            direction = MarketDirection.BULLISH.value
            confidence = min(0.8, 0.5 + abs_score * 0.3)
        elif score < -0.4:
            direction = MarketDirection.STRONG_BEARISH.value
            confidence = min(0.9, 0.6 + abs_score * 0.3)
        elif score < -0.15:
            direction = MarketDirection.BEARISH.value
            confidence = min(0.8, 0.5 + abs_score * 0.3)
        else:
            direction = MarketDirection.NEUTRAL.value
            confidence = 0.5 - abs_score  # Less confident when near neutral

        return direction, round(confidence, 2)

    def _find_primary_driver(
        self,
        source_scores: dict[str, dict],
        market_score: Optional[float],
    ) -> str:
        """Identify what's driving the direction."""
        if market_score is not None and abs(market_score) > 0.3:
            return "actual_market_data"

        # Find source with highest absolute weighted contribution
        max_contrib = 0
        primary = "unknown"

        for source, data in source_scores.items():
            weight = SIGNAL_WEIGHTS.get(source, SIGNAL_WEIGHTS["default"])
            contrib = abs(data["score"]) * weight
            if contrib > max_contrib:
                max_contrib = contrib
                primary = source

        return primary

    def _generate_explanation(
        self,
        direction: str,
        score: float,
        components: dict[str, float],
        primary_driver: str,
    ) -> str:
        """Generate human-readable explanation."""
        direction_text = {
            "strong_bullish": "强烈看涨",
            "bullish": "偏多",
            "neutral": "中性",
            "bearish": "偏空",
            "strong_bearish": "强烈看跌",
        }.get(direction, direction)

        driver_text = {
            "actual_market_data": "实际市场数据（价格变动）",
            "cross_asset": "跨资产联动",
            "macro_auditor": "宏观经济指标",
            "news_impact": "新闻情绪",
        }.get(primary_driver, primary_driver)

        explanation = f"市场方向: {direction_text} (评分: {score:+.2f})\n"
        explanation += f"主要驱动: {driver_text}\n"

        # Add component breakdown
        if components:
            explanation += "各因素贡献:\n"
            for name, value in sorted(components.items(), key=lambda x: abs(x[1]), reverse=True):
                if abs(value) > 0.01:
                    explanation += f"  - {name}: {value:+.2f}\n"

        return explanation


# Singleton
_calculator: Optional[MarketDirectionCalculator] = None


def get_market_direction_calculator() -> MarketDirectionCalculator:
    """Get the global market direction calculator."""
    global _calculator
    if _calculator is None:
        _calculator = MarketDirectionCalculator()
    return _calculator


def calculate_market_direction(
    signals: list[dict[str, Any]],
    market_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Convenience function to calculate market direction.

    Returns dict for easy JSON serialization.
    """
    calc = get_market_direction_calculator()
    result = calc.calculate_direction(signals, market_data)

    return {
        "direction": result.direction,
        "score": result.score,
        "confidence": result.confidence,
        "components": result.components,
        "primary_driver": result.primary_driver,
        "explanation": result.explanation,
    }
