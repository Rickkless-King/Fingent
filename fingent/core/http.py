"""
HTTP client wrapper with timeout, retry, and rate limiting.

Provides a unified HTTP interface for all providers.
"""

import asyncio
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from fingent.core.config import get_settings
from fingent.core.errors import ProviderError, RateLimitError
from fingent.core.logging import get_logger

logger = get_logger("http")


class HttpClient:
    """
    HTTP client with built-in retry, timeout, and error handling.

    Features:
    - Configurable timeout
    - Exponential backoff retry
    - Rate limit handling
    - Request/response logging
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: int = 3,
        headers: Optional[dict[str, str]] = None,
    ):
        settings = get_settings()
        self.base_url = base_url
        self.timeout = timeout or settings.http_timeout
        self.max_retries = max_retries
        self.default_headers = headers or {}

        self._client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialized sync HTTP client."""
        if self._client is None:
            client_kwargs = {
                "timeout": self.timeout,
                "headers": self.default_headers,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = httpx.Client(**client_kwargs)
        return self._client

    @property
    def async_client(self) -> httpx.AsyncClient:
        """Lazy-initialized async HTTP client."""
        if self._async_client is None:
            client_kwargs = {
                "timeout": self.timeout,
                "headers": self.default_headers,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._async_client = httpx.AsyncClient(**client_kwargs)
        return self._async_client

    def close(self) -> None:
        """Close HTTP clients."""
        if self._client:
            self._client.close()
            self._client = None
        if self._async_client:
            asyncio.get_event_loop().run_until_complete(self._async_client.aclose())
            self._async_client = None

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def get(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        provider_name: str = "unknown",
    ) -> dict[str, Any]:
        """
        Make a GET request with retry logic.

        Args:
            url: Request URL (can be relative if base_url is set)
            params: Query parameters
            headers: Additional headers
            provider_name: Provider name for error reporting

        Returns:
            JSON response as dict

        Raises:
            ProviderError: On request failure
            RateLimitError: On 429 status
        """
        try:
            logger.debug(f"GET {url} params={params}")
            response = self.client.get(url, params=params, headers=headers)
            return self._handle_response(response, provider_name)
        except httpx.TimeoutException as e:
            logger.warning(f"Timeout on GET {url}: {e}")
            raise ProviderError(
                f"Request timeout: {url}",
                provider=provider_name,
                recoverable=True,
            ) from e
        except httpx.NetworkError as e:
            logger.error(f"Network error on GET {url}: {e}")
            raise ProviderError(
                f"Network error: {e}",
                provider=provider_name,
                recoverable=True,
            ) from e

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def post(
        self,
        url: str,
        *,
        json: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        provider_name: str = "unknown",
    ) -> dict[str, Any]:
        """Make a POST request with retry logic."""
        try:
            logger.debug(f"POST {url}")
            response = self.client.post(url, json=json, data=data, headers=headers)
            return self._handle_response(response, provider_name)
        except httpx.TimeoutException as e:
            raise ProviderError(
                f"Request timeout: {url}",
                provider=provider_name,
                recoverable=True,
            ) from e
        except httpx.NetworkError as e:
            raise ProviderError(
                f"Network error: {e}",
                provider=provider_name,
                recoverable=True,
            ) from e

    async def aget(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        provider_name: str = "unknown",
    ) -> dict[str, Any]:
        """Async GET request."""
        try:
            logger.debug(f"Async GET {url} params={params}")
            response = await self.async_client.get(url, params=params, headers=headers)
            return self._handle_response(response, provider_name)
        except httpx.TimeoutException as e:
            raise ProviderError(
                f"Request timeout: {url}",
                provider=provider_name,
                recoverable=True,
            ) from e

    def _handle_response(
        self,
        response: httpx.Response,
        provider_name: str,
    ) -> dict[str, Any]:
        """Handle HTTP response and convert to dict."""
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                "Rate limit exceeded",
                provider=provider_name,
                retry_after=int(retry_after) if retry_after else None,
            )

        if response.status_code >= 400:
            logger.error(f"HTTP {response.status_code}: {response.text[:200]}")
            raise ProviderError(
                f"HTTP {response.status_code}: {response.reason_phrase}",
                provider=provider_name,
                recoverable=response.status_code >= 500,
            )

        try:
            return response.json()
        except Exception:
            # Return text content wrapped in dict
            return {"_raw": response.text}


# Global HTTP client instance
_default_client: Optional[HttpClient] = None


def get_http_client() -> HttpClient:
    """Get the default HTTP client instance."""
    global _default_client
    if _default_client is None:
        _default_client = HttpClient()
    return _default_client
