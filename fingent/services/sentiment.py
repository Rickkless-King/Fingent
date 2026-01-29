"""
Unified Sentiment Analysis Service.

Provides consistent sentiment analysis regardless of news source.

Strategy:
1. Use source-provided sentiment if available (Marketaux, AlphaVantage)
2. Use LLM for batch sentiment analysis if no source sentiment
3. Use keyword-based rules as final fallback

This ensures all news articles have sentiment scores,
regardless of which provider they came from.
"""

import re
from typing import Any, Optional
from dataclasses import dataclass

from fingent.core.config import get_settings, load_yaml_config
from fingent.core.logging import LoggerMixin
from fingent.domain.models import NewsItem


@dataclass
class SentimentResult:
    """Result of sentiment analysis."""
    score: float  # -1 to 1
    label: str  # bullish, bearish, neutral
    confidence: float  # 0 to 1
    method: str  # source, llm, keywords


# Keyword-based sentiment rules
BULLISH_KEYWORDS = [
    # English
    "surge", "soar", "jump", "rally", "gain", "rise", "climb", "record high",
    "beat", "exceed", "outperform", "upgrade", "bullish", "optimistic",
    "growth", "profit", "revenue up", "strong earnings", "buy",
    # Chinese
    "上涨", "飙升", "突破", "利好", "看涨", "牛市", "增长", "盈利",
]

BEARISH_KEYWORDS = [
    # English
    "plunge", "crash", "tumble", "drop", "fall", "decline", "slump", "sink",
    "miss", "disappoint", "downgrade", "bearish", "pessimistic", "warning",
    "loss", "cut", "layoff", "recession", "sell", "fear", "concern",
    # Chinese
    "下跌", "暴跌", "崩盘", "利空", "看跌", "熊市", "亏损", "裁员",
]


