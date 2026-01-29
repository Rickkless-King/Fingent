"""
Streamlit dashboard for Fingent.

Run with: streamlit run fingent/ui/streamlit_app.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

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

    # Tabs - include Arbitrage if available
    if ARB_AVAILABLE:
        tab1, tab2, tab3, tab4 = st.tabs(["Latest Report", "History", "Raw Data", "Arbitrage"])
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
            "bullish": "🟢",
            "bearish": "🔴",
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
                use_container_width=True,
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
            sentiment_display = f"{sentiment:+.2f}" if sentiment else "N/A"
            sentiment_method = article.get("sentiment_method", "")
            method_badge = ""
            if sentiment_method == "source":
                method_badge = " <small>(API)</small>"
            elif sentiment_method == "keywords":
                method_badge = " <small>(Keywords)</small>"
            elif sentiment_method == "llm":
                method_badge = " <small>(AI)</small>"

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
                use_container_width=True,
            )
        with tab2:
            st.line_chart(
                history_df.set_index("timestamp")[["signals", "alerts"]],
                use_container_width=True,
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
                st.dataframe(rates_df, use_container_width=True)

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
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No market data")

    with tab3:
        news = latest.get("news_data", {})
        articles = news.get("articles", [])
        if articles:
            df = pd.DataFrame(articles)
            display_cols = ["title", "source", "published_at", "sentiment_score"]
            df = df[[c for c in display_cols if c in df.columns]]
            st.dataframe(df, use_container_width=True)
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
    """Show Polymarket arbitrage detection interface."""
    st.header("Polymarket Arbitrage Detection")
    st.caption("Term Structure Arbitrage: Detect price divergence between same-event markets with different expiries")

    config = load_yaml_config()
    arb_config = config.get("arbitrage", {})

    # Status
    enabled = arb_config.get("enabled", False)
    if not enabled:
        st.warning(
            "Arbitrage detection is disabled in config. "
            "Set `arbitrage.enabled: true` in config/config.yaml to enable."
        )

    # Sidebar info for arbitrage
    with st.sidebar:
        st.divider()
        st.header("Arbitrage Config")
        st.text(f"Enabled: {enabled}")

        ts_config = arb_config.get("term_structure", {})
        st.text(f"Delta Threshold: {ts_config.get('delta_threshold', 0.05)}")
        st.text(f"Trigger Window: {ts_config.get('trigger_window_minutes', 120)} min")

        risk_config = arb_config.get("risk", {})
        st.caption("Risk Filters")
        st.text(f"Min Volume: ${risk_config.get('min_volume_24h', 5000)}")
        st.text(f"Max Spread: {risk_config.get('max_spread_bps', 300)} bps")

    # Initialize engine in session state
    if "arb_engine" not in st.session_state:
        st.session_state.arb_engine = None
        st.session_state.arb_results = None

    # Controls
    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔍 Scan Polymarket", type="primary", disabled=not enabled):
            with st.spinner("Scanning Polymarket for arbitrage opportunities..."):
                try:
                    engine = ArbEngine()
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
        if st.button("📰 Scan with News Trigger", disabled=not enabled):
            with st.spinner("Fetching news and scanning for arbitrage..."):
                try:
                    engine = ArbEngine()
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

    st.divider()

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
                        st.dataframe(leg_df, use_container_width=True)

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
        st.info("Click 'Scan Polymarket' or 'Scan with News Trigger' to detect arbitrage opportunities.")

    # Keywords reference
    with st.expander("Trigger Keywords (from config)"):
        keywords = arb_config.get("trigger_keywords", [])
        if keywords:
            for kw in keywords:
                st.code(kw)
        else:
            st.info("No trigger keywords configured")


if __name__ == "__main__":
    main()
