"""
Alert definitions for Fingent.

Alerts are rule-based notifications triggered by market conditions.
They are NOT produced by LLM - only by the rule engine.
"""

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any, Optional

from fingent.core.timeutil import format_timestamp, now_utc


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Alert:
    """
    An alert triggered by rule-based conditions.

    Alerts are:
    - Deterministic (same input = same output)
    - Rule-based (defined in config.yaml)
    - Independent of LLM

    Attributes:
        id: Unique alert ID
        rule_name: Name of the rule that triggered this alert
        title: Short title for display
        message: Detailed message
        severity: Alert severity level
        triggered_at: Timestamp when alert was triggered
        condition: The condition that was met
        current_value: The value that triggered the alert
        threshold: The threshold that was exceeded
        metadata: Additional context
    """
    id: str
    rule_name: str
    title: str
    message: str
    severity: str = AlertSeverity.MEDIUM.value
    triggered_at: str = ""
    condition: dict[str, Any] = field(default_factory=dict)
    current_value: Optional[float] = None
    threshold: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.triggered_at:
            self.triggered_at = format_timestamp(now_utc())

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alert":
        """Create Alert from dict."""
        return cls(**data)

    def to_telegram_message(self) -> str:
        """Format alert for Telegram notification."""
        severity_emoji = {
            AlertSeverity.LOW.value: "📢",
            AlertSeverity.MEDIUM.value: "⚠️",
            AlertSeverity.HIGH.value: "🚨",
            AlertSeverity.CRITICAL.value: "🔴",
        }
        emoji = severity_emoji.get(self.severity, "📢")

        lines = [
            f"{emoji} *{self.title}*",
            "",
            self.message,
            "",
            f"📊 当前值: `{self.current_value}`",
            f"📏 阈值: `{self.threshold}`",
            f"⏰ 时间: {self.triggered_at}",
        ]
        return "\n".join(lines)


def create_alert(
    rule_name: str,
    title: str,
    message: str,
    *,
    severity: str = AlertSeverity.MEDIUM.value,
    condition: Optional[dict[str, Any]] = None,
    current_value: Optional[float] = None,
    threshold: Optional[float] = None,
    metadata: Optional[dict[str, Any]] = None,
    run_id: str = "",
) -> dict[str, Any]:
    """
    Factory function to create an alert dict.

    Returns dict directly for easy insertion into GraphState.

    Args:
        rule_name: Name of the rule (from config.yaml)
        title: Short display title
        message: Detailed message
        severity: Alert severity
        condition: Condition that was met
        current_value: Value that triggered the alert
        threshold: Threshold that was exceeded
        metadata: Additional context
        run_id: Current run ID

    Returns:
        Alert as dict (JSON-serializable)

    Example:
        alert = create_alert(
            rule_name="btc_crash",
            title="BTC 24h 大跌",
            message="BTC 24小时跌幅达到 -10.5%，超过 -8% 阈值",
            severity=AlertSeverity.HIGH.value,
            current_value=-0.105,
            threshold=-0.08,
            run_id="run_20260124_070000"
        )
    """
    alert_id = f"alert_{rule_name}_{run_id}"

    return {
        "id": alert_id,
        "rule_name": rule_name,
        "title": title,
        "message": message,
        "severity": severity,
        "triggered_at": format_timestamp(now_utc()),
        "condition": condition or {},
        "current_value": current_value,
        "threshold": threshold,
        "metadata": metadata or {},
    }


class AlertRuleEngine:
    """
    Simple rule-based alert engine.

    Evaluates conditions from config and produces alerts.
    This is NOT an LLM - it's deterministic rule matching.
    """

    OPERATORS = {
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    def __init__(self, rules: list[dict[str, Any]]):
        """
        Initialize with rules from config.

        Args:
            rules: List of rule definitions from config.yaml
        """
        self.rules = rules

    def evaluate(
        self,
        metrics: dict[str, float],
        run_id: str = "",
    ) -> list[dict[str, Any]]:
        """
        Evaluate all rules against current metrics.

        Args:
            metrics: Dict of metric_name -> value
            run_id: Current run ID

        Returns:
            List of triggered alerts
        """
        alerts = []

        for rule in self.rules:
            alert = self._evaluate_rule(rule, metrics, run_id)
            if alert:
                alerts.append(alert)

        return alerts

    def _evaluate_rule(
        self,
        rule: dict[str, Any],
        metrics: dict[str, float],
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Evaluate a single rule."""
        condition = rule.get("condition", {})
        metric_name = condition.get("metric")
        operator = condition.get("operator")
        threshold = condition.get("threshold")

        if not all([metric_name, operator, threshold is not None]):
            return None

        current_value = metrics.get(metric_name)
        if current_value is None:
            return None

        op_func = self.OPERATORS.get(operator)
        if op_func is None:
            return None

        if op_func(current_value, threshold):
            return create_alert(
                rule_name=rule.get("name", "unknown"),
                title=rule.get("description", rule.get("name", "Alert")),
                message=f"{metric_name} = {current_value:.4f} {operator} {threshold}",
                severity=rule.get("severity", AlertSeverity.MEDIUM.value),
                condition=condition,
                current_value=current_value,
                threshold=threshold,
                run_id=run_id,
            )

        return None