class SentimentAnalyzer(LoggerMixin):
    """
    Unified sentiment analysis service.

    Provides consistent sentiment scores regardless of news source.
    """

    def __init__(self):
        self.settings = get_settings()
        self.config = load_yaml_config()
        self._llm_service = None

    def _get_llm_service(self):
        """Lazily initialize LLM service."""
        if self._llm_service is None:
            try:
                from fingent.services.llm import create_llm_service
                self._llm_service = create_llm_service()
            except Exception as e:
                self.logger.warning(f"LLM service not available: {e}")
        return self._llm_service

    def analyze_article(self, article: dict[str, Any]) -> SentimentResult:
        """
        Analyze sentiment of a single article.

        Args:
            article: Article dict with title, summary, sentiment_score (optional)

        Returns:
            SentimentResult with score, label, confidence, method
        """
        # 1. Check if source already provided sentiment
        source_score = article.get("sentiment_score")
        if source_score is not None and source_score != 0:
            return SentimentResult(
                score=source_score,
                label=self._score_to_label(source_score),
                confidence=0.7,  # Source sentiment is usually reliable
                method="source",
            )

        # 2. Try keyword-based analysis (fast, no API call)
        title = article.get("title", "")
        summary = article.get("summary", "")
        text = f"{title} {summary}".lower()

        keyword_result = self._analyze_by_keywords(text)
        if keyword_result.score != 0:
            return keyword_result

        # 3. Default to neutral if no signals
        return SentimentResult(
            score=0,
            label="neutral",
            confidence=0.3,
            method="default",
        )

    def analyze_batch(
        self,
        articles: list[dict[str, Any]],
        use_llm: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Analyze sentiment for a batch of articles.

        Updates each article dict with sentiment_score and sentiment_label.

        Args:
            articles: List of article dicts
            use_llm: Whether to use LLM for articles without sentiment

        Returns:
            Updated articles with sentiment scores
        """
        articles_needing_analysis = []
        results = []

        for article in articles:
            # Check if already has valid sentiment
            if article.get("sentiment_score") is not None:
                results.append(article)
                continue

            # Analyze using keywords first
            result = self.analyze_article(article)
            article["sentiment_score"] = result.score
            article["sentiment_label"] = result.label
            article["sentiment_method"] = result.method
            article["sentiment_confidence"] = result.confidence

            # Collect articles that might benefit from LLM analysis
            if result.method == "default" and use_llm:
                articles_needing_analysis.append(article)

            results.append(article)

        # Batch LLM analysis for articles without clear sentiment
        if articles_needing_analysis and use_llm:
            self._analyze_batch_with_llm(articles_needing_analysis)

        return results

    def _analyze_by_keywords(self, text: str) -> SentimentResult:
        """Analyze sentiment using keyword matching."""
        bullish_count = 0
        bearish_count = 0

        for keyword in BULLISH_KEYWORDS:
            if keyword.lower() in text:
                bullish_count += 1

        for keyword in BEARISH_KEYWORDS:
            if keyword.lower() in text:
                bearish_count += 1

        total = bullish_count + bearish_count

        if total == 0:
            return SentimentResult(
                score=0,
                label="neutral",
                confidence=0.2,
                method="keywords",
            )

        # Calculate score based on keyword balance
        score = (bullish_count - bearish_count) / max(total, 1)
        # Scale to -1 to 1 range
        score = max(-1, min(1, score * 0.5))

        # Confidence based on keyword count
        confidence = min(0.3 + (total * 0.1), 0.6)

        return SentimentResult(
            score=score,
            label=self._score_to_label(score),
            confidence=confidence,
            method="keywords",
        )

    def _analyze_batch_with_llm(self, articles: list[dict[str, Any]]) -> None:
        """
        Use LLM to analyze sentiment for articles without clear signals.

        Updates articles in place.
        """
        llm = self._get_llm_service()
        if not llm:
            return

        # Prepare batch prompt
        titles = [a.get("title", "")[:100] for a in articles[:10]]  # Limit batch size

        prompt = """Analyze the sentiment of these news headlines for financial markets.
For each headline, respond with exactly one word: bullish, bearish, or neutral.

Headlines:
"""
        for i, title in enumerate(titles, 1):
            prompt += f"{i}. {title}\n"

        prompt += """
Response format (one word per line):
1. [sentiment]
2. [sentiment]
..."""

        try:
            response = llm.generate(prompt, max_tokens=100)

            # Parse response
            lines = response.strip().split("\n")
            for i, line in enumerate(lines):
                if i >= len(articles):
                    break

                line_lower = line.lower()
                if "bullish" in line_lower:
                    score = 0.4
                    label = "bullish"
                elif "bearish" in line_lower:
                    score = -0.4
                    label = "bearish"
                else:
                    score = 0
                    label = "neutral"

                articles[i]["sentiment_score"] = score
                articles[i]["sentiment_label"] = label
                articles[i]["sentiment_method"] = "llm"
                articles[i]["sentiment_confidence"] = 0.5

            self.logger.info(f"LLM analyzed {len(titles)} articles")

        except Exception as e:
            self.logger.warning(f"LLM sentiment analysis failed: {e}")

    def calculate_aggregate_sentiment(
        self,
        articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Calculate aggregate sentiment metrics from articles.

        Returns:
            Dict with avg_sentiment, distribution, confidence
        """
        scores = []
        confidences = []
        distribution = {"bullish": 0, "bearish": 0, "neutral": 0}

        for article in articles:
            score = article.get("sentiment_score")
            if score is not None:
                scores.append(score)
                confidences.append(article.get("sentiment_confidence", 0.5))

                label = article.get("sentiment_label", "neutral")
                if label in distribution:
                    distribution[label] += 1

        if not scores:
            return {
                "avg_sentiment": 0,
                "weighted_sentiment": 0,
                "sentiment_distribution": distribution,
                "confidence": 0,
                "article_count": len(articles),
            }

        avg_sentiment = sum(scores) / len(scores)

        # Weighted average by confidence
        weighted_sum = sum(s * c for s, c in zip(scores, confidences))
        total_confidence = sum(confidences)
        weighted_sentiment = weighted_sum / total_confidence if total_confidence > 0 else 0

        avg_confidence = sum(confidences) / len(confidences)

        return {
            "avg_sentiment": avg_sentiment,
            "weighted_sentiment": weighted_sentiment,
            "sentiment_distribution": distribution,
            "confidence": avg_confidence,
            "article_count": len(articles),
        }

    @staticmethod
    def _score_to_label(score: float) -> str:
        """Convert sentiment score to label."""
        if score > 0.15:
            return "bullish"
        elif score < -0.15:
            return "bearish"
        return "neutral"


# Singleton instance
_sentiment_analyzer: Optional[SentimentAnalyzer] = None


def get_sentiment_analyzer() -> SentimentAnalyzer:
    """Get the global sentiment analyzer instance."""
    global _sentiment_analyzer
    if _sentiment_analyzer is None:
        _sentiment_analyzer = SentimentAnalyzer()
    return _sentiment_analyzer
