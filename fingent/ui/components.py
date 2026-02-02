"""
Reusable UI components for Fingent Streamlit dashboard.

Keep components focused, stateless where possible, and well-typed.
"""

import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timedelta
from typing import Any, Optional


# =============================================================================
# Formatters
# =============================================================================

def format_delta(value: float) -> str:
    """Format delta as percentage with sign."""
    return f"{value:+.2%}"


def format_price(value: float) -> str:
    """Format price as percentage."""
    return f"{value:.2%}"


def format_currency(value: float) -> str:
    """Format as USD currency."""
    if value >= 1_000_000:
        return f"${value/1_000_000:.1f}M"
    elif value >= 1_000:
        return f"${value/1_000:.1f}K"
    return f"${value:,.0f}"


def format_time_ago(published_at: str) -> str:
    """Format timestamp as relative time (e.g., '5m', '2h', '01/15')."""
    if not published_at:
        return ""
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        diff = now - dt
        if diff < timedelta(hours=1):
            return f"{int(diff.total_seconds() / 60)}m"
        elif diff < timedelta(days=1):
            return f"{int(diff.total_seconds() / 3600)}h"
        return dt.strftime("%m/%d")
    except Exception:
        return published_at[:10] if len(published_at) > 10 else published_at


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate text with ellipsis."""
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


# =============================================================================
# KPI Cards
# =============================================================================

def render_kpi_row(metrics: list[tuple[str, Any, Optional[str]]]):
    """
    Render a row of KPI metrics.

    Args:
        metrics: List of (label, value, delta) tuples
    """
    cols = st.columns(len(metrics))
    for col, (label, value, delta) in zip(cols, metrics):
        with col:
            st.metric(label, value, delta)


def render_shock_kpis(results: dict):
    """Render KPI cards for shock scan results."""
    deltas = results.get("all_deltas", [])
    max_delta = max((abs(d.get("delta", 0) or 0) for d in deltas), default=0)

    metrics = [
        ("Events", results.get("events_scanned", 0), None),
        ("Markets", results.get("markets_scanned", 0), None),
        ("Shocks", results.get("shocks_found", 0), None),
        ("Max Δ", format_delta(max_delta) if max_delta else "N/A", None),
    ]
    render_kpi_row(metrics)


# =============================================================================
# Tables
# =============================================================================

def render_shock_table(shocks: list[dict]):
    """Render shocks as a clean data table."""
    if not shocks:
        st.info("No shocks detected.")
        return

    rows = []
    for s in shocks:
        rows.append({
            "Δ": format_delta(s.get("delta", 0)),
            "Prob": format_price(s.get("current_mid", 0)),
            "Vol 24h": format_currency(s.get("volume_24h", 0) or 0),
            "Spread": f"{s.get('spread_bps', 0):.0f}bps",
            "Question": truncate(s.get("question", ""), 60),
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


def render_movers_table(deltas: list[dict], limit: int = 10, sort_by: str = "delta"):
    """
    Render top movers table.

    Args:
        deltas: List of delta dictionaries
        limit: Max rows to show
        sort_by: 'delta' for largest moves, 'volume' for most liquid
    """
    if not deltas:
        st.info("No data available.")
        return

    if sort_by == "volume":
        sorted_data = sorted(deltas, key=lambda x: x.get("volume_24h", 0) or 0, reverse=True)
    else:
        sorted_data = sorted(deltas, key=lambda x: abs(x.get("delta", 0)), reverse=True)

    rows = []
    for d in sorted_data[:limit]:
        rows.append({
            "Δ": format_delta(d.get("delta", 0)),
            "Prob": format_price(d.get("current_mid", 0)),
            "Vol 24h": format_currency(d.get("volume_24h", 0) or 0),
            "Question": truncate(d.get("question", ""), 50),
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


# =============================================================================
# Charts
# =============================================================================

def render_delta_scatter(items: list[dict], height: int = 300):
    """Render delta scatter chart with volume as bubble size."""
    if not items:
        st.info("No data to plot.")
        return

    df = pd.DataFrame(items)

    # Ensure required columns exist
    if "delta" not in df.columns:
        st.warning("Missing delta data.")
        return

    df["volume_24h"] = pd.to_numeric(df.get("volume_24h", 0), errors="coerce").fillna(0)
    df["current_mid"] = pd.to_numeric(df.get("current_mid", 0.5), errors="coerce").fillna(0.5)

    chart = (
        alt.Chart(df)
        .mark_circle(opacity=0.7)
        .encode(
            x=alt.X("current_mid:Q", title="Probability", scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("delta:Q", title="Delta (Δ)"),
            size=alt.Size("volume_24h:Q", title="Volume", scale=alt.Scale(range=[20, 500])),
            color=alt.condition(
                alt.datum.delta > 0,
                alt.value("#22c55e"),  # green for positive
                alt.value("#ef4444"),  # red for negative
            ),
            tooltip=["question", "delta", "current_mid", "volume_24h"],
        )
        .properties(height=height)
    )

    st.altair_chart(chart, use_container_width=True)


def render_price_history(history: list[dict], height: int = 200):
    """Render price history line chart."""
    if not history or len(history) < 2:
        st.caption("Insufficient history data.")
        return

    df = pd.DataFrame(history)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    chart = (
        alt.Chart(df)
        .mark_line(color="#3b82f6")
        .encode(
            x=alt.X("timestamp:T", title="Time"),
            y=alt.Y("mid:Q", title="Probability", scale=alt.Scale(domain=[0, 1])),
        )
        .properties(height=height)
    )

    st.altair_chart(chart, use_container_width=True)


# =============================================================================
# News Components
# =============================================================================

def render_news_list(articles: list[dict], limit: int = 10):
    """Render compact news list with sentiment indicators."""
    if not articles:
        st.info("No news available.")
        return

    for article in articles[:limit]:
        title = article.get("title", "Untitled")
        source = article.get("source", "Unknown")
        published_at = article.get("published_at", "")
        sentiment = article.get("sentiment_score") or 0
        url = article.get("url", "")

        # Sentiment indicator
        if sentiment > 0.3:
            icon = "🟢"
        elif sentiment < -0.3:
            icon = "🔴"
        else:
            icon = "⚪"

        time_str = format_time_ago(published_at)
        header = f"{icon} {truncate(title, 55)} ({time_str})"

        with st.expander(header, expanded=False):
            st.caption(f"Source: {source} | Sentiment: {sentiment:+.2f}")
            if article.get("summary"):
                st.markdown(truncate(article["summary"], 200))
            if url:
                st.markdown(f"[Read more]({url})")


# =============================================================================
# Market Data Components
# =============================================================================

def render_market_metrics(quotes: dict):
    """Render market quotes as metric cards."""
    if not quotes:
        st.info("No market data.")
        return

    symbols = list(quotes.keys())

    for i in range(0, len(symbols), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i + j < len(symbols):
                symbol = symbols[i + j]
                q = quotes[symbol]
                price = q.get("price", 0)
                change = q.get("change_24h", 0) or 0

                with col:
                    st.metric(
                        symbol,
                        f"${price:,.2f}" if price else "N/A",
                        f"{change:+.2%}" if change else None,
                        delta_color="normal" if change >= 0 else "inverse",
                    )


# =============================================================================
# Arbitrage Components
# =============================================================================

def render_arb_opportunity(opp: dict, index: int):
    """Render a single arbitrage opportunity card."""
    event_id = opp.get("event_id", "Unknown")[:20]
    edge = opp.get("edge", 0)
    confidence = opp.get("confidence", 0)

    header = f"#{index + 1} {event_id}... | Edge: {edge:.2%} | Conf: {confidence:.0%}"

    with st.expander(header, expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Delta Diff", format_delta(opp.get("delta_diff", 0)))
        with col2:
            st.metric("Edge", format_delta(edge))
        with col3:
            st.metric("Confidence", f"{confidence:.0%}")

        # Legs table
        legs = opp.get("legs", [])
        if legs:
            st.markdown("**Legs:**")
            leg_rows = []
            for leg in legs:
                leg_rows.append({
                    "Side": leg.get("side", ""),
                    "Tenor": f"{leg.get('tenor_days', 0)}d",
                    "Mid": format_price(leg.get("current_mid", 0)),
                    "Δ": format_delta(leg.get("delta", 0)),
                    "Question": truncate(leg.get("question", ""), 40),
                })
            st.dataframe(pd.DataFrame(leg_rows), use_container_width=True, hide_index=True)

        # Risk flags
        risk_flags = opp.get("risk_flags", [])
        if risk_flags:
            for flag in risk_flags:
                st.warning(flag)
