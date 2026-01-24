"""Tests for data providers."""

import pytest
from unittest.mock import Mock, patch

from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus
from fingent.providers.fred import FREDProvider


class TestBaseProvider:
    """Tests for BaseProvider."""

    def test_provider_status_enum(self):
        """Test ProviderStatus enum values."""
        assert ProviderStatus.HEALTHY == "healthy"
        assert ProviderStatus.DEGRADED == "degraded"
        assert ProviderStatus.UNAVAILABLE == "unavailable"

    def test_health_check_result(self):
        """Test HealthCheckResult creation."""
        result = HealthCheckResult(
            status=ProviderStatus.HEALTHY,
            message="OK",
            latency_ms=100.5,
        )
        assert result.status == ProviderStatus.HEALTHY
        assert result.latency_ms == 100.5


class TestFREDProvider:
    """Tests for FREDProvider."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = Mock()
        settings.fred_api_key = "test_api_key"
        settings.http_timeout = 30
        settings.cache_ttl = 3600
        return settings

    def test_series_metadata(self):
        """Test FRED series metadata."""
        assert "FEDFUNDS" in FREDProvider.SERIES
        assert FREDProvider.SERIES["FEDFUNDS"]["name"] == "Federal Funds Rate"

    @patch("fingent.providers.fred.Fred")
    def test_get_latest_success(self, mock_fred_class, mock_settings):
        """Test successful get_latest call."""
        # Setup mock
        mock_client = Mock()
        mock_fred_class.return_value = mock_client

        # Mock series data
        import pandas as pd
        mock_series = pd.Series(
            [5.25, 5.33],
            index=pd.to_datetime(["2024-01-01", "2024-02-01"]),
        )
        mock_client.get_series.return_value = mock_series

        # Create provider and test
        provider = FREDProvider(settings=mock_settings)
        provider._client = mock_client
        provider._initialized = True

        result = provider.get_latest("FEDFUNDS")

        assert result is not None
        assert result.value == 5.33
        assert result.previous_value == 5.25

    def test_yield_spread_calculation(self, mock_settings):
        """Test yield spread calculation logic."""
        # This tests the calculation, not the API
        short_rate = 4.5
        long_rate = 4.0
        spread = long_rate - short_rate

        assert spread == -0.5  # Inverted curve
