"""
Base provider class for all data adapters.

All providers must:
- Inherit from BaseProvider
- Implement required abstract methods
- Handle errors gracefully (don't crash the pipeline)
- Return domain models, not raw API responses
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from fingent.core.cache import CacheManager, get_provider_cache
from fingent.core.config import Settings, get_settings
from fingent.core.errors import ProviderError, DataNotAvailableError, QuotaExceededError
from fingent.core.http import HttpClient, get_http_client
from fingent.core.logging import LoggerMixin
from fingent.core.quota import get_quota_manager


class ProviderStatus(str, Enum):
    """Provider health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass
class HealthCheckResult:
    """Result of a provider health check."""
    status: ProviderStatus
    message: str
    latency_ms: Optional[float] = None
    details: Optional[dict[str, Any]] = None


class BaseProvider(ABC, LoggerMixin):
    """
    Abstract base class for all data providers.

    Subclasses must implement:
    - name: Provider identifier
    - healthcheck(): Check if provider is available
    - Any data fetching methods specific to the provider

    Features provided by base class:
    - HTTP client with retry/timeout
    - Caching
    - Logging
    - Error handling patterns
    """

    name: str = "base"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        http_client: Optional[HttpClient] = None,
        cache: Optional[CacheManager] = None,
    ):
        """
        Initialize provider.

        Args:
            settings: Application settings. Uses global if not provided.
            http_client: HTTP client. Uses global if not provided.
            cache: Cache manager. Uses global provider cache if not provided.
        """
        self.settings = settings or get_settings()
        self.http = http_client or get_http_client()
        self.cache = cache or get_provider_cache(self.name)

        self._initialized = False
        self._last_error: Optional[Exception] = None
        self._quota_manager = get_quota_manager()

    def _ensure_initialized(self) -> None:
        """Ensure provider is initialized. Override in subclass if needed."""
        if not self._initialized:
            self._initialize()
            self._initialized = True

    def _initialize(self) -> None:
        """
        Perform any initialization logic.

        Override in subclass for API-specific setup.
        """
        pass

    @abstractmethod
    def healthcheck(self) -> HealthCheckResult:
        """
        Check if the provider is healthy.

        Returns:
            HealthCheckResult with status and details
        """
        pass

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get value from cache with provider prefix."""
        full_key = f"{self.name}:{key}"
        return self.cache.get(full_key)

    def _set_cached(self, key: str, value: Any) -> None:
        """Set value in cache with provider prefix."""
        full_key = f"{self.name}:{key}"
        self.cache.set(full_key, value)

    def _handle_error(
        self,
        error: Exception,
        operation: str,
        recoverable: bool = True,
    ) -> None:
        """
        Handle and log provider errors.

        Args:
            error: The exception that occurred
            operation: Description of the operation that failed
            recoverable: Whether the error is recoverable
        """
        self._last_error = error
        self.logger.error(
            f"Provider {self.name} error during {operation}: {error}",
            exc_info=True,
        )

        if not recoverable:
            raise ProviderError(
                f"{self.name}: {operation} failed - {error}",
                provider=self.name,
                recoverable=False,
            )

    def _make_request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Make HTTP request with error handling.

        Args:
            method: HTTP method (get/post)
            url: Request URL
            **kwargs: Additional arguments for request

        Returns:
            Response as dict

        Raises:
            ProviderError: On request failure
        """
        self._consume_quota()
        kwargs["provider_name"] = self.name

        try:
            if method.lower() == "get":
                return self.http.get(url, **kwargs)
            elif method.lower() == "post":
                return self.http.post(url, **kwargs)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(
                f"Request failed: {e}",
                provider=self.name,
                recoverable=True,
            ) from e

    def _consume_quota(self, cost: int = 1) -> None:
        """Consume quota for this provider if limits are enabled."""
        result = self._quota_manager.check_and_consume(self.name, cost=cost)
        if not result.allowed:
            raise QuotaExceededError(
                f"{self.name} quota exceeded: {result.reason}",
                provider=self.name,
            )


class OptionalProvider(BaseProvider):
    """
    Base class for optional providers that can gracefully degrade.

    If the provider is unavailable, it returns empty results instead of errors.
    Use this for non-critical data sources like Polymarket.
    """

    def __init__(self, *args, enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        """Check if provider is enabled."""
        return self._enabled

    def disable(self) -> None:
        """Disable this provider."""
        self._enabled = False
        self.logger.warning(f"Provider {self.name} has been disabled")

    def enable(self) -> None:
        """Enable this provider."""
        self._enabled = True
        self.logger.info(f"Provider {self.name} has been enabled")

    def safe_fetch(self, fetch_func, *args, default=None, **kwargs):
        """
        Safely execute a fetch operation with fallback.

        Args:
            fetch_func: The fetch method to call
            *args: Arguments for fetch_func
            default: Default value if fetch fails
            **kwargs: Keyword arguments for fetch_func

        Returns:
            Fetch result or default value
        """
        if not self.is_enabled:
            self.logger.debug(f"Provider {self.name} is disabled, returning default")
            return default

        try:
            return fetch_func(*args, **kwargs)
        except Exception as e:
            self.logger.warning(
                f"Provider {self.name} fetch failed, using default: {e}"
            )
            return default
