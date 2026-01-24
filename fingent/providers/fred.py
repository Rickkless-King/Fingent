"""
FRED (Federal Reserve Economic Data) provider.

Provides macroeconomic indicators like:
- Federal Funds Rate
- Treasury Yields (2Y, 10Y)
- CPI / Inflation
- Unemployment Rate
- Nonfarm Payrolls
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fredapi import Fred

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import MacroIndicator
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class FREDProvider(BaseProvider):
    """
    FRED data provider for macroeconomic indicators.

    Uses the fredapi library for data access.
    """

    name = "fred"

    # Common FRED series
    SERIES = {
        # Interest rates
        "FEDFUNDS": {"name": "Federal Funds Rate", "unit": "percent", "frequency": "monthly"},
        "DGS10": {"name": "10-Year Treasury Rate", "unit": "percent", "frequency": "daily"},
        "DGS2": {"name": "2-Year Treasury Rate", "unit": "percent", "frequency": "daily"},
        "DGS30": {"name": "30-Year Treasury Rate", "unit": "percent", "frequency": "daily"},

        # Inflation
        "CPIAUCSL": {"name": "CPI All Items", "unit": "index", "frequency": "monthly"},
        "CPILFESL": {"name": "Core CPI", "unit": "index", "frequency": "monthly"},
        "PCEPI": {"name": "PCE Price Index", "unit": "index", "frequency": "monthly"},

        # Employment
        "UNRATE": {"name": "Unemployment Rate", "unit": "percent", "frequency": "monthly"},
        "PAYEMS": {"name": "Nonfarm Payrolls", "unit": "thousands", "frequency": "monthly"},
        "ICSA": {"name": "Initial Jobless Claims", "unit": "number", "frequency": "weekly"},

        # GDP
        "GDP": {"name": "GDP", "unit": "billions", "frequency": "quarterly"},
        "GDPC1": {"name": "Real GDP", "unit": "billions", "frequency": "quarterly"},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client: Optional[Fred] = None

    def _initialize(self) -> None:
        """Initialize FRED API client."""
        api_key = self.settings.fred_api_key
        if not api_key:
            raise ProviderError(
                "FRED API key not configured",
                provider=self.name,
                recoverable=False,
            )
        self._client = Fred(api_key=api_key)
        self.logger.info("FRED provider initialized")

    @property
    def client(self) -> Fred:
        """Get FRED client, initializing if needed."""
        self._ensure_initialized()
        return self._client

    def healthcheck(self) -> HealthCheckResult:
        """Check FRED API health by fetching a simple series."""
        start_time = time.time()

        try:
            # Fetch a simple, always-available series
            self.client.get_series("DGS10", limit=1)
            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="FRED API is responding",
                latency_ms=latency,
            )
        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"FRED API error: {e}",
            )

    def get_series(
        self,
        series_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Fetch raw series data from FRED.

        Args:
            series_id: FRED series ID (e.g., "FEDFUNDS")
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            limit: Max observations to return

        Returns:
            List of {date, value} dicts
        """
        cache_key = f"series:{series_id}:{start_date}:{end_date}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            series = self.client.get_series(
                series_id,
                observation_start=start_date,
                observation_end=end_date,
            )

            # Convert to list of dicts
            data = []
            for date, value in series.tail(limit).items():
                if value is not None and not (isinstance(value, float) and value != value):
                    data.append({
                        "date": date.strftime("%Y-%m-%d"),
                        "value": float(value),
                    })

            self._set_cached(cache_key, data)
            return data

        except Exception as e:
            self._handle_error(e, f"get_series({series_id})")
            raise DataNotAvailableError(
                f"Failed to fetch series {series_id}",
                provider=self.name,
            ) from e

    def get_latest(self, series_id: str) -> Optional[MacroIndicator]:
        """
        Get the latest value for a series.

        Args:
            series_id: FRED series ID

        Returns:
            MacroIndicator with latest value, or None if unavailable
        """
        cache_key = f"latest:{series_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return MacroIndicator.from_dict(cached)

        try:
            data = self.get_series(series_id, limit=2)
            if not data:
                return None

            latest = data[-1]
            previous = data[-2] if len(data) > 1 else None

            series_info = self.SERIES.get(series_id, {})

            indicator = MacroIndicator(
                series_id=series_id,
                name=series_info.get("name", series_id),
                value=latest["value"],
                previous_value=previous["value"] if previous else None,
                change=latest["value"] - previous["value"] if previous else None,
                unit=series_info.get("unit", ""),
                frequency=series_info.get("frequency", ""),
                timestamp=latest["date"],
                source=self.name,
            )

            self._set_cached(cache_key, indicator.to_dict())
            return indicator

        except Exception as e:
            self.logger.warning(f"Failed to get latest {series_id}: {e}")
            return None

    def get_macro_snapshot(
        self,
        series_ids: Optional[list[str]] = None,
    ) -> dict[str, MacroIndicator]:
        """
        Get a snapshot of multiple macro indicators.

        Args:
            series_ids: List of series to fetch. Uses defaults if not provided.

        Returns:
            Dict mapping series_id to MacroIndicator
        """
        if series_ids is None:
            # Default key indicators
            series_ids = ["FEDFUNDS", "DGS10", "DGS2", "CPIAUCSL", "UNRATE"]

        results = {}
        for series_id in series_ids:
            indicator = self.get_latest(series_id)
            if indicator:
                results[series_id] = indicator

        return results

    def get_yield_spread(
        self,
        short_term: str = "DGS2",
        long_term: str = "DGS10",
    ) -> Optional[float]:
        """
        Calculate yield spread (e.g., 2Y-10Y).

        Args:
            short_term: Short-term rate series ID
            long_term: Long-term rate series ID

        Returns:
            Spread in percentage points, or None if unavailable
        """
        try:
            short = self.get_latest(short_term)
            long = self.get_latest(long_term)

            if short and long:
                return long.value - short.value
            return None

        except Exception as e:
            self.logger.warning(f"Failed to calculate yield spread: {e}")
            return None

    def get_inflation_metrics(self) -> dict[str, Any]:
        """
        Get inflation-related metrics.

        Returns:
            Dict with CPI, Core CPI, and calculated YoY changes
        """
        try:
            # Get CPI data with more history for YoY calculation
            cpi_data = self.get_series("CPIAUCSL", limit=13)  # 13 months for YoY
            core_cpi_data = self.get_series("CPILFESL", limit=13)

            result = {}

            if len(cpi_data) >= 13:
                current = cpi_data[-1]["value"]
                year_ago = cpi_data[0]["value"]
                yoy_change = (current - year_ago) / year_ago
                result["cpi_yoy"] = round(yoy_change * 100, 2)  # As percentage
                result["cpi_current"] = current

            if len(core_cpi_data) >= 13:
                current = core_cpi_data[-1]["value"]
                year_ago = core_cpi_data[0]["value"]
                yoy_change = (current - year_ago) / year_ago
                result["core_cpi_yoy"] = round(yoy_change * 100, 2)
                result["core_cpi_current"] = current

            return result

        except Exception as e:
            self.logger.warning(f"Failed to get inflation metrics: {e}")
            return {}
