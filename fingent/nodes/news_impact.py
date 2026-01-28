"""
News Impact node - Analyzes news sentiment.

Data sources: AlphaVantage (primary), Finnhub (fallback)
Signals produced: sentiment_bullish, sentiment_bearish, sentiment_mixed
"""

from typing import Any, Optional

from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.signals import SignalDirection, SignalName, create_signal
from fingent.nodes.base import BaseNode
from fingent.providers.alphavantage import AlphaVantageProvider
from fingent.providers.finnhub import FinnhubProvider


class NewsImpactNode(BaseNode):
    """
    News sentiment analysis node.

    Uses AlphaVantage NEWS_SENTIMENT API for sentiment analysis.
    Falls back to Finnhub news if AlphaVantage is unavailable.
    """

    node_name = "news_impact"

    def __init__(
        self,
        *args,
        alphavantage_provider: Optional[AlphaVantageProvider] = None,
        finnhub_provider: Optional[FinnhubProvider] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.alphavantage = alphavantage_provider or AlphaVantageProvider()
        self.finnhub = finnhub_provider or FinnhubProvider()
        self._load_news_config()

    def _load_news_config(self) -> None:
        news_cfg = self.config.get("providers", {}).get("news", {})
        self.news_primary = news_cfg.get("primary", "alphavantage")
        self.news_fallback = news_cfg.get("fallback", "finnhub")

    def _get_news_provider(self, name: str):
        if name == "alphavantage":
            return self.alphavantage
        if name == "finnhub":
            return self.finnhub
        return None

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Analyze news sentiment.

        Returns:
            State update with news_data and signals
        """
        run_id = self.get_run_id(state)
        existing_signals = self.get_existing_signals(state)
        errors = []
        signals = []

        # Fetch news data
        news_data = self._fetch_news_data(errors)

        # Analyze and produce signals
        if news_data.get("articles"):
            signals = self._analyze_sentiment(news_data, run_id)

        # Merge signals
        all_signals = self.merge_signals(existing_signals, signals)

        return {
            "news_data": news_data,
            "signals": all_signals,
            "errors": state.get("errors", []) + errors,
        }

    def _fetch_news_data(self, errors: list) -> dict[str, Any]:
        """Fetch news from AlphaVantage (primary) or Finnhub (fallback)."""
        news_data = {
            "timestamp": format_timestamp(now_utc()),
            "articles": [],
            "summary": {},
            "source": "none",
        }
        primary = self._get_news_provider(self.news_primary)
        fallback = self._get_news_provider(self.news_fallback)

        # Try primary provider
        if primary:
            try:
                if primary.name == "alphavantage":
                    summary = primary.get_market_sentiment_summary()
                    if summary.get("article_count", 0) > 0:
                        news_data["summary"] = summary
                        news_data["articles"] = summary.get("latest_articles", [])
                        news_data["source"] = primary.name
                        self.logger.info(
                            f"Fetched {summary['article_count']} articles from AlphaVantage"
                        )
                        return news_data
                elif primary.name == "finnhub":
                    market_news = primary.get_market_news("general")
                    if market_news:
                        news_data["articles"] = [n.to_dict() for n in market_news[:10]]
                        news_data["source"] = primary.name
                        news_data["summary"] = {
                            "article_count": len(market_news),
                            "avg_sentiment": 0,
                            "sentiment_distribution": {},
                        }
                        self.logger.info(
                            f"Fetched {len(market_news)} articles from Finnhub"
                        )
                        return news_data
            except Exception as e:
                self.logger.warning(f"{primary.name} news failed: {e}")
                errors.append(self.create_error(
                    f"{primary.name} news failed: {e}",
                    recoverable=True,
                ))

        # Fallback provider
        if fallback:
            try:
                if fallback.name == "finnhub":
                    market_news = fallback.get_market_news("general")
                    if market_news:
                        news_data["articles"] = [n.to_dict() for n in market_news[:10]]
                        news_data["source"] = fallback.name
                        news_data["summary"] = {
                            "article_count": len(market_news),
                            "avg_sentiment": 0,
                            "sentiment_distribution": {},
                        }
                        self.logger.info(
                            f"Fetched {len(market_news)} articles from Finnhub (fallback)"
                        )
                elif fallback.name == "alphavantage":
                    summary = fallback.get_market_sentiment_summary()
                    if summary.get("article_count", 0) > 0:
                        news_data["summary"] = summary
                        news_data["articles"] = summary.get("latest_articles", [])
                        news_data["source"] = fallback.name
                        self.logger.info(
                            f"Fetched {summary['article_count']} articles from AlphaVantage (fallback)"
                        )
            except Exception as e:
                self.logger.error(f"{fallback.name} news failed: {e}")
                errors.append(self.create_error(f"{fallback.name} news failed: {e}"))

        return news_data

    def _analyze_sentiment(
        self,
        news_data: dict[str, Any],
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Analyze news sentiment and produce signals."""
        signals = []
        summary = news_data.get("summary", {})
        source = news_data.get("source", "unknown")

        # If we have AlphaVantage data with sentiment scores
        if source == "alphavantage" and summary:
            signal = self._analyze_alphavantage_sentiment(summary, run_id)
            if signal:
                signals.append(signal)

        # If we only have Finnhub (no sentiment), use article count heuristic
        elif source == "finnhub":
            # Without sentiment data, we can only report neutral
            signals.append(create_signal(
                name=SignalName.SENTIMENT_MIXED.value,
                direction=SignalDirection.NEUTRAL.value,
                score=0,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.3,  # Low confidence without sentiment data
                evidence={
                    "source": "finnhub",
                    "article_count": summary.get("article_count", 0),
                    "note": "No sentiment data available from fallback source",
                },
            ))

        return signals

    def _analyze_alphavantage_sentiment(
        self,
        summary: dict[str, Any],
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze AlphaVantage sentiment data."""
        avg_sentiment = summary.get("avg_sentiment", 0)
        distribution = summary.get("sentiment_distribution", {})
        article_count = summary.get("article_count", 0)

        evidence = {
            "avg_sentiment": avg_sentiment,
            "article_count": article_count,
            "distribution": distribution,
        }

        # Thresholds from config
        thresholds = self.config.get("signal_thresholds", {}).get("sentiment", {})
        bullish_threshold = thresholds.get("bullish_threshold", 0.15)
        bearish_threshold = thresholds.get("bearish_threshold", -0.15)

        # Confidence based on article count
        confidence = min(0.5 + (article_count / 100), 0.9)

        if avg_sentiment >= bullish_threshold:
            return create_signal(
                name=SignalName.SENTIMENT_BULLISH.value,
                direction=SignalDirection.BULLISH.value,
                score=min(avg_sentiment * 2, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=confidence,
                evidence=evidence,
            )
        elif avg_sentiment <= bearish_threshold:
            return create_signal(
                name=SignalName.SENTIMENT_BEARISH.value,
                direction=SignalDirection.BEARISH.value,
                score=max(avg_sentiment * 2, -1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=confidence,
                evidence=evidence,
            )
        else:
            return create_signal(
                name=SignalName.SENTIMENT_MIXED.value,
                direction=SignalDirection.NEUTRAL.value,
                score=avg_sentiment,
                source_node=self.node_name,
                run_id=run_id,
                confidence=confidence * 0.8,  # Lower confidence for neutral
                evidence=evidence,
            )
