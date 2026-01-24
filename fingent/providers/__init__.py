"""
Providers module - Data adapters

Each provider wraps an external API and returns domain models.
All providers inherit from BaseProvider for consistent interface.
"""

from fingent.providers.base import BaseProvider, ProviderStatus
from fingent.providers.fred import FREDProvider
from fingent.providers.finnhub import FinnhubProvider
from fingent.providers.alphavantage import AlphaVantageProvider
from fingent.providers.okx import OKXProvider
from fingent.providers.polymarket import PolymarketProvider
from fingent.providers.dbnomics import DBnomicsProvider
from fingent.providers.polygon import PolygonProvider

__all__ = [
    "BaseProvider",
    "ProviderStatus",
    "FREDProvider",
    "FinnhubProvider",
    "AlphaVantageProvider",
    "OKXProvider",
    "PolymarketProvider",
    "DBnomicsProvider",
    "PolygonProvider",
]
