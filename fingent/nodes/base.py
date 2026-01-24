"""
Base node class for all LangGraph nodes.

All nodes must:
- Inherit from BaseNode
- Implement run(state) method
- Return partial state update (dict)
- Write errors to state["errors"], not raise exceptions
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from fingent.core.config import Settings, get_settings, load_yaml_config
from fingent.core.errors import NodeExecutionError
from fingent.core.logging import LoggerMixin
from fingent.core.timeutil import format_timestamp, now_utc


class BaseNode(ABC, LoggerMixin):
    """
    Abstract base class for LangGraph nodes.

    Each node:
    - Has a unique name
    - Receives full GraphState
    - Returns partial state update
    - Handles errors gracefully
    """

    node_name: str = "base"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        config: Optional[dict] = None,
    ):
        """
        Initialize node.

        Args:
            settings: Application settings
            config: YAML configuration
        """
        self.settings = settings or get_settings()
        self.config = config or load_yaml_config()

    @abstractmethod
    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Execute node logic.

        Args:
            state: Current GraphState

        Returns:
            Partial state update (only fields to update)

        Contract:
        - MUST return a dict
        - MUST NOT raise exceptions (write to state["errors"] instead)
        - SHOULD produce signals when applicable
        - SHOULD be idempotent
        """
        pass

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """Make node callable for LangGraph."""
        return self.safe_run(state)

    def safe_run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Execute node with error handling.

        Wraps run() to catch exceptions and record them in state.
        """
        self.logger.info(f"Node {self.node_name} starting")

        try:
            result = self.run(state)
            self.logger.info(f"Node {self.node_name} completed")
            return result

        except Exception as e:
            self.logger.error(f"Node {self.node_name} failed: {e}", exc_info=True)

            # Record error in state
            error = {
                "node": self.node_name,
                "error": str(e),
                "timestamp": format_timestamp(now_utc()),
                "recoverable": True,
            }

            return {"errors": [error]}

    def get_run_id(self, state: dict[str, Any]) -> str:
        """Get run_id from state."""
        return state.get("run_id", "unknown")

    def get_existing_signals(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Get existing signals from state."""
        return state.get("signals", [])

    def get_existing_errors(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Get existing errors from state."""
        return state.get("errors", [])

    def merge_signals(
        self,
        existing: list[dict[str, Any]],
        new_signals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Merge new signals with existing ones, avoiding duplicates.

        Uses signal ID for deduplication.
        """
        existing_ids = {s.get("id") for s in existing}
        merged = list(existing)

        for signal in new_signals:
            if signal.get("id") not in existing_ids:
                merged.append(signal)
                existing_ids.add(signal.get("id"))

        return merged

    def create_error(
        self,
        message: str,
        *,
        recoverable: bool = True,
        details: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Create an error dict for state."""
        return {
            "node": self.node_name,
            "error": message,
            "timestamp": format_timestamp(now_utc()),
            "recoverable": recoverable,
            "details": details or {},
        }
