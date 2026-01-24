"""
DBnomics provider for global macroeconomic data.

DBnomics aggregates economic data from 100+ sources including:
- Eurostat, World Bank, IMF, ECB, OECD
- National statistical agencies
- Central banks worldwide

Key features:
- NO API KEY REQUIRED (completely free and open)
- 100+ million time series
- Global coverage (not just US like FRED)

Reference: https://db.nomics.world/
"""

import time
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import MacroIndicator
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class DBnomicsProvider(BaseProvider):
    """
    DBnomics data provider for global macroeconomic indicators.

    Uses the dbnomics Python library for data access.
    No API key required - completely open and free.
    """

    name = "dbnomics"

    # Common DBnomics series organized by provider/dataset/series
    # Format: "provider/dataset/series_id"
    SERIES = {
        # IMF - International Monetary Fund
        "IMF/WEO:2024-10/USA.NGDP_RPCH": {
            "name": "US Real GDP Growth (IMF)",
            "unit": "percent",
            "frequency": "annual",
        },
        "IMF/WEO:2024-10/CHN.NGDP_RPCH": {
            "name": "China Real GDP Growth (IMF)",
            "unit": "percent",
            "frequency": "annual",
        },

        # World Bank
        "WB/WDI/NY.GDP.MKTP.KD.ZG-US": {
            "name": "US GDP Growth (World Bank)",
            "unit": "percent",
            "frequency": "annual",
        },

        # Eurostat
        "Eurostat/prc_hicp_manr/M.RCH_A.CP00.EA": {
            "name": "Eurozone HICP Inflation",
            "unit": "percent",
            "frequency": "monthly",
        },

        # OECD
        "OECD/MEI/USA.CPALTT01.IXOB.M": {
            "name": "US CPI (OECD)",
            "unit": "index",
            "frequency": "monthly",
        },
        "OECD/MEI/USA.LRUN64TT.STSA.M": {
            "name": "US Unemployment Rate (OECD)",
            "unit": "percent",
            "frequency": "monthly",
        },

        # ECB - European Central Bank
        "ECB/FM/M.U2.EUR.4F.KR.MRR_FR.LEV": {
            "name": "ECB Main Refinancing Rate",
            "unit": "percent",
            "frequency": "monthly",
        },

        # BIS - Bank for International Settlements
        "BIS/WS_CBPOL/D.US.N": {
            "name": "Fed Policy Rate (BIS)",
            "unit": "percent",
            "frequency": "daily",
        },
    }

    # Useful dataset mappings for discovery
    DATASETS = {
        "global_gdp": [
            "IMF/WEO:2024-10",  # IMF World Economic Outlook
        ],
        "inflation": [
            "Eurostat/prc_hicp_manr",  # EU Inflation
            "OECD/MEI",  # OECD Main Economic Indicators
        ],
        "central_banks": [
            "BIS/WS_CBPOL",  # BIS Central Bank Policy Rates
            "ECB/FM",  # ECB Financial Markets
        ],
        "trade": [
            "OECD/MEI",  # Trade balance etc.
            "WB/WDI",  # World Development Indicators
        ],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dbnomics = None

    def _initialize(self) -> None:
        """Initialize DBnomics client (lazy import)."""
        try:
            import dbnomics
            self._dbnomics = dbnomics
            self.logger.info("DBnomics provider initialized (no API key required)")
        except ImportError as e:
            raise ProviderError(
                "dbnomics package not installed. Run: pip install dbnomics",
                provider=self.name,
                recoverable=False,
            ) from e

    @property
    def dbnomics(self):
        """Get dbnomics module, initializing if needed."""
        self._ensure_initialized()
        return self._dbnomics

    def healthcheck(self) -> HealthCheckResult:
        """Check DBnomics API health by fetching a simple series."""
        start_time = time.time()

        try:
            # Fetch a simple, always-available series
            df = self.dbnomics.fetch_series("OECD/MEI/USA.CPALTT01.IXOB.M")
            if df is not None and len(df) > 0:
                latency = (time.time() - start_time) * 1000
                return HealthCheckResult(
                    status=ProviderStatus.HEALTHY,
                    message="DBnomics API is responding",
                    latency_ms=latency,
                    details={"rows_fetched": len(df)},
                )
            else:
                return HealthCheckResult(
                    status=ProviderStatus.DEGRADED,
                    message="DBnomics returned empty response",
                )
        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"DBnomics API error: {e}",
            )

    def fetch_series(
        self,
        series_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Fetch a time series from DBnomics.

        Args:
            series_id: Full series ID in format "provider/dataset/series"
                      e.g., "OECD/MEI/USA.CPALTT01.IXOB.M"
            limit: Max observations to return (most recent)

        Returns:
            List of {date, value} dicts
        """
        cache_key = f"series:{series_id}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            df = self.dbnomics.fetch_series(series_id)

            if df is None or df.empty:
                return []

            # DBnomics returns DataFrame with 'period' and 'value' columns
            data = []
            df_recent = df.tail(limit)

            for _, row in df_recent.iterrows():
                period = row.get("period") or row.get("original_period")
                value = row.get("value")

                if period is not None and value is not None and pd.notna(value):
                    # Convert period to string date
                    if isinstance(period, pd.Timestamp):
                        date_str = period.strftime("%Y-%m-%d")
                    else:
                        date_str = str(period)

                    data.append({
                        "date": date_str,
                        "value": float(value),
                    })

            self._set_cached(cache_key, data)
            return data

        except Exception as e:
            self._handle_error(e, f"fetch_series({series_id})")
            raise DataNotAvailableError(
                f"Failed to fetch series {series_id}",
                provider=self.name,
            ) from e

    def get_latest(self, series_id: str) -> Optional[MacroIndicator]:
        """
        Get the latest value for a series.

        Args:
            series_id: Full DBnomics series ID

        Returns:
            MacroIndicator with latest value, or None if unavailable
        """
        cache_key = f"latest:{series_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return MacroIndicator.from_dict(cached)

        try:
            data = self.fetch_series(series_id, limit=2)
            if not data:
                return None

            latest = data[-1]
            previous = data[-2] if len(data) > 1 else None

            series_info = self.SERIES.get(series_id, {})

            indicator = MacroIndicator(
                series_id=series_id,
                name=series_info.get("name", series_id.split("/")[-1]),
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

    def search_series(
        self,
        query: str,
        provider: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search for series in DBnomics.

        Args:
            query: Search query (e.g., "US GDP", "inflation eurozone")
            provider: Filter by provider (e.g., "IMF", "OECD")
            limit: Max results to return

        Returns:
            List of matching series metadata
        """
        try:
            # Use the search functionality
            results = self.dbnomics.fetch_series_by_api_link(
                f"https://api.db.nomics.world/v22/series?q={query}&limit={limit}"
                + (f"&provider_code={provider}" if provider else "")
            )

            if results is None or results.empty:
                return []

            # Extract unique series info
            series_list = []
            seen = set()

            for _, row in results.iterrows():
                series_code = row.get("series_code", "")
                if series_code and series_code not in seen:
                    seen.add(series_code)
                    series_list.append({
                        "series_id": f"{row.get('provider_code', '')}/{row.get('dataset_code', '')}/{series_code}",
                        "name": row.get("series_name", series_code),
                        "provider": row.get("provider_code", ""),
                        "dataset": row.get("dataset_code", ""),
                    })

            return series_list[:limit]

        except Exception as e:
            self.logger.warning(f"Series search failed: {e}")
            return []

    def get_global_inflation(self) -> dict[str, MacroIndicator]:
        """
        Get inflation data for major economies.

        Returns:
            Dict mapping country code to MacroIndicator
        """
        inflation_series = {
            "US": "OECD/MEI/USA.CPALTT01.IXOB.M",
            "EU": "Eurostat/prc_hicp_manr/M.RCH_A.CP00.EA",
            "JP": "OECD/MEI/JPN.CPALTT01.IXOB.M",
            "UK": "OECD/MEI/GBR.CPALTT01.IXOB.M",
            "CN": "OECD/MEI/CHN.CPALTT01.IXOB.M",
        }

        results = {}
        for country, series_id in inflation_series.items():
            try:
                indicator = self.get_latest(series_id)
                if indicator:
                    results[country] = indicator
            except Exception as e:
                self.logger.warning(f"Failed to get inflation for {country}: {e}")

        return results

    def get_central_bank_rates(self) -> dict[str, MacroIndicator]:
        """
        Get central bank policy rates for major economies.

        Returns:
            Dict mapping bank code to MacroIndicator
        """
        rate_series = {
            "FED": "BIS/WS_CBPOL/D.US.N",
            "ECB": "ECB/FM/M.U2.EUR.4F.KR.MRR_FR.LEV",
            "BOJ": "BIS/WS_CBPOL/D.JP.N",
            "BOE": "BIS/WS_CBPOL/D.GB.N",
            "PBOC": "BIS/WS_CBPOL/D.CN.N",
        }

        results = {}
        for bank, series_id in rate_series.items():
            try:
                indicator = self.get_latest(series_id)
                if indicator:
                    indicator.name = f"{bank} Policy Rate"
                    results[bank] = indicator
            except Exception as e:
                self.logger.warning(f"Failed to get rate for {bank}: {e}")

        return results

    def get_global_gdp_growth(self) -> dict[str, MacroIndicator]:
        """
        Get GDP growth rates for major economies from IMF WEO.

        Returns:
            Dict mapping country code to MacroIndicator
        """
        gdp_series = {
            "US": "IMF/WEO:2024-10/USA.NGDP_RPCH",
            "CN": "IMF/WEO:2024-10/CHN.NGDP_RPCH",
            "JP": "IMF/WEO:2024-10/JPN.NGDP_RPCH",
            "DE": "IMF/WEO:2024-10/DEU.NGDP_RPCH",
            "UK": "IMF/WEO:2024-10/GBR.NGDP_RPCH",
            "FR": "IMF/WEO:2024-10/FRA.NGDP_RPCH",
            "IN": "IMF/WEO:2024-10/IND.NGDP_RPCH",
        }

        results = {}
        for country, series_id in gdp_series.items():
            try:
                indicator = self.get_latest(series_id)
                if indicator:
                    indicator.name = f"{country} Real GDP Growth"
                    results[country] = indicator
            except Exception as e:
                self.logger.warning(f"Failed to get GDP growth for {country}: {e}")

        return results

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
            series_ids = [
                "BIS/WS_CBPOL/D.US.N",  # Fed rate
                "OECD/MEI/USA.CPALTT01.IXOB.M",  # US CPI
                "OECD/MEI/USA.LRUN64TT.STSA.M",  # US Unemployment
            ]

        results = {}
        for series_id in series_ids:
            indicator = self.get_latest(series_id)
            if indicator:
                results[series_id] = indicator

        return results
