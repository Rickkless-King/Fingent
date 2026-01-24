"""
Bootstrap node - Initializes the workflow run.

Responsibilities:
- Generate unique run_id
- Set timestamp (asof)
- Initialize empty collections
"""

from typing import Any

from fingent.core.timeutil import format_timestamp, generate_run_id, now_utc
from fingent.nodes.base import BaseNode


class BootstrapNode(BaseNode):
    """
    Bootstrap node for workflow initialization.

    This should be the first node in the workflow.
    Sets up run metadata and initializes state collections.
    """

    node_name = "bootstrap"

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Initialize workflow state.

        Returns:
            Initial state with run_id, timestamp, and empty collections
        """
        run_id = generate_run_id()
        timestamp = format_timestamp(now_utc())

        self.logger.info(f"Starting new run: {run_id}")

        return {
            "run_id": run_id,
            "asof": timestamp,
            "timezone": self.settings.timezone,

            # Initialize empty collections (will be populated by other nodes)
            "macro_data": {},
            "market_data": {},
            "news_data": [],
            "sentiment_data": {},

            # Signals from all nodes
            "signals": [],

            # Output
            "alerts": [],
            "report": {},

            # Errors (already may have some from previous state)
            "errors": state.get("errors", []),
        }
