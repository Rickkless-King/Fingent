"""
Streamlit dashboard for Fingent.

Run with: streamlit run fingent/ui/streamlit_app.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime

# Import Fingent modules
from fingent.core.config import get_settings, load_yaml_config
from fingent.services.persistence import create_persistence_service
from fingent.graph.builder import run_workflow, create_default_workflow
from fingent.graph.state import create_initial_state

# Arbitrage imports (optional - graceful if not available)
try:
    from fingent.arb.engine import ArbEngine
    ARB_AVAILABLE = True
except ImportError:
    ARB_AVAILABLE = False


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

        if st.button("🔄 Run Analysis", type="primary"):
            with st.spinner("Running analysis..."):
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
    """Display the latest analysis report."""
    latest = persistence.load_latest()

    if not latest:
        st.info("No analysis data yet. Click 'Run Analysis' to start.")
        return

    report = latest.get("report", {})
    signals = latest.get("signals", [])
    alerts = latest.get("alerts", [])
    errors = latest.get("errors", [])

    # Header metrics
    col1, col2, col3, col4 = st.columns(4)

    signals_summary = report.get("signals_summary", {})
    direction = signals_summary.get("overall_direction", "neutral")
    score = signals_summary.get("overall_score", 0)

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
        st.metric("Alerts", len(alerts))

    with col4:
        st.metric("Errors", len(errors))

    st.divider()

    # Summary
    if report.get("summary"):
        st.header("Summary")
        st.markdown(report["summary"])

    # Trend charts from history
    history_df = build_history_df(persistence, limit=20)
    if not history_df.empty:
        st.header("Trends")
        chart_cols = st.columns(2)
        with chart_cols[0]:
            st.subheader("Signals & Alerts")
            st.line_chart(
                history_df.set_index("timestamp")[["signals", "alerts"]],
                use_container_width=True,
            )
        with chart_cols[1]:
            st.subheader("Overall Score")
            st.line_chart(
                history_df.set_index("timestamp")[["overall_score"]],
                use_container_width=True,
            )

    # Two columns: Signals and Alerts
    col1, col2 = st.columns(2)

    with col1:
        st.header("Signals")
        if signals:
            df = pd.DataFrame(signals)
            df = df[["name", "direction", "score", "confidence", "source_node"]]
            df = df.sort_values("score", key=abs, ascending=False)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No signals")

    with col2:
        st.header("Alerts")
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

    # Report sections
    sections = report.get("sections", [])
    if sections:
        st.header("Report Sections")
        for section in sections:
            st.subheader(section.get("title", "Section"))
            if section.get("content"):
                st.write(section["content"])
            key_points = section.get("key_points", [])
            if key_points:
                st.markdown("\n".join([f"- {p}" for p in key_points]))

    # News list
    news_data = latest.get("news_data", {})
    articles = news_data.get("articles", [])
    if articles:
        st.header("News")
        for article in articles:
            title = article.get("title", "Untitled")
            url = article.get("url")
            source = article.get("source", "unknown")
            published_at = article.get("published_at", "")
            summary = article.get("summary", "")
            with st.expander(f"{title} ({source})"):
                if url:
                    st.markdown(f"[Link]({url})")
                if published_at:
                    st.caption(f"Published: {published_at}")
                if summary:
                    st.write(summary)

    # Errors
    if errors:
        st.header("Errors")
        for error in errors:
            st.warning(f"**{error.get('node')}**: {error.get('error')}")


def show_history(persistence):
    """Show analysis history."""
    st.header("Analysis History")

    snapshots = persistence.list_snapshots(limit=20)

    if not snapshots:
        st.info("No history yet")
        return

    df = pd.DataFrame(snapshots)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    st.dataframe(df, use_container_width=True)

    st.subheader("History Trends")
    history_df = build_history_df(persistence, limit=20)
    if not history_df.empty:
        st.line_chart(
            history_df.set_index("timestamp")[["signals", "alerts", "overall_score"]],
            use_container_width=True,
        )

    # Select a run to view
    selected_run = st.selectbox(
        "Select a run to view details",
        options=[s["run_id"] for s in snapshots],
    )

    if selected_run:
        state = persistence.load_snapshot(selected_run)
        if state:
            st.json(state.get("report", {}))


def show_raw_data(persistence):
    """Show raw state data."""
    st.header("Raw Data")

    latest = persistence.load_latest()

    if not latest:
        st.info("No data")
        return

    tab1, tab2, tab3, tab4 = st.tabs(["Macro", "Market", "News", "Full State"])

    with tab1:
        st.json(latest.get("macro_data", {}))

    with tab2:
        st.json(latest.get("market_data", {}))

    with tab3:
        st.json(latest.get("news_data", {}))

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

                    st.info(
                        f"Scanned {results.get('news_scanned', 0)} news items, "
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
