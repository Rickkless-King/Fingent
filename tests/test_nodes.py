"""Tests for LangGraph nodes."""

import pytest
from unittest.mock import Mock

from fingent.nodes.base import BaseNode
from fingent.nodes.bootstrap import BootstrapNode
from fingent.graph.state import create_initial_state


class TestBootstrapNode:
    """Tests for BootstrapNode."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = Mock()
        settings.timezone = "America/New_York"
        return settings

    def test_bootstrap_creates_run_id(self, mock_settings):
        """Test that bootstrap creates a unique run_id."""
        node = BootstrapNode(settings=mock_settings)
        state = {}

        result = node.run(state)

        assert "run_id" in result
        assert result["run_id"].startswith("run_")

    def test_bootstrap_initializes_collections(self, mock_settings):
        """Test that bootstrap initializes empty collections."""
        node = BootstrapNode(settings=mock_settings)
        state = {}

        result = node.run(state)

        assert result["signals"] == []
        assert result["alerts"] == []
        assert result["errors"] == []
        assert result["macro_data"] == {}
        assert result["market_data"] == {}

    def test_bootstrap_preserves_existing_errors(self, mock_settings):
        """Test that bootstrap preserves errors from previous state."""
        node = BootstrapNode(settings=mock_settings)
        existing_error = {"node": "test", "error": "test error"}
        state = {"errors": [existing_error]}

        result = node.run(state)

        assert len(result["errors"]) == 1
        assert result["errors"][0] == existing_error


class TestBaseNode:
    """Tests for BaseNode functionality."""

    def test_safe_run_catches_exceptions(self):
        """Test that safe_run catches and records exceptions."""

        class FailingNode(BaseNode):
            node_name = "failing"

            def run(self, state):
                raise ValueError("Test error")

        node = FailingNode()
        state = {"errors": []}

        result = node.safe_run(state)

        assert "errors" in result
        assert len(result["errors"]) == 1
        assert result["errors"][0]["node"] == "failing"
        assert "Test error" in result["errors"][0]["error"]

    def test_merge_signals_deduplicates(self):
        """Test that merge_signals removes duplicates by ID."""

        class TestNode(BaseNode):
            node_name = "test"

            def run(self, state):
                return {}

        node = TestNode()

        existing = [{"id": "sig1", "name": "test1"}]
        new = [
            {"id": "sig1", "name": "test1_updated"},  # Duplicate
            {"id": "sig2", "name": "test2"},  # New
        ]

        result = node.merge_signals(existing, new)

        assert len(result) == 2
        assert result[0]["name"] == "test1"  # Original preserved
        assert result[1]["name"] == "test2"


class TestGraphState:
    """Tests for GraphState."""

    def test_create_initial_state(self):
        """Test initial state creation."""
        state = create_initial_state()

        assert state["run_id"] == ""
        assert state["signals"] == []
        assert state["alerts"] == []
        assert state["errors"] == []
