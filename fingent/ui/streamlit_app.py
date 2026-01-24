"""
Streamlit dashboard for Fingent.

Run with: streamlit run fingent/ui/streamlit_app.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime

# Import Fingent modules
from fingent.core.config import get_settings
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


if __name__ == "__main__":
    main()
