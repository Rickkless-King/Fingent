"""
Services module - Cross-cutting capabilities

Contains:
- LLM service (DeepSeek/Qwen)
- Telegram notifications
- Data persistence
- Scheduling
- Sentiment analysis
- Market direction calculation
"""

from fingent.services.llm import LLMService, create_llm_service
from fingent.services.telegram import TelegramService
from fingent.services.persistence import PersistenceService, SQLitePersistence
from fingent.services.scheduler import SchedulerService
from fingent.services.sentiment import SentimentAnalyzer, get_sentiment_analyzer
from fingent.services.market_direction import (
    MarketDirectionCalculator,
    calculate_market_direction,
    get_market_direction_calculator,
)

__all__ = [
    "LLMService",
    "create_llm_service",
    "TelegramService",
    "PersistenceService",
    "SQLitePersistence",
    "SchedulerService",
    "SentimentAnalyzer",
    "get_sentiment_analyzer",
    "MarketDirectionCalculator",
    "calculate_market_direction",
    "get_market_direction_calculator",
]
