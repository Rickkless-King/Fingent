"""
Unified exception definitions for Fingent.

All custom exceptions inherit from FingentError for easy catching.
"""

from typing import Any, Optional


class FingentError(Exception):
    """Base exception for all Fingent errors."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or "FINGENT_ERROR"
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for logging/serialization."""
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(FingentError):
    """Configuration-related errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, code="CONFIG_ERROR", **kwargs)


class ProviderError(FingentError):
    """Data provider errors (API failures, rate limits, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        recoverable: bool = True,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        details["provider"] = provider
        details["recoverable"] = recoverable
        super().__init__(message, code="PROVIDER_ERROR", details=details, **kwargs)
        self.provider = provider
        self.recoverable = recoverable


class DataNotAvailableError(ProviderError):
    """Requested data is not available (missing, insufficient history, etc.)."""

    def __init__(self, message: str, *, provider: str, **kwargs):
        super().__init__(message, provider=provider, recoverable=True, **kwargs)
        self.code = "DATA_NOT_AVAILABLE"


class RateLimitError(ProviderError):
    """API rate limit exceeded."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retry_after: Optional[int] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        details["retry_after"] = retry_after
        super().__init__(message, provider=provider, recoverable=True, details=details, **kwargs)
        self.code = "RATE_LIMIT"
        self.retry_after = retry_after


class AuthenticationError(ProviderError):
    """API authentication failed."""

    def __init__(self, message: str, *, provider: str, **kwargs):
        super().__init__(message, provider=provider, recoverable=False, **kwargs)
        self.code = "AUTH_ERROR"


class NodeExecutionError(FingentError):
    """Error during LangGraph node execution."""

    def __init__(
        self,
        message: str,
        *,
        node_name: str,
        cause: Optional[Exception] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        details["node"] = node_name
        if cause:
            details["cause"] = str(cause)
        super().__init__(message, code="NODE_ERROR", details=details, **kwargs)
        self.node_name = node_name
        self.cause = cause


class LLMError(FingentError):
    """LLM invocation errors."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: Optional[str] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        details["llm_provider"] = provider
        details["model"] = model
        super().__init__(message, code="LLM_ERROR", details=details, **kwargs)
        self.provider = provider
        self.model = model
