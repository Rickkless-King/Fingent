"""
Domain module - Business models

Contains pure business logic models without external dependencies.
All models are JSON-serializable for LangGraph state compatibility.
"""

from fingent.domain.models import (
    MacroIndicator,
    PriceBar,
    NewsItem,
    MarketData,
)
from fingent.domain.signals import (
    SignalDirection,
    SignalName,
    Signal,
    create_signal,
)
from fingent.domain.alerts import (
    AlertSeverity,
    Alert,
    create_alert,
)
from fingent.domain.report import (
    ReportSection,
    Report,
    create_report,
)

__all__ = [
    # Models
    "MacroIndicator",
    "PriceBar",
    "NewsItem",
    "MarketData",
    # Signals
    "SignalDirection",
    "SignalName",
    "Signal",
    "create_signal",
    # Alerts
    "AlertSeverity",
    "Alert",
    "create_alert",
    # Reports
    "ReportSection",
    "Report",
    "create_report",
]
