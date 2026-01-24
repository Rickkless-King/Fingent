"""
Synthesize & Alert node - Final node that aggregates signals and produces alerts/reports.

Responsibilities:
- Aggregate all signals from previous nodes
- Evaluate alert rules (rule-based, NOT LLM)
- Generate report (optionally with LLM for human-readable summary)
"""

from typing import Any, Optional

from fingent.core.config import load_yaml_config
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.alerts import AlertRuleEngine, AlertSeverity, create_alert
from fingent.domain.report import create_report
from fingent.domain.signals import aggregate_signals
from fingent.nodes.base import BaseNode


class SynthesizeAlertNode(BaseNode):
    """
    Final synthesis and alerting node.

    This node:
    1. Aggregates all signals from previous nodes
    2. Evaluates rule-based alerts (from config.yaml)
    3. Generates the final report
    4. Optionally uses LLM for human-readable summary

    IMPORTANT: Alert decisions are ALWAYS rule-based, never LLM-generated.
    """

    node_name = "synthesize_alert"

    def __init__(self, *args, llm_service=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm = llm_service  # Optional, for report generation

        # Initialize rule engine with config
        alert_rules = self.config.get("alert_rules", [])
        self.rule_engine = AlertRuleEngine(alert_rules)

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Synthesize signals and generate alerts/report.

        Returns:
            State update with alerts and report
        """
        run_id = self.get_run_id(state)
        errors = []

        # Get all signals from state
        signals = state.get("signals", [])
        self.logger.info(f"Synthesizing {len(signals)} signals")

        # Aggregate signals
        signals_summary = aggregate_signals(signals)

        # Extract metrics for alert evaluation
        metrics = self._extract_metrics(state)

        # Evaluate alert rules (rule-based, NOT LLM)
        alerts = self.rule_engine.evaluate(metrics, run_id)
        self.logger.info(f"Generated {len(alerts)} alerts")

        # Generate report
        report = self._generate_report(
            state=state,
            signals_summary=signals_summary,
            alerts=alerts,
            run_id=run_id,
            errors=errors,
        )

        return {
            "alerts": alerts,
            "report": report,
            "errors": state.get("errors", []) + errors,
        }

    def _extract_metrics(self, state: dict[str, Any]) -> dict[str, float]:
        """
        Extract metrics from state for alert rule evaluation.

        This maps state data to the metric names used in alert rules.
        """
        metrics = {}

        # Market data metrics
        market_data = state.get("market_data", {})
        changes = market_data.get("changes", {})

        # BTC 24h change
        btc_change = changes.get("BTC-USDT", {}).get("change_24h")
        if btc_change is not None:
            metrics["btc_24h_change"] = btc_change

        # Gold 24h change
        gold_change = changes.get("GLD", {}).get("change_24h")
        if gold_change is not None:
            metrics["gold_24h_change"] = gold_change

        # VIX level
        vix_level = market_data.get("vix_level")
        if vix_level is not None:
            metrics["vix_level"] = vix_level

        # Macro data metrics
        macro_data = state.get("macro_data", {})

        # Yield spread
        yield_spread = macro_data.get("yield_spread")
        if yield_spread is not None:
            metrics["yield_spread_2y10y"] = yield_spread

        # Hawkish score (from signals)
        signals = state.get("signals", [])
        hawkish_signals = [
            s for s in signals
            if s.get("name") in ["hawkish_bias", "inflation_rising"]
        ]
        if hawkish_signals:
            metrics["hawkish_score"] = sum(
                s.get("score", 0) for s in hawkish_signals
            )

        self.logger.debug(f"Extracted metrics: {metrics}")
        return metrics

    def _generate_report(
        self,
        state: dict[str, Any],
        signals_summary: dict[str, Any],
        alerts: list[dict[str, Any]],
        run_id: str,
        errors: list,
    ) -> dict[str, Any]:
        """Generate the final report."""

        # Build sections from state data
        sections = self._build_sections(state)

        # Build market snapshot
        market_snapshot = self._build_market_snapshot(state)

        # Generate summary
        summary = self._generate_summary(
            signals_summary=signals_summary,
            alerts=alerts,
            sections=sections,
            errors=errors,
        )

        # Create report
        report = create_report(
            run_id=run_id,
            summary=summary,
            sections=sections,
            signals_summary=signals_summary,
            alerts=alerts,
            market_snapshot=market_snapshot,
            llm_used=self.llm is not None and summary != "",
        )

        return report

    def _build_sections(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Build report sections from state data."""
        sections = []

        # Macro section
        macro_data = state.get("macro_data", {})
        if macro_data.get("indicators"):
            indicators = macro_data.get("indicators", {})
            inflation = macro_data.get("inflation", {})

            key_points = []
            if "FEDFUNDS" in indicators:
                rate = indicators["FEDFUNDS"].get("value")
                key_points.append(f"联邦基金利率: {rate}%")
            if inflation.get("cpi_yoy"):
                key_points.append(f"CPI 同比: {inflation['cpi_yoy']}%")
            if macro_data.get("yield_spread"):
                spread = macro_data["yield_spread"]
                key_points.append(f"2Y-10Y 利差: {spread:.2f}%")

            sections.append({
                "title": "宏观经济",
                "content": "美国宏观经济指标分析",
                "section_type": "macro",
                "source_node": "macro_auditor",
                "key_points": key_points,
                "data": macro_data,
            })

        # Market section
        market_data = state.get("market_data", {})
        if market_data.get("assets"):
            assets = market_data.get("assets", {})
            changes = market_data.get("changes", {})

            key_points = []
            for symbol in ["SPY", "BTC-USDT", "GLD"]:
                if symbol in assets:
                    price = assets[symbol].get("price")
                    change = changes.get(symbol, {}).get("change_24h")
                    if price and change is not None:
                        key_points.append(
                            f"{symbol}: ${price:.2f} ({change*100:+.1f}%)"
                        )

            if market_data.get("vix_level"):
                key_points.append(f"VIX: {market_data['vix_level']:.1f}")

            sections.append({
                "title": "跨资产分析",
                "content": "股票、加密货币、避险资产联动分析",
                "section_type": "cross_asset",
                "source_node": "cross_asset",
                "key_points": key_points,
                "data": {"assets": list(assets.keys())},
            })

        # News section
        news_data = state.get("news_data", {})
        if news_data.get("articles"):
            summary = news_data.get("summary", {})

            key_points = [
                f"分析文章数: {summary.get('article_count', 0)}",
                f"平均情绪: {summary.get('avg_sentiment', 0):.2f}",
            ]

            sections.append({
                "title": "新闻情绪",
                "content": "市场新闻情绪分析",
                "section_type": "sentiment",
                "source_node": "news_impact",
                "key_points": key_points,
                "data": summary,
            })

        return sections

    def _build_market_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build a quick market snapshot for the report."""
        snapshot = {
            "timestamp": state.get("asof", format_timestamp(now_utc())),
        }

        market_data = state.get("market_data", {})
        assets = market_data.get("assets", {})

        # Key prices
        for symbol in ["SPY", "QQQ", "BTC-USDT", "GLD", "VIX"]:
            if symbol in assets:
                snapshot[symbol] = {
                    "price": assets[symbol].get("price"),
                    "change_24h": assets[symbol].get("change_24h"),
                }

        return snapshot

    def _generate_summary(
        self,
        signals_summary: dict[str, Any],
        alerts: list[dict[str, Any]],
        sections: list[dict[str, Any]],
        errors: list,
    ) -> str:
        """
        Generate executive summary.

        If LLM is available, use it for natural language summary.
        Otherwise, generate structured text summary.
        """
        # TODO: Integrate LLM for better summaries
        # For now, generate structured summary

        direction = signals_summary.get("overall_direction", "neutral")
        score = signals_summary.get("overall_score", 0)
        signal_count = signals_summary.get("signal_count", 0)

        lines = []

        # Overall direction
        direction_text = {
            "bullish": "整体偏多",
            "bearish": "整体偏空",
            "neutral": "整体中性",
            "hawkish": "Fed 偏鹰",
            "dovish": "Fed 偏鸽",
        }
        lines.append(f"**市场判断**: {direction_text.get(direction, direction)} (评分: {score:+.2f})")
        lines.append(f"**信号数量**: {signal_count} 个")
        lines.append("")

        # Key signals
        key_signals = signals_summary.get("key_signals", [])
        if key_signals:
            lines.append("**关键信号**:")
            for sig in key_signals[:3]:
                name = sig.get("name", "unknown")
                sig_direction = sig.get("direction", "neutral")
                sig_score = sig.get("score", 0)
                lines.append(f"- {name}: {sig_direction} ({sig_score:+.2f})")
            lines.append("")

        # Alerts
        if alerts:
            lines.append(f"**告警**: {len(alerts)} 条")
            for alert in alerts[:2]:
                lines.append(f"- {alert.get('title', 'Alert')}")
            lines.append("")

        # Errors
        if errors:
            lines.append(f"**注意**: {len(errors)} 个数据源异常")

        return "\n".join(lines)
