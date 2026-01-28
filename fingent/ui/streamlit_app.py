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

    # Tabs
    tab1, tab2, tab3 = st.tabs(["Latest Report", "History", "Raw Data"])

    with tab1:
        show_latest_report(persistence)

    with tab2:
        show_history(persistence)

    with tab3:
        show_raw_data(persistence)


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


if __name__ == "__main__":
    main()
