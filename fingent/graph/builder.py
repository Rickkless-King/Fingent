"""
Workflow builder for LangGraph.

Assembles nodes into an executable workflow graph.
"""

from typing import Any, Callable, Optional

from langgraph.graph import StateGraph, END

from fingent.core.config import Settings, get_settings, load_yaml_config
from fingent.core.logging import get_logger
from fingent.graph.state import GraphState, create_initial_state
from fingent.graph.registry import (
    NodeRegistry,
    ProviderRegistry,
    create_default_registries,
)

logger = get_logger("builder")


class WorkflowBuilder:
    """
    Builder for LangGraph workflows.

    Provides a fluent API for constructing the analysis workflow.
    """

    def __init__(
        self,
        provider_registry: Optional[ProviderRegistry] = None,
        node_registry: Optional[NodeRegistry] = None,
    ):
        if provider_registry is None or node_registry is None:
            provider_registry, node_registry = create_default_registries()

        self.provider_registry = provider_registry
        self.node_registry = node_registry

        self._graph = StateGraph(GraphState)
        self._nodes_added: list[str] = []
        self._entry_point: Optional[str] = None

    def add_node(self, name: str) -> "WorkflowBuilder":
        """
        Add a node to the workflow.

        Args:
            name: Node name (must be registered in NodeRegistry)

        Returns:
            Self for chaining
        """
        if not self.node_registry.has(name):
            raise ValueError(f"Node not registered: {name}")

        node_instance = self.node_registry.create(name)
        self._graph.add_node(name, node_instance)
        self._nodes_added.append(name)

        logger.debug(f"Added node to workflow: {name}")
        return self

    def add_edge(self, from_node: str, to_node: str) -> "WorkflowBuilder":
        """
        Add an edge between nodes.

        Args:
            from_node: Source node name
            to_node: Target node name (or END)

        Returns:
            Self for chaining
        """
        if to_node == "END":
            self._graph.add_edge(from_node, END)
        else:
            self._graph.add_edge(from_node, to_node)

        logger.debug(f"Added edge: {from_node} -> {to_node}")
        return self

    def set_entry_point(self, name: str) -> "WorkflowBuilder":
        """
        Set the workflow entry point.

        Args:
            name: Entry node name

        Returns:
            Self for chaining
        """
        self._graph.set_entry_point(name)
        self._entry_point = name

        logger.debug(f"Set entry point: {name}")
        return self

    def add_conditional_edge(
        self,
        from_node: str,
        condition: Callable[[GraphState], str],
        mapping: dict[str, str],
    ) -> "WorkflowBuilder":
        """
        Add a conditional edge.

        Args:
            from_node: Source node
            condition: Function that returns next node name based on state
            mapping: Map of condition results to node names

        Returns:
            Self for chaining
        """
        self._graph.add_conditional_edges(from_node, condition, mapping)
        return self

    def build(self) -> Any:
        """
        Build and compile the workflow.

        Returns:
            Compiled LangGraph workflow
        """
        if not self._entry_point:
            raise ValueError("Entry point not set")

        workflow = self._graph.compile()
        logger.info(
            f"Built workflow with {len(self._nodes_added)} nodes, "
            f"entry: {self._entry_point}"
        )
        return workflow


def create_default_workflow() -> Any:
    """
    Create the default Fingent analysis workflow.

    Workflow structure:
    bootstrap -> macro_auditor -> cross_asset -> news_impact -> synthesize_alert -> END

    Returns:
        Compiled LangGraph workflow
    """
    builder = WorkflowBuilder()

    # Add nodes
    builder.add_node("bootstrap")
    builder.add_node("macro_auditor")
    builder.add_node("cross_asset")
    builder.add_node("news_impact")
    builder.add_node("synthesize_alert")

    # Set entry point
    builder.set_entry_point("bootstrap")

    # Add edges (linear flow for MVP)
    builder.add_edge("bootstrap", "macro_auditor")
    builder.add_edge("macro_auditor", "cross_asset")
    builder.add_edge("cross_asset", "news_impact")
    builder.add_edge("news_impact", "synthesize_alert")
    builder.add_edge("synthesize_alert", "END")

    return builder.build()


def run_workflow(
    workflow: Optional[Any] = None,
    initial_state: Optional[GraphState] = None,
) -> GraphState:
    """
    Execute the workflow.

    Args:
        workflow: Compiled workflow (creates default if not provided)
        initial_state: Initial state (creates empty if not provided)

    Returns:
        Final state after workflow execution
    """
    if workflow is None:
        workflow = create_default_workflow()

    if initial_state is None:
        initial_state = create_initial_state()

    logger.info("Starting workflow execution")

    # Execute workflow
    final_state = workflow.invoke(initial_state)

    logger.info(
        f"Workflow completed. "
        f"Signals: {len(final_state.get('signals', []))}, "
        f"Alerts: {len(final_state.get('alerts', []))}, "
        f"Errors: {len(final_state.get('errors', []))}"
    )

    return final_state
