"""
Registry for providers and nodes.

Enables plugin-style registration and dependency injection.
"""

from typing import Any, Callable, Dict, Optional, Type

from fingent.core.config import Settings, get_settings, load_yaml_config
from fingent.core.logging import get_logger

logger = get_logger("registry")


class ProviderRegistry:
    """
    Registry for data providers.

    Allows registering providers by name and retrieving instances.
    Supports lazy initialization and singleton pattern.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._providers: Dict[str, Type] = {}
        self._instances: Dict[str, Any] = {}

    def register(self, name: str, provider_class: Type) -> None:
        """
        Register a provider class.

        Args:
            name: Provider name (e.g., "fred", "finnhub")
            provider_class: Provider class (not instance)
        """
        self._providers[name] = provider_class
        logger.debug(f"Registered provider: {name}")

    def get(self, name: str, **kwargs) -> Any:
        """
        Get a provider instance.

        Creates instance on first access (lazy initialization).
        Returns cached instance on subsequent calls.

        Args:
            name: Provider name
            **kwargs: Additional arguments for provider constructor

        Returns:
            Provider instance

        Raises:
            KeyError: If provider not registered
        """
        if name not in self._providers:
            raise KeyError(f"Provider not registered: {name}")

        if name not in self._instances:
            provider_class = self._providers[name]
            self._instances[name] = provider_class(
                settings=self.settings,
                **kwargs,
            )
            logger.debug(f"Created provider instance: {name}")

        return self._instances[name]

    def has(self, name: str) -> bool:
        """Check if provider is registered."""
        return name in self._providers

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())

    def clear_instances(self) -> None:
        """Clear cached instances (for testing)."""
        self._instances.clear()


class NodeRegistry:
    """
    Registry for LangGraph nodes.

    Allows registering nodes by name and creating instances with dependencies.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        config: Optional[dict] = None,
        provider_registry: Optional[ProviderRegistry] = None,
    ):
        self.settings = settings or get_settings()
        self.config = config or load_yaml_config()
        self.provider_registry = provider_registry or ProviderRegistry(self.settings)

        self._nodes: Dict[str, Type] = {}
        self._node_providers: Dict[str, list[str]] = {}  # Node -> required providers

    def register(
        self,
        name: str,
        node_class: Type,
        providers: Optional[list[str]] = None,
    ) -> None:
        """
        Register a node class.

        Args:
            name: Node name (e.g., "macro_auditor")
            node_class: Node class (not instance)
            providers: List of required provider names
        """
        self._nodes[name] = node_class
        self._node_providers[name] = providers or []
        logger.debug(f"Registered node: {name}")

    def create(self, name: str, **kwargs) -> Any:
        """
        Create a node instance with injected dependencies.

        Args:
            name: Node name
            **kwargs: Additional arguments for node constructor

        Returns:
            Node instance

        Raises:
            KeyError: If node not registered
        """
        if name not in self._nodes:
            raise KeyError(f"Node not registered: {name}")

        node_class = self._nodes[name]
        required_providers = self._node_providers.get(name, [])

        # Inject providers
        provider_kwargs = {}
        for provider_name in required_providers:
            if self.provider_registry.has(provider_name):
                # Convention: provider_name + "_provider" as kwarg
                kwarg_name = f"{provider_name}_provider"
                provider_kwargs[kwarg_name] = self.provider_registry.get(provider_name)

        # Merge with explicit kwargs
        all_kwargs = {
            "settings": self.settings,
            "config": self.config,
            **provider_kwargs,
            **kwargs,
        }

        instance = node_class(**all_kwargs)
        logger.debug(f"Created node instance: {name}")
        return instance

    def has(self, name: str) -> bool:
        """Check if node is registered."""
        return name in self._nodes

    def list_nodes(self) -> list[str]:
        """List all registered node names."""
        return list(self._nodes.keys())


def create_default_registries() -> tuple[ProviderRegistry, NodeRegistry]:
    """
    Create registries with default providers and nodes registered.

    Returns:
        Tuple of (ProviderRegistry, NodeRegistry)
    """
    from fingent.providers import (
        FREDProvider,
        FinnhubProvider,
        AlphaVantageProvider,
        OKXProvider,
        PolymarketProvider,
    )
    from fingent.nodes import (
        BootstrapNode,
        MacroAuditorNode,
        CrossAssetNode,
        NewsImpactNode,
        SynthesizeAlertNode,
    )

    settings = get_settings()
    config = load_yaml_config()

    # Create provider registry
    provider_registry = ProviderRegistry(settings)
    provider_registry.register("fred", FREDProvider)
    provider_registry.register("finnhub", FinnhubProvider)
    provider_registry.register("alphavantage", AlphaVantageProvider)
    provider_registry.register("okx", OKXProvider)
    provider_registry.register("polymarket", PolymarketProvider)

    # Create node registry
    node_registry = NodeRegistry(settings, config, provider_registry)
    node_registry.register("bootstrap", BootstrapNode, providers=[])
    node_registry.register("macro_auditor", MacroAuditorNode, providers=["fred"])
    node_registry.register("cross_asset", CrossAssetNode, providers=["finnhub", "okx"])
    node_registry.register("news_impact", NewsImpactNode, providers=["alphavantage", "finnhub"])
    node_registry.register("synthesize_alert", SynthesizeAlertNode, providers=[])

    return provider_registry, node_registry
