"""
GraphState definition for LangGraph workflow.

The state is the "memory" that flows through all nodes.
MUST be JSON-serializable (TypedDict + dict/list only).
"""

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    """
    LangGraph workflow state.

    All fields are optional (total=False) to support partial updates.
    Each node returns only the fields it wants to update.

    IMPORTANT: All values must be JSON-serializable.
    Use dict/list instead of custom classes.
    """

    # ==========================================
    # Run Metadata
    # ==========================================
    run_id: str         # Unique identifier for this run
    asof: str           # Analysis timestamp (ISO format)
    timezone: str       # Configured timezone

    # ==========================================
    # Raw Data from Providers
    # ==========================================

    # Macro data from FRED
    macro_data: dict[str, Any]
    # {
    #     "timestamp": "...",
    #     "indicators": {"FEDFUNDS": {...}, "DGS10": {...}},
    #     "yield_spread": 0.5,
    #     "inflation": {"cpi_yoy": 3.2, "core_cpi_yoy": 3.0}
    # }

    # Market data from Finnhub/OKX
    market_data: dict[str, Any]
    # {
    #     "timestamp": "...",
    #     "assets": {"SPY": {...}, "BTC-USDT": {...}},
    #     "changes": {"SPY": {"change_24h": 0.01, "change_7d": 0.02}},
    #     "vix_level": 18.5
    # }

    # News data from AlphaVantage/Finnhub
    news_data: dict[str, Any]
    # {
    #     "timestamp": "...",
    #     "articles": [{...}, {...}],
    #     "summary": {"article_count": 50, "avg_sentiment": 0.1},
    #     "source": "alphavantage"
    # }

    # Sentiment data from Polymarket (optional)
    sentiment_data: dict[str, Any]
    # {
    #     "available": true,
    #     "markets": [{...}],
    #     "fed_probability": 0.7
    # }

    # ==========================================
    # Signals (Standardized Node Outputs)
    # ==========================================

    signals: list[dict[str, Any]]
    # Each signal:
    # {
    #     "id": "macro_auditor_hawkish_bias_run_xxx",
    #     "name": "hawkish_bias",
    #     "direction": "hawkish",
    #     "score": 0.7,
    #     "confidence": 0.8,
    #     "source_node": "macro_auditor",
    #     "evidence": {...},
    #     "timestamp": "..."
    # }

    # ==========================================
    # Outputs
    # ==========================================

    # Alerts (rule-based, not LLM)
    alerts: list[dict[str, Any]]
    # Each alert:
    # {
    #     "id": "alert_btc_crash_run_xxx",
    #     "rule_name": "btc_crash",
    #     "title": "BTC 24h 大跌",
    #     "message": "...",
    #     "severity": "high",
    #     "current_value": -0.1,
    #     "threshold": -0.08
    # }

    # Final report
    report: dict[str, Any]
    # {
    #     "id": "report_run_xxx",
    #     "title": "...",
    #     "summary": "...",
    #     "sections": [...],
    #     "signals_summary": {...},
    #     "alerts": [...],
    #     "market_snapshot": {...}
    # }

    # ==========================================
    # Error Tracking
    # ==========================================

    errors: list[dict[str, Any]]
    # Each error:
    # {
    #     "node": "macro_auditor",
    #     "error": "FRED API timeout",
    #     "timestamp": "...",
    #     "recoverable": true
    # }


def create_initial_state() -> GraphState:
    """
    Create an empty initial state.

    Use this as the starting point for workflow execution.
    """
    return GraphState(
        run_id="",
        asof="",
        timezone="America/New_York",
        macro_data={},
        market_data={},
        news_data={},
        sentiment_data={},
        signals=[],
        alerts=[],
        report={},
        errors=[],
    )


def merge_state(current: GraphState, update: dict[str, Any]) -> GraphState:
    """
    Merge partial state update into current state.

    Special handling for list fields (signals, alerts, errors):
    - Lists are extended, not replaced
    - Deduplication by 'id' field if present

    Args:
        current: Current state
        update: Partial update from a node

    Returns:
        Merged state
    """
    result = dict(current)

    for key, value in update.items():
        if key in ["signals", "alerts", "errors"]:
            # Extend list fields
            existing = result.get(key, [])
            if isinstance(value, list):
                # Deduplicate by id if present
                existing_ids = {
                    item.get("id") for item in existing if isinstance(item, dict)
                }
                for item in value:
                    if isinstance(item, dict):
                        if item.get("id") not in existing_ids:
                            existing.append(item)
                            existing_ids.add(item.get("id"))
                    else:
                        existing.append(item)
                result[key] = existing
            else:
                result[key] = value
        else:
            # Replace other fields
            result[key] = value

    return GraphState(**result)
