"""
Caching utilities for Fingent.

Provides TTL-based caching for API responses to reduce API calls.
"""

import hashlib
import json
import time
from functools import wraps
from typing import Any, Callable, Optional

from cachetools import TTLCache

from fingent.core.config import get_settings, load_yaml_config
from fingent.core.logging import get_logger

logger = get_logger("cache")


class CacheManager:
    """
    TTL-based cache manager.

    Features:
    - Configurable TTL per cache
    - Key generation from function args
    - Manual invalidation
    """

    def __init__(self, maxsize: int = 1000, ttl: Optional[int] = None):
        settings = get_settings()
        self.ttl = ttl or settings.cache_ttl
        self._cache = TTLCache(maxsize=maxsize, ttl=self.ttl)
        self._stats = {"hits": 0, "misses": 0}

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        value = self._cache.get(key)
        if value is not None:
            self._stats["hits"] += 1
            logger.debug(f"Cache hit: {key}")
        else:
            self._stats["misses"] += 1
            logger.debug(f"Cache miss: {key}")
        return value

    def set(self, key: str, value: Any) -> None:
        """Set value in cache."""
        self._cache[key] = value
        logger.debug(f"Cache set: {key}")

    def delete(self, key: str) -> None:
        """Delete key from cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cached values."""
        self._cache.clear()
        logger.info("Cache cleared")

    @property
    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0
        return {
            **self._stats,
            "total": total,
            "hit_rate": round(hit_rate, 3),
            "size": len(self._cache),
        }


def make_cache_key(*args, **kwargs) -> str:
    """Generate a cache key from function arguments."""
    key_parts = [str(arg) for arg in args]
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key_str = ":".join(key_parts)
    return hashlib.md5(key_str.encode()).hexdigest()


def cached(
    cache: Optional[CacheManager] = None,
    ttl: Optional[int] = None,
    key_prefix: str = "",
) -> Callable:
    """
    Decorator for caching function results.

    Args:
        cache: CacheManager instance. Creates new one if not provided.
        ttl: Override TTL for this cache
        key_prefix: Prefix for cache keys

    Example:
        @cached(ttl=300)
        def fetch_data(symbol: str) -> dict:
            ...
    """
    _cache = cache or CacheManager(ttl=ttl)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key = f"{key_prefix}:{func.__name__}:{make_cache_key(*args, **kwargs)}"

            # Try to get from cache
            result = _cache.get(key)
            if result is not None:
                return result

            # Execute function and cache result
            result = func(*args, **kwargs)
            if result is not None:
                _cache.set(key, result)
            return result

        # Expose cache for manual operations
        wrapper.cache = _cache
        return wrapper

    return decorator


# Global cache instances for different purposes
_provider_cache: Optional[CacheManager] = None
_provider_caches: dict[str, CacheManager] = {}
_llm_cache: Optional[CacheManager] = None


def _get_provider_ttl(provider_name: Optional[str]) -> int:
    settings = get_settings()
    config = load_yaml_config()
    usage_mode = config.get("usage_mode", {})
    cache_cfg = usage_mode.get("cache_ttl", {})
    default_ttl = cache_cfg.get("default", settings.cache_ttl)
    if provider_name:
        provider_ttl = cache_cfg.get("providers", {}).get(provider_name)
        if provider_ttl:
            return int(provider_ttl)
    return int(default_ttl)


def get_provider_cache(provider_name: Optional[str] = None) -> CacheManager:
    """Get cache for provider API responses (supports per-provider TTL)."""
    global _provider_cache
    if not provider_name:
        if _provider_cache is None:
            settings = get_settings()
            _provider_cache = CacheManager(maxsize=500, ttl=settings.cache_ttl)
        return _provider_cache

    ttl = _get_provider_ttl(provider_name)
    existing = _provider_caches.get(provider_name)
    if existing and existing.ttl == ttl:
        return existing

    cache = CacheManager(maxsize=500, ttl=ttl)
    _provider_caches[provider_name] = cache
    return cache


def get_llm_cache() -> CacheManager:
    """Get cache for LLM responses (longer TTL)."""
    global _llm_cache
    if _llm_cache is None:
        # LLM responses cached for 1 hour
        _llm_cache = CacheManager(maxsize=100, ttl=3600)
    return _llm_cache
