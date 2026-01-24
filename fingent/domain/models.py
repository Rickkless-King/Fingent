"""
Core data models for Fingent.

All models use dataclass and provide to_dict() for JSON serialization.
These models represent the domain objects without external API dependencies.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional
from enum import Enum


class AssetType(str, Enum):
    """Asset type enumeration."""
    US_EQUITY = "us_equity"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    BOND = "bond"
    INDEX = "index"
    FX = "fx"


@dataclass
class MacroIndicator:
    """
    Macro economic indicator data point.

    Example: Federal Funds Rate, CPI, Unemployment Rate
    """
    series_id: str          # FRED series ID, e.g., "FEDFUNDS"
    name: str               # Human-readable name
    value: float            # Current value
    previous_value: Optional[float] = None
    change: Optional[float] = None  # Change from previous
    unit: str = ""          # e.g., "percent", "index"
    frequency: str = ""     # e.g., "monthly", "daily"
    timestamp: Optional[str] = None
    source: str = "fred"

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MacroIndicator":
        """Create from dict."""
        return cls(**data)


@dataclass
class PriceBar:
    """
    Price data for a single time period.

    Represents OHLCV (Open, High, Low, Close, Volume) data.
    """
    symbol: str
    timestamp: str          # ISO format
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    asset_type: str = "us_equity"

    # Calculated fields
    change_24h: Optional[float] = None  # 24h percentage change
    change_7d: Optional[float] = None   # 7d percentage change

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceBar":
        return cls(**data)


@dataclass
class MarketData:
    """
    Aggregated market data for an asset.

    Contains current price and calculated metrics.
    """
    symbol: str
    name: str
    asset_type: str
    price: float
    timestamp: str

    # Price changes
    change_24h: Optional[float] = None  # Percentage
    change_7d: Optional[float] = None   # Percentage

    # Additional metrics
    volume_24h: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None

    # For indices like VIX
    level: Optional[float] = None

    # Data source
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketData":
        return cls(**data)


@dataclass
class NewsItem:
    """
    A single news article with sentiment analysis.
    """
    title: str
    summary: str
    url: str
    published_at: str       # ISO timestamp
    source: str             # News source name

    # Sentiment (from API or LLM)
    sentiment_label: Optional[str] = None   # bullish/bearish/neutral
    sentiment_score: Optional[float] = None  # -1 to 1

    # Relevance
    tickers: list[str] = field(default_factory=list)  # Related tickers
    topics: list[str] = field(default_factory=list)   # e.g., ["fed", "inflation"]

    # Provider info
    provider: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        return cls(**data)


@dataclass
class SentimentData:
    """
    Aggregated sentiment data from prediction markets or other sources.
    """
    source: str             # e.g., "polymarket"
    market_id: str
    question: str           # e.g., "Will Fed raise rates in March?"

    # Odds/probabilities
    yes_price: float        # 0-1 probability
    no_price: float

    # Changes
    change_1h: Optional[float] = None
    change_24h: Optional[float] = None

    volume: Optional[float] = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SentimentData":
        return cls(**data)


@dataclass
class CrossAssetSnapshot:
    """
    Snapshot of cross-asset market conditions.

    Used for correlation and divergence analysis.
    """
    timestamp: str
    assets: dict[str, MarketData] = field(default_factory=dict)

    # Calculated correlations/divergences
    btc_gold_correlation: Optional[float] = None
    risk_on_score: Optional[float] = None  # -1 (risk off) to 1 (risk on)

    # Yield curve
    yield_spread_2y10y: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "timestamp": self.timestamp,
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "btc_gold_correlation": self.btc_gold_correlation,
            "risk_on_score": self.risk_on_score,
            "yield_spread_2y10y": self.yield_spread_2y10y,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CrossAssetSnapshot":
        assets = {k: MarketData.from_dict(v) for k, v in data.get("assets", {}).items()}
        return cls(
            timestamp=data["timestamp"],
            assets=assets,
            btc_gold_correlation=data.get("btc_gold_correlation"),
            risk_on_score=data.get("risk_on_score"),
            yield_spread_2y10y=data.get("yield_spread_2y10y"),
        )
