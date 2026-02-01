"""
Streamlit dashboard for Fingent.

Run with: streamlit run fingent/ui/streamlit_app.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import os
import sys

# Import Fingent modules
from fingent.core.config import get_settings, load_yaml_config
from fingent.services.persistence import create_persistence_service
from fingent.graph.builder import run_workflow, create_default_workflow
from fingent.graph.state import create_initial_state

# LLM imports for on-demand summary
try:
    from fingent.services.llm import create_llm_service, generate_morning_brief
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

# Arbitrage imports (optional - graceful if not available)
try:
    from fingent.arb.engine import ArbEngine
    ARB_AVAILABLE = True
except ImportError:
    ARB_AVAILABLE = False

# Shock store for persistent baselines (optional)
try:
    from fingent.services.shock_store import ShockStore
    SHOCK_STORE_AVAILABLE = True
except ImportError:
    SHOCK_STORE_AVAILABLE = False


def _clear_news_cache():
    """Clear all news-related caches to force fresh data fetch."""
    try:
        from fingent.core.cache import get_provider_cache
        # Clear caches for all news providers
        for provider in ["marketaux", "fmp", "gnews", "finnhub", "alphavantage"]:
            try:
                cache = get_provider_cache(provider)
                cache.clear()
            except Exception:
                pass
        # Also clear the news router singleton to reset stats
        try:
            from fingent.providers import news_router
            news_router._news_router = None
        except Exception:
            pass
    except Exception as e:
        st.warning(f"Could not clear cache: {e}")


def main():
    st.set_page_config(
        page_title="Fingent - Macro Analysis",
        page_icon="📊",
        layout="wide",
    )

    st.title("📊 Fingent Dashboard")
    st.caption("Top-Down Macro Financial Analysis System")

    # Sidebar
    with st.sidebar:
        st.header("Controls")

        # Clear cache option
        clear_cache = st.checkbox("Clear cache before run", value=False,
                                   help="Force fetch fresh data, ignore cached results")

        if st.button("🔄 Run Analysis", type="primary"):
            with st.spinner("Running analysis..."):
                if clear_cache:
                    _clear_news_cache()
                run_analysis()
            st.success("Analysis complete!")
            st.rerun()

        st.divider()

        # Settings display
        st.header("Settings")
        settings = get_settings()
        st.text(f"Environment: {settings.fingent_env}")
        st.text(f"Timezone: {settings.timezone}")

        st.divider()
        st.header("Usage Mode")
        config = load_yaml_config()
        usage = config.get("usage_mode", {})
        st.text(f"Mode: {usage.get('name', 'default')}")
        st.text(f"Enabled: {usage.get('enabled', False)}")
        quotas = usage.get("quotas", {})
        if quotas:
            st.caption("Quotas")
            for provider, limit in quotas.items():
                st.text(f"{provider}: {limit}")

    # Main content
    persistence = create_persistence_service()

    # Tabs - include Polymarket if available
    if ARB_AVAILABLE:
        tab1, tab2, tab3, tab4 = st.tabs(["Latest Report", "History", "Raw Data", "Polymarket"])
    else:
        tab1, tab2, tab3 = st.tabs(["Latest Report", "History", "Raw Data"])
        tab4 = None

    with tab1:
        show_latest_report(persistence)

    with tab2:
        show_history(persistence)

    with tab3:
        show_raw_data(persistence)

    if tab4 is not None:
        with tab4:
            show_arbitrage()


def run_analysis():
    """Run the analysis pipeline."""
    workflow = create_default_workflow()
    initial_state = create_initial_state()
    final_state = run_workflow(workflow, initial_state)

    # Save results
    persistence = create_persistence_service()
    persistence.save_snapshot(final_state)

    return final_state


def show_latest_report(persistence):
    """Display the latest analysis report with improved UI."""
    latest = persistence.load_latest()

    if not latest:
        st.info("No analysis data yet. Click 'Run Analysis' to start.")
        return

    report = latest.get("report", {})
    signals = latest.get("signals", [])
    alerts = latest.get("alerts", [])
    errors = latest.get("errors", [])
    news_data = latest.get("news_data", {})

    # ============================================
    # Section 1: Morning Brief / AI Summary
    # ============================================
    st.header("Today's Market Brief")

    # Check if AI summary exists in session state
    if "ai_summary" not in st.session_state:
        st.session_state.ai_summary = None

    # Show existing summary or template
    if report.get("summary"):
        # Show the existing summary (from pipeline)
        with st.container():
            st.markdown(f"> {report['summary']}")
    elif st.session_state.ai_summary:
        # Show AI-generated summary
        with st.container():
            st.markdown(f"**AI Analysis:**\n\n{st.session_state.ai_summary}")
    else:
        # Show template summary
        signals_summary = report.get("signals_summary", {})
        direction = signals_summary.get("overall_direction", "neutral")
        score = signals_summary.get("overall_score", 0)

        template_summary = _generate_template_summary(direction, score, signals, alerts)
        st.markdown(template_summary)

    # AI Generate button
    if LLM_AVAILABLE:
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("✨ Generate AI Analysis", help="Use LLM to generate detailed analysis"):
                with st.spinner("Generating AI analysis..."):
                    try:
                        llm = create_llm_service()
                        if llm:
                            summary = generate_morning_brief(llm, latest)
                            st.session_state.ai_summary = summary
                            st.rerun()
                        else:
                            st.error("LLM not configured. Check API keys in .env")
                    except Exception as e:
                        st.error(f"Failed to generate: {e}")

    st.divider()

    # ============================================
    # Section 2: Key Metrics (compact)
    # ============================================
    signals_summary = report.get("signals_summary", {})
    direction = signals_summary.get("overall_direction", "neutral")
    score = signals_summary.get("overall_score", 0)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        direction_emoji = {
            "strong_bullish": "🟢",
            "bullish": "🟢",
            "bearish": "🔴",
            "strong_bearish": "🔴",
            "neutral": "⚪",
            "hawkish": "🦅",
            "dovish": "🕊️",
        }.get(direction, "⚪")
        st.metric("Direction", f"{direction_emoji} {direction.upper()}", f"{score:+.2f}")

    with col2:
        st.metric("Signals", len(signals))

    with col3:
        alert_color = "normal" if not alerts else "inverse"
        st.metric("Alerts", len(alerts), delta="!" if alerts else None, delta_color=alert_color)

    with col4:
        st.metric("Errors", len(errors))

    # Show direction breakdown if available
    direction_driver = signals_summary.get("direction_driver", "")
    direction_components = signals_summary.get("direction_components", {})

    if direction_driver or direction_components:
        with st.expander("📊 Direction Breakdown", expanded=False):
            # Driver explanation
            driver_text = {
                "actual_market_data": "实际市场数据（S&P 500、VIX 等价格变动）",
                "cross_asset": "跨资产分析（股票、加密货币、避险资产联动）",
                "macro_auditor": "宏观经济指标（利率、通胀、就业）",
                "news_impact": "新闻情绪分析",
            }.get(direction_driver, direction_driver)

            st.markdown(f"**主要驱动因素**: {driver_text}")

            # Component breakdown
            if direction_components:
                st.markdown("**各因素贡献**:")
                for name, value in sorted(direction_components.items(), key=lambda x: abs(x[1]), reverse=True):
                    if abs(value) > 0.01:
                        # Color based on value
                        if value > 0.2:
                            color = "green"
                        elif value < -0.2:
                            color = "red"
                        else:
                            color = "gray"

                        name_text = {
                            "cross_asset": "跨资产分析",
                            "macro_auditor": "宏观经济",
                            "news_impact": "新闻情绪",
                            "market_data_direct": "实际市场数据",
                        }.get(name, name)

                        st.markdown(f"- {name_text}: <span style='color:{color}'>{value:+.2f}</span>", unsafe_allow_html=True)

            # Confidence
            confidence = signals_summary.get("direction_confidence", 0)
            st.markdown(f"**置信度**: {confidence*100:.0f}%")

    # ============================================
    # Section 3: News (compact list - title + time + score)
    # ============================================
    st.header("News")

    articles = news_data.get("articles", [])
    if articles:
        _render_news_compact(articles[:15])  # Show top 15, expandable
    else:
        st.info("No news articles available")

    st.divider()

    # ============================================
    # Section 4: Market Data (compact cards)
    # ============================================
    st.header("Market Overview")

    market_data = latest.get("market_data", {})
    macro_data = latest.get("macro_data", {})

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Prices")
        _render_market_cards(market_data)

    with col2:
        st.subheader("Macro Indicators")
        _render_macro_cards(macro_data)

    st.divider()

    # ============================================
    # Section 5: Signals & Alerts (collapsible)
    # ============================================
    with st.expander("📊 Signals Detail", expanded=False):
        if signals:
            df = pd.DataFrame(signals)
            display_cols = ["name", "direction", "score", "confidence", "source_node"]
            df = df[[c for c in display_cols if c in df.columns]]
            df = df.sort_values("score", key=abs, ascending=False)

            # Color code by direction
            st.dataframe(
                df,
                width="stretch",
                column_config={
                    "score": st.column_config.ProgressColumn(
                        "Score",
                        min_value=-1,
                        max_value=1,
                        format="%.2f"
                    ),
                    "confidence": st.column_config.ProgressColumn(
                        "Confidence",
                        min_value=0,
                        max_value=1,
                        format="%.0%%"
                    ),
                }
            )
        else:
            st.info("No signals")

    with st.expander("⚠️ Alerts Detail", expanded=bool(alerts)):
        if alerts:
            for alert in alerts:
                severity = alert.get("severity", "medium")
                color = {"low": "blue", "medium": "orange", "high": "red", "critical": "red"}.get(
                    severity, "orange"
                )
                st.markdown(
                    f":{color}[**{alert.get('title')}**]\n\n{alert.get('message')}"
                )
        else:
            st.success("No alerts triggered")

    # Errors (if any)
    if errors:
        with st.expander("❌ Errors", expanded=True):
            for error in errors:
                st.warning(f"**{error.get('node')}**: {error.get('error')}")


def _generate_template_summary(direction: str, score: float, signals: list, alerts: list) -> str:
    """Generate a template-based summary without LLM."""
    direction_text = {
        "bullish": "bullish (risk-on)",
        "bearish": "bearish (risk-off)",
        "neutral": "neutral",
        "hawkish": "hawkish (tightening bias)",
        "dovish": "dovish (easing bias)",
    }.get(direction, "neutral")

    summary = f"**Market Direction:** {direction_text.upper()} (score: {score:+.2f})\n\n"

    if signals:
        top_signals = sorted(signals, key=lambda x: abs(x.get("score", 0)), reverse=True)[:3]
        summary += "**Key Signals:**\n"
        for sig in top_signals:
            summary += f"- {sig.get('name')}: {sig.get('direction')} ({sig.get('score', 0):+.2f})\n"

    if alerts:
        summary += f"\n**Alerts:** {len(alerts)} triggered - review recommended"
    else:
        summary += "\n**Alerts:** None"

    summary += "\n\n*Click 'Generate AI Analysis' for detailed interpretation.*"

    return summary


def _render_news_compact(articles: list):
    """Render news as compact expandable list: title (time) + score."""
    for article in articles:
        title = article.get("title", "Untitled")
        source = article.get("source", "Unknown")
        published_at = article.get("published_at", "")
        summary = article.get("summary", "")
        url = article.get("url", "")
        sentiment = article.get("sentiment_score") or 0  # Handle None values

        # Format time
        time_str = ""
        if published_at:
            try:
                dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                diff = now - dt
                if diff < timedelta(hours=1):
                    time_str = f"{int(diff.total_seconds() / 60)}m"
                elif diff < timedelta(days=1):
                    time_str = f"{int(diff.total_seconds() / 3600)}h"
                else:
                    time_str = dt.strftime("%m/%d")
            except Exception:
                time_str = published_at[:10] if len(published_at) > 10 else published_at

        # Sentiment color and icon
        if sentiment > 0.3:
            sent_icon = "🟢"
            sent_color = "green"
        elif sentiment < -0.3:
            sent_icon = "🔴"
            sent_color = "red"
        else:
            sent_icon = "⚪"
            sent_color = "gray"

        # Compact header: icon + title (time) + score
        header_title = f"{sent_icon} {title[:60]}{'...' if len(title) > 60 else ''}"
        if time_str:
            header_title += f" ({time_str})"

        # Expandable for details
        with st.expander(header_title, expanded=False):
            # Score display with method indicator
            # Show N/A only if sentiment_method is missing (not analyzed)
            sentiment_method = article.get("sentiment_method", "")
            if sentiment_method:
                sentiment_display = f"{sentiment:+.2f}"
            else:
                sentiment_display = "N/A"

            method_badge = ""
            if sentiment_method == "source":
                method_badge = " <small>(API)</small>"
            elif sentiment_method == "keywords":
                method_badge = " <small>(Keywords)</small>"
            elif sentiment_method == "llm":
                method_badge = " <small>(AI)</small>"
            elif sentiment_method == "default":
                method_badge = " <small>(Default)</small>"

            st.markdown(
                f"**Sentiment:** <span style='color:{sent_color}'>{sentiment_display}</span>{method_badge} | "
                f"**Source:** {source}",
                unsafe_allow_html=True
            )

            # Summary
            if summary:
                st.markdown(summary[:300] + "..." if len(summary) > 300 else summary)

            # Link
            if url:
                st.markdown(f"[Read full article]({url})")


def _render_market_cards(market_data: dict):
    """Render market data as compact cards."""
    quotes = market_data.get("quotes", {})

    if not quotes:
        st.info("No market data")
        return

    # Create 2-column layout for cards
    symbols = list(quotes.keys())
    for i in range(0, len(symbols), 2):
        cols = st.columns(2)
        for j, col in enumerate(cols):
            if i + j < len(symbols):
                symbol = symbols[i + j]
                q = quotes[symbol]
                with col:
                    price = q.get("price", 0)
                    change = q.get("change_24h", 0) or 0

                    # Format change
                    change_str = f"{change:+.2%}" if change else "N/A"
                    delta_color = "normal" if change >= 0 else "inverse"

                    st.metric(
                        symbol,
                        f"${price:,.2f}" if price else "N/A",
                        change_str,
                        delta_color=delta_color
                    )


def _render_macro_cards(macro_data: dict):
    """Render macro data as compact cards."""
    rates = macro_data.get("rates", {})
    inflation = macro_data.get("inflation", {})

    metrics = []

    if rates.get("fed_funds_rate"):
        metrics.append(("Fed Rate", f"{rates['fed_funds_rate']:.2f}%", None))

    if rates.get("yield_spread_2y10y") is not None:
        spread = rates["yield_spread_2y10y"]
        metrics.append(("2Y-10Y Spread", f"{spread:+.2f}%", "inverse" if spread < 0 else "normal"))

    if inflation.get("cpi_yoy"):
        metrics.append(("CPI YoY", f"{inflation['cpi_yoy']:.1f}%", None))

    if not metrics:
        st.info("No macro data")
        return

    for i in range(0, len(metrics), 2):
        cols = st.columns(2)
        for j, col in enumerate(cols):
            if i + j < len(metrics):
                name, value, _ = metrics[i + j]
                with col:
                    st.metric(name, value)


def show_history(persistence):
    """Show analysis history with improved display."""
    st.header("Analysis History")

    snapshots = persistence.list_snapshots(limit=20)

    if not snapshots:
        st.info("No history yet")
        return

    # Summary cards for recent runs
    st.subheader("Recent Runs")

    for i, snap in enumerate(snapshots[:5]):
        with st.container():
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            with col1:
                ts = snap.get("timestamp", "")
                st.markdown(f"**{ts[:19]}**")
            with col2:
                st.metric("Signals", snap.get("signal_count", 0), label_visibility="collapsed")
            with col3:
                st.metric("Alerts", snap.get("alert_count", 0), label_visibility="collapsed")
            with col4:
                if st.button("View", key=f"view_{snap['run_id']}"):
                    st.session_state.selected_run = snap["run_id"]

        if i < 4:
            st.markdown("---")

    st.divider()

    # Trend chart
    st.subheader("Trends")
    history_df = build_history_df(persistence, limit=20)
    if not history_df.empty:
        tab1, tab2 = st.tabs(["Score Trend", "Signals & Alerts"])
        with tab1:
            st.line_chart(
                history_df.set_index("timestamp")[["overall_score"]],
                width="stretch",
            )
        with tab2:
            st.line_chart(
                history_df.set_index("timestamp")[["signals", "alerts"]],
                width="stretch",
            )

    # Detail view
    st.divider()
    st.subheader("Run Details")

    selected_run = st.selectbox(
        "Select a run to view",
        options=[s["run_id"] for s in snapshots],
        format_func=lambda x: f"{x[:8]}... ({next((s['timestamp'][:16] for s in snapshots if s['run_id'] == x), '')})"
    )

    if selected_run:
        state = persistence.load_snapshot(selected_run)
        if state:
            report = state.get("report", {})

            with st.expander("Report Summary", expanded=True):
                if report.get("summary"):
                    st.markdown(report["summary"])
                else:
                    st.info("No summary")

            with st.expander("Signals Summary"):
                st.json(report.get("signals_summary", {}))

            with st.expander("Full Report"):
                st.json(report)


def show_raw_data(persistence):
    """Show raw state data with better organization."""
    st.header("Raw Data Explorer")

    latest = persistence.load_latest()

    if not latest:
        st.info("No data")
        return

    # Overview
    st.subheader("Data Overview")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        macro_count = len(latest.get("macro_data", {}).get("rates", {}))
        st.metric("Macro Indicators", macro_count)
    with col2:
        market_count = len(latest.get("market_data", {}).get("quotes", {}))
        st.metric("Market Quotes", market_count)
    with col3:
        news_count = len(latest.get("news_data", {}).get("articles", []))
        st.metric("News Articles", news_count)
    with col4:
        signal_count = len(latest.get("signals", []))
        st.metric("Signals", signal_count)

    st.divider()

    # Tabs for different data types
    tab1, tab2, tab3, tab4 = st.tabs(["Macro", "Market", "News", "Full State"])

    with tab1:
        macro = latest.get("macro_data", {})
        if macro:
            # Rates
            if macro.get("rates"):
                st.subheader("Interest Rates")
                rates_df = pd.DataFrame([macro["rates"]])
                st.dataframe(rates_df, width="stretch")

            # Inflation
            if macro.get("inflation"):
                st.subheader("Inflation")
                st.json(macro["inflation"])

            # Employment
            if macro.get("employment"):
                st.subheader("Employment")
                st.json(macro["employment"])
        else:
            st.info("No macro data")

    with tab2:
        market = latest.get("market_data", {})
        if market.get("quotes"):
            quotes_list = [
                {"symbol": k, **v}
                for k, v in market["quotes"].items()
            ]
            df = pd.DataFrame(quotes_list)
            st.dataframe(df, width="stretch")
        else:
            st.info("No market data")

    with tab3:
        news = latest.get("news_data", {})
        articles = news.get("articles", [])
        if articles:
            df = pd.DataFrame(articles)
            display_cols = ["title", "source", "published_at", "sentiment_score"]
            df = df[[c for c in display_cols if c in df.columns]]
            st.dataframe(df, width="stretch")
        else:
            st.info("No news data")

    with tab4:
        st.json(latest)


def build_history_df(persistence, limit: int = 20) -> pd.DataFrame:
    snapshots = persistence.list_snapshots(limit=limit)
    rows = []
    for item in snapshots:
        state = persistence.load_snapshot(item["run_id"])
        report = (state or {}).get("report", {})
        summary = report.get("signals_summary", {})
        rows.append({
            "timestamp": item["timestamp"],
            "signals": item.get("signal_count", 0),
            "alerts": item.get("alert_count", 0),
            "overall_score": summary.get("overall_score", 0),
            "direction": summary.get("overall_direction", "neutral"),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    return df


def show_arbitrage():
    """Show Polymarket probability shock + arbitrage interface."""
    st.header("Polymarket Monitor")
    st.caption("Track probability shocks first, then term-structure arbitrage")

    config = load_yaml_config()
    arb_config = config.get("arbitrage", {})
    shock_config = arb_config.get("probability_shock", {})

    # Lightweight monitoring controls (optional)
    st.subheader("🛰️ Monitoring Control")
    monitor_status = _get_monitor_status()
    status_text = "Running" if monitor_status["running"] else "Stopped"
    st.info(f"Monitor Status: {status_text}")
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        if st.button("▶️ Start Monitor", disabled=monitor_status["running"]):
            started = _start_monitor_process()
            if started:
                st.success("Monitor started in background.")
            else:
                st.warning("Monitor is already running or failed to start.")
    with col_m2:
        if st.button("⏹️ Stop Monitor", disabled=not monitor_status["running"]):
            stopped = _stop_monitor_process()
            if stopped:
                st.success("Monitor stopped.")
            else:
                st.warning("Monitor not running or could not stop.")
    st.caption("Monitor runs in a background process and writes data to SQLite.")
    st.divider()

    # Status
    enabled = arb_config.get("enabled", False)
    shock_enabled = shock_config.get("enabled", True)
    if not enabled:
        st.warning(
            "Arbitrage detection is disabled in config. "
            "Set `arbitrage.enabled: true` in config/config.yaml to enable."
        )

    # Sidebar info for arbitrage
    with st.sidebar:
        st.divider()
        st.header("Polymarket Config")
        st.text(f"Arbitrage Enabled: {enabled}")
        st.text(f"Shock Enabled: {shock_enabled}")

        st.caption("Shock Monitor")
        st.text(f"Delta Threshold: {shock_config.get('delta_threshold', 0.05)}")
        st.text(f"Lookback: {shock_config.get('lookback_minutes', 60)} min")
        st.text(f"Min Age: {shock_config.get('min_age_seconds', 60)} s")

        ts_config = arb_config.get("term_structure", {})
        st.caption("Term Structure")
        st.text(f"Delta Threshold: {ts_config.get('delta_threshold', 0.05)}")
        st.text(f"Trigger Window: {ts_config.get('trigger_window_minutes', 120)} min")

        risk_config = arb_config.get("risk", {})
        st.caption("Risk Filters")
        st.text(f"Min Volume: ${risk_config.get('min_volume_24h', 5000)}")
        st.text(f"Max Spread: {risk_config.get('max_spread_bps', 300)} bps")
        st.text(f"Min Depth: ${risk_config.get('min_depth_usd', 1000)}")

    # Initialize engine in session state
    if "arb_engine" not in st.session_state:
        st.session_state.arb_engine = None
        st.session_state.arb_results = None
    if "shock_results" not in st.session_state:
        st.session_state.shock_results = None
    if "shock_store" not in st.session_state:
        st.session_state.shock_store = ShockStore() if SHOCK_STORE_AVAILABLE else None
    if "demo_loaded" not in st.session_state:
        st.session_state.demo_loaded = False

    # Demo data (for UI preview)
    with st.expander("🧪 Demo Data (fake, UI preview)", expanded=False):
        st.caption("This creates fake results so you can preview the UI layout.")
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            if st.button("Load Demo Results"):
                _load_demo_results()
                st.session_state.demo_loaded = True
                st.success("Demo data loaded.")
        with col_d2:
            if st.button("Clear Demo Results"):
                st.session_state.shock_results = None
                st.session_state.arb_results = None
                st.session_state.demo_loaded = False
                st.info("Demo data cleared.")

    # ============================================
    # Section 1: Probability Shock Leaderboard
    # ============================================
    st.subheader("⚡ Probability Shock Leaderboard")

    with st.expander("⚙️ Demo Settings (temporary overrides)", expanded=False):
        st.caption("These overrides apply only to this scan, for quick preview.")
        demo_delta = st.slider(
            "Delta Threshold",
            min_value=0.0,
            max_value=0.20,
            value=float(shock_config.get("delta_threshold", 0.05)),
            step=0.01,
        )
        demo_allow_illiquid = st.checkbox(
            "Allow synthetic quotes (no orderbook)",
            value=False,
            help="Use Gamma/CLOB price even if orderbook depth is missing.",
        )
        demo_allow_stale = st.checkbox(
            "Allow stale baselines",
            value=False,
            help="Use last stored baseline even if older than lookback.",
        )
        demo_min_volume = st.number_input(
            "Min Volume 24h (USD)",
            min_value=0,
            max_value=100000,
            value=int(arb_config.get("risk", {}).get("min_volume_24h", 5000)),
            step=500,
        )
        demo_min_depth = st.number_input(
            "Min Depth (USD)",
            min_value=0,
            max_value=100000,
            value=int(arb_config.get("risk", {}).get("min_depth_usd", 1000)),
            step=100,
        )
        demo_max_spread = st.number_input(
            "Max Spread (bps)",
            min_value=50,
            max_value=100000,
            value=min(int(arb_config.get("risk", {}).get("max_spread_bps", 300)), 100000),
            step=50,
        )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("⚡ Scan Probability Shocks", type="primary", disabled=not enabled or not shock_enabled):
            with st.spinner("Scanning Polymarket for probability shocks..."):
                try:
                    shock_store = st.session_state.shock_store
                    if st.session_state.arb_engine is None:
                        engine = ArbEngine(shock_store=shock_store)
                    else:
                        engine = st.session_state.arb_engine
                        if shock_store and getattr(engine, "shock_store", None) is None:
                            engine.shock_store = shock_store
                    st.session_state.arb_engine = engine
                    # Apply demo overrides for this scan only
                    original_shock = dict(engine.config.get("probability_shock", {}))
                    original_risk = dict(engine.config.get("risk", {}))
                    engine.config.setdefault("probability_shock", {})
                    engine.config["probability_shock"]["delta_threshold"] = float(demo_delta)
                    engine.config["probability_shock"]["allow_illiquid_quotes"] = bool(demo_allow_illiquid)
                    engine.config["probability_shock"]["allow_stale_baseline"] = bool(demo_allow_stale)
                    engine.config.setdefault("risk", {})
                    engine.config["risk"]["min_volume_24h"] = float(demo_min_volume)
                    engine.config["risk"]["min_depth_usd"] = float(demo_min_depth)
                    engine.config["risk"]["max_spread_bps"] = float(demo_max_spread)

                    results = engine.scan_probability_shocks()

                    # Restore config
                    engine.config["probability_shock"] = original_shock
                    engine.config["risk"] = original_risk
                    st.session_state.shock_results = results
                    found = results.get("shocks_found", 0)
                    if found:
                        st.success(f"Found {found} shock(s)!")
                    else:
                        st.info("No probability shocks detected")
                except Exception as e:
                    st.error(f"Shock scan failed: {e}")

    with col2:
        if st.button("🧹 Reset Shock Baselines", disabled=not enabled):
            st.session_state.shock_results = None
            st.session_state.arb_engine = None
            st.success("Shock baselines will refresh on next scan.")

    st.divider()

    shock_results = st.session_state.shock_results
    if shock_results:
        stat_cols = st.columns(4)
        with stat_cols[0]:
            st.metric("Events Scanned", shock_results.get("events_scanned", 0))
        with stat_cols[1]:
            st.metric("Markets Scanned", shock_results.get("markets_scanned", 0))
        with stat_cols[2]:
            st.metric("Shocks Found", shock_results.get("shocks_found", 0))
        with stat_cols[3]:
            st.metric("Tags", len(shock_results.get("tags_used", [])))

        shocks = shock_results.get("shocks", [])
        if shocks:
            df_rows = []
            for s in shocks:
                delta = s.get("delta", 0)
                direction = "⬆️" if delta > 0 else "⬇️"
                vol = s.get("volume_24h")
                df_rows.append({
                    "Dir": direction,
                    "Δ": f"{delta:+.2%}",
                    "Prob": f"{s.get('current_mid', 0):.2%}",
                    "Age": f"{s.get('age_minutes', 0):.1f}m",
                    "Sector": s.get("sector", "") or "N/A",
                    "Vol24h": f"${vol:,.0f}" if isinstance(vol, (int, float)) else "N/A",
                    "Spread": f"{s.get('spread_bps', 0):.0f}bps",
                    "Depth": f"${s.get('depth_usd', 0):,.0f}",
                    "Question": (s.get("question", "")[:90] + "...")
                    if len(s.get("question", "")) > 90 else s.get("question", ""),
                })
            df = pd.DataFrame(df_rows)
            st.dataframe(df, width="stretch", hide_index=True)

            with st.expander("Shock Details", expanded=False):
                for i, s in enumerate(shocks):
                    with st.container():
                        st.markdown(
                            f"**#{i+1} {s.get('question', 'Unknown')}**"
                        )
                        st.text(f"Event: {s.get('event_title', '')} ({s.get('event_id', '')[:8]}...)")
                        st.text(f"Market: {s.get('market_id', '')}")
                        if s.get("sector"):
                            st.text(f"Sector: {s.get('sector')}")
                        st.text(f"Δ: {s.get('delta', 0):+.2%} | Prob: {s.get('current_mid', 0):.2%}")
                        st.text(f"Age: {s.get('age_minutes', 0):.1f}m | Spread: {s.get('spread_bps', 0):.0f}bps")
                        st.text(f"Volume24h: {s.get('volume_24h', 0):,.0f} | Depth: {s.get('depth_usd', 0):,.0f}")
                        if s.get("end_time"):
                            st.text(f"End: {s.get('end_time')}")
                        # 24h history chart (if available)
                        shock_store = st.session_state.shock_store
                        market_id = s.get("market_id", "")
                        if shock_store and market_id:
                            history = shock_store.get_history(market_id, window_minutes=1440, limit=1000)
                            if history and len(history) > 1:
                                hist_df = pd.DataFrame(history)
                                hist_df["timestamp"] = pd.to_datetime(hist_df["timestamp"])
                                st.line_chart(
                                    hist_df.set_index("timestamp")[["mid"]],
                                    width="stretch",
                                )
                                stats = shock_store.get_window_stats(market_id, window_minutes=1440)
                                if stats:
                                    max_change = stats["max"] - stats["min"]
                                    baseline = stats["first"] or 0.0
                                    max_change_pct = (max_change / baseline) if baseline else 0.0
                                    st.caption(
                                        f"24h range: {stats['min']:.2%} -> {stats['max']:.2%} "
                                        f"(Delta {max_change:+.2%}, {max_change_pct:+.2%} vs first)"
                                    )
                            else:
                                st.caption("No 24h history yet (need more samples).")
                        st.markdown("---")
        else:
            st.info("No probability shocks in the current window.")
    else:
        st.info("Click 'Scan Probability Shocks' to build the leaderboard.")

    st.divider()

    # ============================================
    # Section 2: Term Structure Arbitrage
    # ============================================
    st.subheader("📈 Term Structure Arbitrage")

    # Controls
    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔍 Scan Polymarket (Arb)", disabled=not enabled):
            with st.spinner("Scanning Polymarket for arbitrage opportunities..."):
                try:
                    engine = st.session_state.arb_engine or ArbEngine()
                    st.session_state.arb_engine = engine

                    # Run scan
                    results = engine.run_full_pipeline(use_finnhub=False)
                    st.session_state.arb_results = results

                    if results.get("opportunities"):
                        st.success(f"Found {len(results['opportunities'])} opportunities!")
                    else:
                        st.info("No arbitrage opportunities detected")

                except Exception as e:
                    st.error(f"Scan failed: {e}")

    with col2:
        if st.button("📰 Scan with News Trigger (Arb)", disabled=not enabled):
            with st.spinner("Fetching news and scanning for arbitrage..."):
                try:
                    engine = st.session_state.arb_engine or ArbEngine()
                    st.session_state.arb_engine = engine

                    # Run with Finnhub news
                    results = engine.run_full_pipeline(use_finnhub=True)
                    st.session_state.arb_results = results

                    providers_used = results.get('news_providers_used', [])
                    providers_str = ", ".join(providers_used) if providers_used else "finnhub"
                    st.info(
                        f"Scanned {results.get('news_scanned', 0)} news items from [{providers_str}], "
                        f"{results.get('news_triggered', 0)} triggered keywords"
                    )

                    if results.get("opportunities"):
                        st.success(f"Found {len(results['opportunities'])} opportunities!")
                    else:
                        st.info("No arbitrage opportunities detected")

                except Exception as e:
                    st.error(f"Scan failed: {e}")

    # Display results
    results = st.session_state.arb_results

    if results:
        # Stats
        st.subheader("Scan Results")
        stat_cols = st.columns(4)
        with stat_cols[0]:
            st.metric("News Scanned", results.get("news_scanned", 0))
        with stat_cols[1]:
            st.metric("News Triggered", results.get("news_triggered", 0))
        with stat_cols[2]:
            st.metric("Events Found", results.get("events_found", 0))
        with stat_cols[3]:
            st.metric("Opportunities", results.get("opportunities_confirmed", 0))

        # Opportunities
        opportunities = results.get("opportunities", [])
        if opportunities:
            st.subheader("Detected Opportunities")

            for i, opp in enumerate(opportunities):
                with st.expander(
                    f"#{i+1} Event: {opp.get('event_id', 'Unknown')[:20]}... | "
                    f"Edge: {opp.get('edge', 0):.2%} | "
                    f"Confidence: {opp.get('confidence', 0):.0%}"
                ):
                    # Basic info
                    st.markdown(f"**Type:** {opp.get('type', 'TERM_STRUCTURE')}")
                    st.markdown(f"**Timestamp:** {opp.get('timestamp', '')}")
                    st.markdown(f"**Status:** {opp.get('status', 'UNKNOWN')}")

                    # Metrics
                    metric_cols = st.columns(3)
                    with metric_cols[0]:
                        st.metric("Delta Diff", f"{opp.get('delta_diff', 0):.2%}")
                    with metric_cols[1]:
                        st.metric("Edge", f"{opp.get('edge', 0):.2%}")
                    with metric_cols[2]:
                        st.metric("Confidence", f"{opp.get('confidence', 0):.0%}")

                    # Legs
                    st.markdown("**Legs:**")
                    legs = opp.get("legs", [])
                    if legs:
                        leg_df = pd.DataFrame(legs)
                        st.dataframe(leg_df, width="stretch")

                    # Risk flags
                    risk_flags = opp.get("risk_flags", [])
                    if risk_flags:
                        st.markdown("**Risk Flags:**")
                        for flag in risk_flags:
                            st.warning(flag)

                    # Evidence
                    evidence = opp.get("evidence", {})
                    if evidence:
                        with st.expander("Evidence (JSON)"):
                            st.json(evidence)

        # Errors
        errors = results.get("errors", [])
        if errors:
            st.subheader("Errors")
            for error in errors:
                st.error(error)

    else:
        st.info("Click 'Scan Polymarket (Arb)' or 'Scan with News Trigger (Arb)' to detect arbitrage opportunities.")

    # Keywords reference
    with st.expander("Trigger Keywords (from config)"):
        keywords = arb_config.get("trigger_keywords", [])
        if keywords:
            for kw in keywords:
                st.code(kw)
        else:
            st.info("No trigger keywords configured")


def _monitor_pid_path() -> Path:
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "monitor.pid"


def _get_monitor_status() -> dict:
    pid_path = _monitor_pid_path()
    if not pid_path.exists():
        return {"running": False, "pid": None}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        return {"running": False, "pid": None}
    return {"running": _pid_is_running(pid), "pid": pid}


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _start_monitor_process() -> bool:
    status = _get_monitor_status()
    if status["running"]:
        return False
    pid_path = _monitor_pid_path()
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [sys.executable, "-m", "fingent.cli.main", "--monitor"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            cwd=str(Path.cwd()),
        )
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        return True
    except Exception:
        return False


def _stop_monitor_process() -> bool:
    status = _get_monitor_status()
    pid = status.get("pid")
    if not pid or not status.get("running"):
        return False
    try:
        os.kill(pid, 15)
    except Exception:
        return False
    try:
        _monitor_pid_path().unlink(missing_ok=True)
    except Exception:
        pass
    return True


def _load_demo_results() -> None:
    """Inject fake results for UI preview."""
    now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    st.session_state.shock_results = {
        "timestamp": now_ts,
        "enabled": True,
        "tags_used": [],
        "events_scanned": 12,
        "markets_scanned": 48,
        "shocks_found": 3,
        "shocks": [
            {
                "event_id": "demo_event_1",
                "event_title": "Will Fed cut rates by June 2026?",
                "sector": "economy",
                "market_id": "demo_market_1",
                "question": "Will the Fed cut rates by June 2026?",
                "tags": ["economy", "fed"],
                "current_mid": 0.62,
                "baseline_mid": 0.48,
                "delta": 0.14,
                "age_minutes": 45.0,
                "volume_24h": 125000,
                "liquidity": 42000,
                "spread_bps": 55,
                "depth_usd": 1800,
                "end_time": "2026-06-30T23:59:00Z",
                "tenor_days": 150,
                "timestamp": now_ts,
            },
            {
                "event_id": "demo_event_2",
                "event_title": "Will BTC hit $100k in 2026?",
                "sector": "crypto",
                "market_id": "demo_market_2",
                "question": "Will Bitcoin hit $100k by end of 2026?",
                "tags": ["crypto", "bitcoin"],
                "current_mid": 0.41,
                "baseline_mid": 0.56,
                "delta": -0.15,
                "age_minutes": 30.0,
                "volume_24h": 98000,
                "liquidity": 31000,
                "spread_bps": 80,
                "depth_usd": 1500,
                "end_time": "2026-12-31T23:59:00Z",
                "tenor_days": 330,
                "timestamp": now_ts,
            },
            {
                "event_id": "demo_event_3",
                "event_title": "Will a major tariff be announced in 2026?",
                "sector": "politics",
                "market_id": "demo_market_3",
                "question": "Will the US announce new major tariffs in 2026?",
                "tags": ["politics", "trade"],
                "current_mid": 0.33,
                "baseline_mid": 0.25,
                "delta": 0.08,
                "age_minutes": 70.0,
                "volume_24h": 56000,
                "liquidity": 12000,
                "spread_bps": 120,
                "depth_usd": 900,
                "end_time": "2026-11-01T23:59:00Z",
                "tenor_days": 270,
                "timestamp": now_ts,
            },
        ],
        "latest_quotes": {},
        "errors": [],
    }

    st.session_state.arb_results = {
        "timestamp": now_ts,
        "enabled": True,
        "news_scanned": 3,
        "news_triggered": 1,
        "news_providers_used": ["demo"],
        "events_found": 1,
        "opportunities_raw": 1,
        "opportunities_confirmed": 1,
        "opportunities": [
            {
                "id": "demo_opp_1",
                "timestamp": now_ts,
                "type": "TERM_STRUCTURE",
                "event_id": "demo_event_1",
                "legs": [
                    {
                        "market_id": "demo_market_short",
                        "question": "Will the Fed cut rates by June 2026?",
                        "tenor_days": 120,
                        "side": "SHORT_LEG",
                        "current_mid": 0.64,
                        "delta": 0.15,
                    },
                    {
                        "market_id": "demo_market_long",
                        "question": "Will the Fed cut rates by Dec 2026?",
                        "tenor_days": 300,
                        "side": "LONG_LEG",
                        "current_mid": 0.52,
                        "delta": 0.05,
                    },
                ],
                "delta_diff": 0.10,
                "edge": 0.06,
                "confidence": 0.72,
                "evidence": {},
                "risk_flags": [],
                "status": "CONFIRMED",
            }
        ],
        "errors": [],
    }


if __name__ == "__main__":
    main()
