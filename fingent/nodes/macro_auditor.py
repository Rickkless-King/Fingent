"""
Macro Auditor node - Analyzes macroeconomic indicators.

Data sources: FRED
Signals produced: hawkish_bias, dovish_bias, inflation_rising, etc.
"""

from typing import Any, Optional

from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.signals import SignalDirection, SignalName, create_signal
from fingent.nodes.base import BaseNode
from fingent.providers.fred import FREDProvider


class MacroAuditorNode(BaseNode):
    """
    Macro economic auditor node.

    Analyzes FRED data to produce macro signals:
    - Fed policy stance (hawkish/dovish)
    - Inflation trends
    - Labor market conditions
    - Yield curve status
    """

    node_name = "macro_auditor"

    def __init__(self, *args, fred_provider: Optional[FREDProvider] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fred = fred_provider or FREDProvider()

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Analyze macro indicators and produce signals.

        Returns:
            State update with macro_data and signals
        """
        run_id = self.get_run_id(state)
        existing_signals = self.get_existing_signals(state)
        errors = []
        signals = []

        # Fetch macro data
        macro_data = self._fetch_macro_data(errors)

        # Analyze and produce signals
        if macro_data:
            signals = self._analyze_macro(macro_data, run_id)

        # Merge signals
        all_signals = self.merge_signals(existing_signals, signals)

        return {
            "macro_data": macro_data,
            "signals": all_signals,
            "errors": state.get("errors", []) + errors,
        }

    def _fetch_macro_data(self, errors: list) -> dict[str, Any]:
        """Fetch macro data from FRED."""
        macro_data = {
            "timestamp": format_timestamp(now_utc()),
            "indicators": {},
            "yield_spread": None,
            "inflation": {},
        }

        try:
            # Get key indicators
            indicators = self.fred.get_macro_snapshot()
            macro_data["indicators"] = {
                k: v.to_dict() for k, v in indicators.items()
            }

            # Get yield spread (2Y-10Y)
            yield_spread = self.fred.get_yield_spread()
            macro_data["yield_spread"] = yield_spread

            # Get inflation metrics
            inflation = self.fred.get_inflation_metrics()
            macro_data["inflation"] = inflation

            self.logger.info(f"Fetched {len(indicators)} macro indicators")

        except Exception as e:
            self.logger.error(f"Failed to fetch macro data: {e}")
            errors.append(self.create_error(f"FRED fetch failed: {e}"))

        return macro_data

    def _analyze_macro(
        self,
        macro_data: dict[str, Any],
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Analyze macro data and produce signals."""
        signals = []
        indicators = macro_data.get("indicators", {})
        inflation = macro_data.get("inflation", {})
        yield_spread = macro_data.get("yield_spread")

        # === Fed Policy Stance ===
        fed_signal = self._analyze_fed_stance(indicators, inflation, run_id)
        if fed_signal:
            signals.append(fed_signal)

        # === Inflation Trend ===
        inflation_signal = self._analyze_inflation(inflation, run_id)
        if inflation_signal:
            signals.append(inflation_signal)

        # === Yield Curve ===
        if yield_spread is not None:
            yield_signal = self._analyze_yield_curve(yield_spread, run_id)
            if yield_signal:
                signals.append(yield_signal)

        # === Labor Market ===
        labor_signal = self._analyze_labor(indicators, run_id)
        if labor_signal:
            signals.append(labor_signal)

        return signals

    def _analyze_fed_stance(
        self,
        indicators: dict,
        inflation: dict,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze Fed policy stance."""
        hawkish_score = 0
        evidence = {}

        # Fed funds rate level - 调整阈值使其更敏感
        fed_funds = indicators.get("FEDFUNDS", {})
        if fed_funds:
            rate = fed_funds.get("value", 0)
            evidence["fed_funds_rate"] = rate
            # 更细致的利率评估
            if rate >= 5.0:
                hawkish_score += 1.5
            elif rate >= 4.0:
                hawkish_score += 1
            elif rate >= 3.0:
                hawkish_score += 0.5  # 中等偏紧
            elif rate <= 1.5:
                hawkish_score -= 1.5
            elif rate <= 2.5:
                hawkish_score -= 0.5  # 中等偏松

        # CPI level
        cpi_yoy = inflation.get("cpi_yoy")
        if cpi_yoy is not None:
            evidence["cpi_yoy"] = cpi_yoy
            if cpi_yoy > 4.0:
                hawkish_score += 1.5
            elif cpi_yoy > 3.0:
                hawkish_score += 1
            elif cpi_yoy > 2.5:
                hawkish_score += 0.5  # 通胀略高于目标
            elif cpi_yoy < 1.5:
                hawkish_score -= 1
            elif cpi_yoy < 2.0:
                hawkish_score -= 0.5  # 通胀略低于目标

        # 即使分数较低也产出信号，提供更多信息
        if hawkish_score >= 0.5:
            return create_signal(
                name=SignalName.HAWKISH_BIAS.value,
                direction=SignalDirection.HAWKISH.value,
                score=min(hawkish_score * 0.3, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5 + min(abs(hawkish_score) * 0.1, 0.3),
                evidence=evidence,
            )
        elif hawkish_score <= -0.5:
            return create_signal(
                name=SignalName.DOVISH_BIAS.value,
                direction=SignalDirection.DOVISH.value,
                score=max(hawkish_score * 0.3, -1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5 + min(abs(hawkish_score) * 0.1, 0.3),
                evidence=evidence,
            )
        else:
            # 中性信号 - 政策立场不明确
            return create_signal(
                name="fed_neutral",
                direction=SignalDirection.NEUTRAL.value,
                score=hawkish_score * 0.2,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.4,
                evidence=evidence,
            )

    def _analyze_inflation(
        self,
        inflation: dict,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze inflation trends."""
        cpi_yoy = inflation.get("cpi_yoy")
        core_cpi_yoy = inflation.get("core_cpi_yoy")

        if cpi_yoy is None:
            return None

        evidence = {"cpi_yoy": cpi_yoy}
        if core_cpi_yoy:
            evidence["core_cpi_yoy"] = core_cpi_yoy

        # Fed 目标是 2%，根据偏离程度给出信号
        target = 2.0
        deviation = cpi_yoy - target

        if cpi_yoy > 3.5:
            # 高通胀
            return create_signal(
                name=SignalName.INFLATION_RISING.value,
                direction=SignalDirection.BEARISH.value,
                score=min((cpi_yoy - 2.0) / 3.0, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.8,
                evidence=evidence,
            )
        elif cpi_yoy > 2.5:
            # 通胀略高于目标
            return create_signal(
                name=SignalName.INFLATION_RISING.value,
                direction=SignalDirection.BEARISH.value,
                score=min(deviation / 2.0, 0.5),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.6,
                evidence=evidence,
            )
        elif cpi_yoy < 1.5:
            # 通胀过低
            return create_signal(
                name=SignalName.INFLATION_COOLING.value,
                direction=SignalDirection.BULLISH.value,
                score=max((2.0 - cpi_yoy) / 2.0, 0.3),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.8,
                evidence=evidence,
            )
        elif cpi_yoy < 2.0:
            # 通胀略低于目标
            return create_signal(
                name=SignalName.INFLATION_COOLING.value,
                direction=SignalDirection.BULLISH.value,
                score=max(abs(deviation) / 1.0, 0.2),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5,
                evidence=evidence,
            )
        else:
            # 通胀接近目标 (2.0-2.5%)
            return create_signal(
                name="inflation_stable",
                direction=SignalDirection.NEUTRAL.value,
                score=deviation * 0.2,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.6,
                evidence=evidence,
            )

    def _analyze_yield_curve(
        self,
        yield_spread: float,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze yield curve for inversion signals."""
        evidence = {"yield_spread_2y10y": yield_spread}

        if yield_spread < -0.2:
            # Inverted yield curve
            return create_signal(
                name=SignalName.YIELD_CURVE_INVERSION.value,
                direction=SignalDirection.BEARISH.value,
                score=min(abs(yield_spread) / 0.5, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.9,
                evidence=evidence,
            )

        return None

    def _analyze_labor(
        self,
        indicators: dict,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze labor market conditions."""
        unrate = indicators.get("UNRATE", {})

        if not unrate:
            return None

        unemployment = unrate.get("value")
        if unemployment is None:
            return None

        evidence = {"unemployment_rate": unemployment}

        # 自然失业率约为 4-5%
        if unemployment < 4.0:
            # 劳动力市场紧张
            return create_signal(
                name=SignalName.LABOR_STRONG.value,
                direction=SignalDirection.BULLISH.value,
                score=max((4.0 - unemployment) / 2.0, 0.3),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.7,
                evidence=evidence,
            )
        elif unemployment < 4.5:
            # 劳动力市场健康
            return create_signal(
                name=SignalName.LABOR_STRONG.value,
                direction=SignalDirection.BULLISH.value,
                score=0.3,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.6,
                evidence=evidence,
            )
        elif unemployment > 5.5:
            # 劳动力市场疲软
            return create_signal(
                name=SignalName.LABOR_WEAK.value,
                direction=SignalDirection.BEARISH.value,
                score=min((unemployment - 4.0) / 3.0, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.7,
                evidence=evidence,
            )
        elif unemployment > 5.0:
            # 劳动力市场有压力
            return create_signal(
                name=SignalName.LABOR_WEAK.value,
                direction=SignalDirection.BEARISH.value,
                score=0.3,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5,
                evidence=evidence,
            )
        else:
            # 劳动力市场正常 (4.5-5.0%)
            return create_signal(
                name="labor_neutral",
                direction=SignalDirection.NEUTRAL.value,
                score=0,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5,
                evidence=evidence,
            )
