"""
Fingent Dashboard - Streamlit UI""" """ """ """ """ """ """ """ """ """ """ """ """ """ """ """ """  """ """ """ """ """ """ """ """ """ """ """ """ """ """ """ """ """

A clean, focused dashboard for macro financial analysis and Polymarket monitoring.

Run with: streamlit run fingent/ui/streamlit_app.py
"""

import streamlit as st
import pandas as pd

# Fingent imports
from fingent.core.config import get_settings, load_yaml_config
from fingent.services.persistence import create_persistence_service
from fingent.graph.builder import run_workflow, create_default_workflow
from fingent.graph.state import create_initial_state

# UI components
from fingent.ui.components import (
    render_kpi_row,
    render_shock_kpis,
    render_shock_table,
    render_movers_table,
    render_delta_scatter,
    render_news_list,
    render_market_metrics,
    render_arb_opportunity,
)

# Optional imports
try:
    from fingent.services.llm import create_llm_service, generate_morning_brief
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

try:
    from fingent.arb.engine import ArbEngine
    ARB_AVAILABLE = True
except ImportError:
    ARB_AVAILABLE = False

try:
    from fingent.services.shock_store import ShockStore
    SHOCK_STORE_AVAILABLE = True
except ImportError:
    SHOCK_STORE_AVAILABLE = False


# =============================================================================
# Page Config
# =============================================================================

def setup_page():
    """Configure Streamlit page settings."""
    st.set_page_config(
        page_title="Fingent - Macro Analysis",
        page_icon="📊",
        layout="wide",
    )


# =============================================================================
# Sidebar
# =============================================================================

def render_sidebar():
    """Render sidebar with controls and settings."""
    with st.sidebar:
        st.header("Controls")

        clear_cache = st.checkbox("Clear cache before run", value=False)

        if st.button("🔄 Run Analysis", type="primary"):
            with st.spinner("Running analysis..."):
                if clear_cache:
                    _clear_news_cache()
                _run_analysis()
            st.success("Analysis complete!")
            st.rerun()

        st.divider()

        # Settings display
        st.header("Settings")
        settings = get_settings()
        st.text(f"Env: {settings.fingent_env}")

        # Polymarket config (if enabled)
        if ARB_AVAILABLE:
            config = load_yaml_config()
            arb_config = config.get("arbitrage", {})
            shock_config = arb_config.get("probability_shock", {})

            st.divider()
            st.header("Polymarket")
            st.text(f"Enabled: {arb_config.get('enabled', False)}")
            st.text(f"Delta Threshold: {shock_config.get('delta_threshold', 0.05)}")
            st.text(f"Lookback: {shock_config.get('lookback_minutes', 60)} min")


def _clear_news_cache():
    """Clear news provider caches."""
    try:
        from fingent.core.cache import get_provider_cache
        for provider in ["marketaux", "fmp", "gnews", "finnhub", "alphavantage"]:
            try:
                get_provider_cache(provider).clear()
            except Exception:
                pass
    except Exception:
        pass


def _run_analysis():
    """Execute the analysis pipeline."""
    workflow = create_default_workflow()
    initial_state = create_initial_state()
    final_state = run_workflow(workflow, initial_state)

    persistence = create_persistence_service()
    persistence.save_snapshot(final_state)
    return final_state


# =============================================================================
# Tab 1: Latest Report
# =============================================================================

def render_report_tab(persistence):
    """Render the latest analysis report."""
    latest = persistence.load_latest()

    if not latest:
        st.info("No analysis data yet. Click 'Run Analysis' to start.")
        return

    report = latest.get("report", {})
    signals = latest.get("signals", [])
    alerts = latest.get("alerts", [])
    news_data = latest.get("news_data", {})
    market_data = latest.get("market_data", {})

    # === Section 1: Summary ===
    st.header("Today's Brief")
    signals_summary = report.get("signals_summary", {})
    direction = signals_summary.get("overall_direction", "neutral")
    score = signals_summary.get("overall_score", 0)

    _render_direction_summary(direction, score, signals, alerts)

    # AI Summary button
    if LLM_AVAILABLE:
        if st.button("✨ Generate AI Analysis"):
            with st.spinner("Generating..."):
                try:
                    llm = create_llm_service()
                    if llm:
                        summary = generate_morning_brief(llm, latest)
                        st.markdown(f"**AI Analysis:**\n\n{summary}")
                except Exception as e:
                    st.error(f"Failed: {e}")

    st.divider()

    # === Section 2: Key Metrics ===
    direction_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(direction, "⚪")
    metrics = [
        ("Direction", f"{direction_emoji} {direction.upper()}", f"{score:+.2f}"),
        ("Signals", len(signals), None),
        ("Alerts", len(alerts), "!" if alerts else None),
    ]
    render_kpi_row(metrics)

    st.divider()

    # === Section 3: News ===
    st.header("News")
    render_news_list(news_data.get("articles", []), limit=10)

    st.divider()

    # === Section 4: Market Data ===
    st.header("Markets")
    render_market_metrics(market_data.get("quotes", {}))

    # === Section 5: Signals (collapsed) ===
    with st.expander("📊 Signals Detail", expanded=False):
        if signals:
            df = pd.DataFrame(signals)
            cols = ["name", "direction", "score", "confidence"]
            df = df[[c for c in cols if c in df.columns]]
            st.dataframe(df.sort_values("score", key=abs, ascending=False), use_container_width=True)
        else:
            st.info("No signals")

    # === Section 6: Alerts (collapsed) ===
    with st.expander("⚠️ Alerts", expanded=bool(alerts)):
        if alerts:
            for alert in alerts:
                st.warning(f"**{alert.get('title')}**: {alert.get('message')}")
        else:
            st.success("No alerts")


