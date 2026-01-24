"""
Fingent CLI entry point.

Usage:
    # Run once
    python -m fingent.cli.main --once

    # Run with scheduler
    python -m fingent.cli.main --scheduled

    # Show status
    python -m fingent.cli.main --status
"""

import signal
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from fingent.core.config import get_settings, load_yaml_config
from fingent.core.logging import setup_logging, get_logger
from fingent.graph.builder import create_default_workflow, run_workflow
from fingent.graph.state import create_initial_state
from fingent.services.persistence import create_persistence_service
from fingent.services.scheduler import create_scheduler_service
from fingent.services.telegram import create_telegram_service

console = Console()
logger = get_logger("cli")


def run_pipeline_once() -> dict:
    """
    Execute the analysis pipeline once.

    Returns:
        Final state dict
    """
    logger.info("Starting single pipeline run")

    # Create workflow
    workflow = create_default_workflow()
    initial_state = create_initial_state()

    # Execute
    final_state = run_workflow(workflow, initial_state)

    # Persist results
    persistence = create_persistence_service()
    run_id = persistence.save_snapshot(final_state)

    # Send notifications
    alerts = final_state.get("alerts", [])
    if alerts:
        telegram = create_telegram_service()
        telegram.send_alerts(alerts)

    # Log summary
    logger.info(
        f"Pipeline complete. Run: {run_id}, "
        f"Signals: {len(final_state.get('signals', []))}, "
        f"Alerts: {len(alerts)}, "
        f"Errors: {len(final_state.get('errors', []))}"
    )

    return final_state


def display_report(state: dict) -> None:
    """Display report in terminal."""
    report = state.get("report", {})

    console.print("\n" + "=" * 60)
    console.print(f"[bold blue]{report.get('title', 'Fingent Report')}[/bold blue]")
    console.print("=" * 60 + "\n")

    # Summary
    if report.get("summary"):
        console.print("[bold]Summary[/bold]")
        console.print(report["summary"])
        console.print()

    # Signals table
    signals = state.get("signals", [])
    if signals:
        table = Table(title="Signals")
        table.add_column("Name", style="cyan")
        table.add_column("Direction", style="green")
        table.add_column("Score", justify="right")
        table.add_column("Source", style="dim")

        for sig in signals[:10]:
            direction_color = {
                "bullish": "green",
                "bearish": "red",
                "neutral": "white",
                "hawkish": "yellow",
                "dovish": "blue",
            }.get(sig.get("direction", ""), "white")

            table.add_row(
                sig.get("name", ""),
                f"[{direction_color}]{sig.get('direction', '')}[/{direction_color}]",
                f"{sig.get('score', 0):+.2f}",
                sig.get("source_node", ""),
            )

        console.print(table)
        console.print()

    # Alerts
    alerts = state.get("alerts", [])
    if alerts:
        console.print("[bold red]Alerts[/bold red]")
        for alert in alerts:
            severity = alert.get("severity", "medium")
            emoji = {"low": "📢", "medium": "⚠️", "high": "🚨", "critical": "🔴"}.get(
                severity, "⚠️"
            )
            console.print(f"  {emoji} {alert.get('title')}: {alert.get('message')}")
        console.print()

    # Errors
    errors = state.get("errors", [])
    if errors:
        console.print("[bold yellow]Errors[/bold yellow]")
        for error in errors:
            console.print(f"  ⚠️ [{error.get('node')}] {error.get('error')}")
        console.print()


@click.command()
@click.option("--once", is_flag=True, help="Run pipeline once and exit")
@click.option("--scheduled", is_flag=True, help="Run with scheduler")
@click.option("--status", is_flag=True, help="Show system status")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(once: bool, scheduled: bool, status: bool, verbose: bool) -> None:
    """Fingent - Top-Down Macro Financial Analysis System"""

    # Setup logging
    log_level = "DEBUG" if verbose else None
    setup_logging(log_level=log_level)

    if status:
        show_status()
        return

    if once:
        console.print("[bold]Running Fingent analysis...[/bold]\n")
        state = run_pipeline_once()
        display_report(state)
        return

    if scheduled:
        run_scheduled()
        return

    # Default: show help
    ctx = click.get_current_context()
    click.echo(ctx.get_help())


def show_status() -> None:
    """Show system status."""
    settings = get_settings()
    config = load_yaml_config()

    console.print("\n[bold]Fingent System Status[/bold]\n")

    # Settings table
    table = Table(title="Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value")

    table.add_row("Environment", settings.fingent_env)
    table.add_row("Timezone", settings.timezone)
    table.add_row("Log Level", settings.log_level)
    table.add_row("Database", settings.database_url)

    console.print(table)
    console.print()

    # API Keys status
    table = Table(title="API Keys")
    table.add_column("Provider", style="cyan")
    table.add_column("Status")

    def check_key(key: Optional[str]) -> str:
        if key and len(key) > 5:
            return "[green]✓ Configured[/green]"
        return "[red]✗ Missing[/red]"

    table.add_row("FRED", check_key(settings.fred_api_key))
    table.add_row("Finnhub", check_key(settings.finnhub_api_key))
    table.add_row("AlphaVantage", check_key(settings.alphavantage_api_key))
    table.add_row("OKX", check_key(settings.okx_api_key))
    table.add_row("DeepSeek", check_key(settings.deepseek_api_key))
    table.add_row("Qwen", check_key(settings.dashscope_api_key))
    table.add_row("Telegram", check_key(settings.telegram_bot_token))

    console.print(table)
    console.print()

    # Recent runs
    persistence = create_persistence_service()
    recent = persistence.list_snapshots(limit=5)

    if recent:
        table = Table(title="Recent Runs")
        table.add_column("Run ID", style="cyan")
        table.add_column("Time")
        table.add_column("Signals", justify="right")
        table.add_column("Alerts", justify="right")
        table.add_column("Errors", justify="right")

        for run in recent:
            table.add_row(
                run["run_id"][:20] + "...",
                run["timestamp"][:19],
                str(run["signal_count"]),
                str(run["alert_count"]),
                str(run["error_count"]),
            )

        console.print(table)


def run_scheduled() -> None:
    """Run with scheduler."""
    console.print("[bold]Starting Fingent scheduler...[/bold]")
    console.print("Press Ctrl+C to stop\n")

    scheduler = create_scheduler_service()
    scheduler.setup_from_config(run_pipeline_once)
    scheduler.start()

    # Show scheduled jobs
    jobs = scheduler.get_jobs()
    if jobs:
        table = Table(title="Scheduled Jobs")
        table.add_column("Job", style="cyan")
        table.add_column("Next Run")

        for job in jobs:
            table.add_row(job["name"], job["next_run"] or "N/A")

        console.print(table)
    else:
        console.print("[yellow]No jobs scheduled. Check config/config.yaml[/yellow]")

    # Handle shutdown
    def shutdown(signum, frame):
        console.print("\n[yellow]Shutting down...[/yellow]")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep running
    try:
        while True:
            signal.pause()
    except AttributeError:
        # Windows doesn't have signal.pause
        import time
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