def _render_direction_summary(direction: str, score: float, signals: list, alerts: list):
    """Generate a simple text summary."""
    direction_text = {
        "bullish": "bullish (risk-on)",
        "bearish": "bearish (risk-off)",
        "neutral": "neutral",
    }.get(direction, "neutral")

    summary = f"**Market Direction:** {direction_text.upper()} (score: {score:+.2f})\n\n"

    if signals:
        top = sorted(signals, key=lambda x: abs(x.get("score", 0)), reverse=True)[:3]
        summary += "**Key Signals:**\n"
        for s in top:
            summary += f"- {s.get('name')}: {s.get('direction')} ({s.get('score', 0):+.2f})\n"

    summary += f"\n**Alerts:** {len(alerts)} triggered" if alerts else "\n**Alerts:** None"
    st.markdown(summary)


# =============================================================================
# Tab 2: History
# =============================================================================

def render_history_tab(persistence):
    """Render analysis history."""
    st.header("Analysis History")

    snapshots = persistence.list_snapshots(limit=20)
    if not snapshots:
        st.info("No history yet")
        return

    # Recent runs list
    for snap in snapshots[:5]:
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.text(snap.get("timestamp", "")[:19])
        with col2:
            st.text(f"Signals: {snap.get('signal_count', 0)}")
        with col3:
            st.text(f"Alerts: {snap.get('alert_count', 0)}")

    # Trend chart
    st.divider()
    st.subheader("Score Trend")
    history_data = _build_history_data(persistence, snapshots)
    if history_data:
        df = pd.DataFrame(history_data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        st.line_chart(df.set_index("timestamp")[["overall_score"]])


def _build_history_data(persistence, snapshots) -> list[dict]:
    """Build history data for charting."""
    rows = []
    for item in snapshots[:20]:
        state = persistence.load_snapshot(item["run_id"])
        if state:
            summary = state.get("report", {}).get("signals_summary", {})
            rows.append({
                "timestamp": item["timestamp"],
                "overall_score": summary.get("overall_score", 0),
            })
    return rows


# =============================================================================
# Tab 3: Polymarket
# =============================================================================

def render_polymarket_tab():
    """Render Polymarket monitoring interface."""
    st.header("Polymarket Monitor")

    config = load_yaml_config()
    arb_config = config.get("arbitrage", {})
    shock_config = arb_config.get("probability_shock", {})

    enabled = arb_config.get("enabled", False)
    if not enabled:
        st.warning("Arbitrage is disabled. Set `arbitrage.enabled: true` in config.yaml")
        return

    # Initialize session state
    if "shock_store" not in st.session_state:
        st.session_state.shock_store = ShockStore() if SHOCK_STORE_AVAILABLE else None
    if "arb_engine" not in st.session_state:
        st.session_state.arb_engine = None
    if "shock_results" not in st.session_state:
        st.session_state.shock_results = None

    # === Controls ===
    st.subheader("Scan Controls")

    col1, col2, col3 = st.columns(3)

    with col1:
        delta_threshold = st.slider(
            "Delta Threshold",
            min_value=0.01,
            max_value=0.20,
            value=float(shock_config.get("delta_threshold", 0.05)),
            step=0.01,
        )

    with col2:
        allow_illiquid = st.checkbox(
            "Allow illiquid quotes",
            value=shock_config.get("allow_illiquid_quotes", False),
        )

    with col3:
        min_volume = st.number_input(
            "Min Volume 24h",
            min_value=0,
            max_value=100000,
            value=int(arb_config.get("risk", {}).get("min_volume_24h", 1000)),
            step=500,
        )

    # Scan buttons
    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:
        if st.button("⚡ Scan Shocks", type="primary"):
            _run_shock_scan(delta_threshold, allow_illiquid, min_volume)

    with col_btn2:
        if st.button("🔄 Reset Baselines"):
            st.session_state.shock_results = None
            st.session_state.arb_engine = None
            st.success("Baselines reset.")

    st.divider()

    # === Results ===
    results = st.session_state.shock_results

    if not results:
        st.info("Click 'Scan Shocks' to detect probability changes.")
        return

    # KPI Cards
    render_shock_kpis(results)

    st.divider()

    # Tabs for different views
    view_tab1, view_tab2, view_tab3 = st.tabs(["Shocks", "Top Movers", "Chart"])

    shocks = results.get("shocks", [])
    deltas = results.get("all_deltas", [])

    with view_tab1:
        st.subheader(f"Detected Shocks ({len(shocks)})")
        render_shock_table(shocks)

    with view_tab2:
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Largest Moves")
            render_movers_table(deltas, limit=10, sort_by="delta")
        with col_b:
            st.subheader("Most Liquid")
            render_movers_table(deltas, limit=10, sort_by="volume")

    with view_tab3:
        st.subheader("Delta Distribution")
        render_delta_scatter(deltas)

    # === Term Structure Arbitrage (collapsed) ===
    st.divider()
    with st.expander("📈 Term Structure Arbitrage", expanded=False):
        _render_arb_section()


def _run_shock_scan(delta_threshold: float, allow_illiquid: bool, min_volume: int):
    """Execute shock scan with given parameters."""
    with st.spinner("Scanning Polymarket..."):
        try:
            shock_store = st.session_state.shock_store
            engine = st.session_state.arb_engine or ArbEngine(shock_store=shock_store)
            st.session_state.arb_engine = engine

            # Apply overrides
            engine.config.setdefault("probability_shock", {})
            engine.config["probability_shock"]["delta_threshold"] = delta_threshold
            engine.config["probability_shock"]["allow_illiquid_quotes"] = allow_illiquid
            engine.config.setdefault("risk", {})
            engine.config["risk"]["min_volume_24h"] = float(min_volume)

            results = engine.scan_probability_shocks()
            st.session_state.shock_results = results

            found = results.get("shocks_found", 0)
            all_deltas_count = len(results.get("all_deltas", []))

            if found:
                st.success(f"Found {found} shock(s)! (Total deltas: {all_deltas_count})")
            else:
                st.info(f"No probability shocks detected. (Total deltas: {all_deltas_count})")

        except Exception as e:
            st.error(f"Scan failed: {e}")


def _render_arb_section():
    """Render term structure arbitrage section."""
    if st.button("🔍 Scan Arbitrage"):
        with st.spinner("Scanning..."):
            try:
                engine = st.session_state.arb_engine or ArbEngine()
                st.session_state.arb_engine = engine
                results = engine.run_full_pipeline(use_finnhub=False)

                opps = results.get("opportunities", [])
                if opps:
                    st.success(f"Found {len(opps)} opportunities!")
                    for i, opp in enumerate(opps):
                        render_arb_opportunity(opp, i)
                else:
                    st.info("No arbitrage opportunities detected.")

            except Exception as e:
                st.error(f"Scan failed: {e}")


# =============================================================================
# Tab 4: Raw Data
# =============================================================================

def render_raw_data_tab(persistence):
    """Render raw data explorer."""
    st.header("Raw Data")

    latest = persistence.load_latest()
    if not latest:
        st.info("No data available.")
        return

    # Overview metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Macro Indicators", len(latest.get("macro_data", {}).get("rates", {})))
    with col2:
        st.metric("Market Quotes", len(latest.get("market_data", {}).get("quotes", {})))
    with col3:
        st.metric("News Articles", len(latest.get("news_data", {}).get("articles", [])))

    st.divider()

    # Data tabs
    tab1, tab2, tab3 = st.tabs(["Macro", "Market", "Full JSON"])

    with tab1:
        macro = latest.get("macro_data", {})
        if macro.get("rates"):
            st.dataframe(pd.DataFrame([macro["rates"]]), use_container_width=True)
        else:
            st.info("No macro data")

    with tab2:
        quotes = latest.get("market_data", {}).get("quotes", {})
        if quotes:
            df = pd.DataFrame([{"symbol": k, **v} for k, v in quotes.items()])
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No market data")

    with tab3:
        st.json(latest)


# =============================================================================
# Main
# =============================================================================

def main():
    """Main application entry point."""
    setup_page()

    st.title("📊 Fingent Dashboard")
    st.caption("Top-Down Macro Financial Analysis")

    render_sidebar()

    persistence = create_persistence_service()

    # Main tabs
    if ARB_AVAILABLE:
        tab1, tab2, tab3, tab4 = st.tabs(["Report", "History", "Polymarket", "Raw Data"])
    else:
        tab1, tab2, tab4 = st.tabs(["Report", "History", "Raw Data"])
        tab3 = None

    with tab1:
        render_report_tab(persistence)

    with tab2:
        render_history_tab(persistence)

    if tab3 is not None:
        with tab3:
            render_polymarket_tab()

    with tab4:
        render_raw_data_tab(persistence)


if __name__ == "__main__":
    main()
