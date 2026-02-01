"""
SQLite storage for prediction market shock baselines and price history.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Float, Index, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from fingent.core.config import Settings, get_settings
from fingent.core.logging import get_logger

logger = get_logger("shock_store")

Base = declarative_base()


class ShockPrice(Base):
    __tablename__ = "shock_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String(128), index=True, nullable=False)
    event_id = Column(String(128), index=True, nullable=True)
    question = Column(String(512), nullable=True)
    mid = Column(Float, nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)


Index("idx_shock_prices_market_ts", ShockPrice.market_id, ShockPrice.timestamp)


class ShockMarket(Base):
    __tablename__ = "shock_markets"

    market_id = Column(String(128), primary_key=True)
    event_id = Column(String(128), index=True, nullable=True)
    question = Column(String(512), nullable=True)
    end_time = Column(DateTime, index=True, nullable=True)
    last_seen = Column(DateTime, index=True, nullable=False)


class ShockStore:
    def __init__(self, settings: Optional[Settings] = None):
        settings = settings or get_settings()
        self.database_url = settings.database_url
        self.engine = create_engine(self.database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_price(
        self,
        market_id: str,
        event_id: Optional[str],
        question: Optional[str],
        mid: float,
        timestamp: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> None:
        ts = timestamp or datetime.now(timezone.utc)
        session = self.Session()
        try:
            self.upsert_market(
                market_id=market_id,
                event_id=event_id,
                question=question,
                end_time=end_time,
                session=session,
            )
            session.add(
                ShockPrice(
                    market_id=market_id,
                    event_id=event_id,
                    question=question,
                    mid=mid,
                    timestamp=ts,
                )
            )
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to record shock price: {e}")
        finally:
            session.close()

    def upsert_market(
        self,
        market_id: str,
        event_id: Optional[str],
        question: Optional[str],
        end_time: Optional[datetime],
        session=None,
    ) -> None:
        owns_session = False
        if session is None:
            session = self.Session()
            owns_session = True
        try:
            existing = session.get(ShockMarket, market_id)
            if existing:
                existing.event_id = event_id or existing.event_id
                existing.question = question or existing.question
                existing.end_time = end_time or existing.end_time
                existing.last_seen = datetime.now(timezone.utc)
            else:
                session.add(
                    ShockMarket(
                        market_id=market_id,
                        event_id=event_id,
                        question=question,
                        end_time=end_time,
                        last_seen=datetime.now(timezone.utc),
                    )
                )
            if owns_session:
                session.commit()
        except Exception as e:
            if owns_session:
                session.rollback()
            logger.warning(f"Failed to upsert market: {e}")
        finally:
            if owns_session:
                session.close()

    def get_baseline(
        self,
        market_id: str,
        lookback_minutes: int,
        min_age_seconds: int,
    ) -> Optional[dict]:
        """
        Get baseline price for a market within lookback window and older than min_age.
        Returns the oldest record in the window (stable baseline).
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=lookback_minutes)
        min_age = now - timedelta(seconds=min_age_seconds)

        session = self.Session()
        try:
            record = (
                session.query(ShockPrice)
                .filter(ShockPrice.market_id == market_id)
                .filter(ShockPrice.timestamp >= window_start)
                .filter(ShockPrice.timestamp <= min_age)
                .order_by(ShockPrice.timestamp.asc())
                .first()
            )
            if record:
                return {
                    "market_id": record.market_id,
                    "event_id": record.event_id,
                    "question": record.question,
                    "mid": record.mid,
                    "timestamp": record.timestamp,
                }
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch baseline: {e}")
            return None
        finally:
            session.close()

    def get_history(
        self,
        market_id: str,
        window_minutes: int = 1440,
        limit: int = 500,
    ) -> list[dict]:
        """
        Get recent price history for a market.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=window_minutes)
        session = self.Session()
        try:
            rows = (
                session.query(ShockPrice)
                .filter(ShockPrice.market_id == market_id)
                .filter(ShockPrice.timestamp >= window_start)
                .order_by(ShockPrice.timestamp.asc())
                .limit(limit)
                .all()
            )
            return [
                {"timestamp": r.timestamp, "mid": r.mid, "question": r.question}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch history: {e}")
            return []
        finally:
            session.close()

    def get_window_stats(
        self,
        market_id: str,
        window_minutes: int = 1440,
    ) -> Optional[dict]:
        """
        Get min/max/first/last stats for a market within a window.
        """
        history = self.get_history(market_id, window_minutes=window_minutes, limit=2000)
        if not history:
            return None
        mids = [h["mid"] for h in history if h.get("mid") is not None]
        if not mids:
            return None
        return {
            "min": min(mids),
            "max": max(mids),
            "first": history[0]["mid"],
            "last": history[-1]["mid"],
            "count": len(mids),
        }

    def get_latest_price(self, market_id: str) -> Optional[dict]:
        """Get latest price record for a market."""
        session = self.Session()
        try:
            record = (
                session.query(ShockPrice)
                .filter(ShockPrice.market_id == market_id)
                .order_by(ShockPrice.timestamp.desc())
                .first()
            )
            if record:
                return {
                    "market_id": record.market_id,
                    "event_id": record.event_id,
                    "question": record.question,
                    "mid": record.mid,
                    "timestamp": record.timestamp,
                }
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch latest price: {e}")
            return None
        finally:
            session.close()

    def purge_expired(self, now: Optional[datetime] = None) -> dict[str, int]:
        """
        Purge expired markets and their price history.
        """
        now = now or datetime.now(timezone.utc)
        session = self.Session()
        removed_markets = 0
        removed_prices = 0
        try:
            expired = (
                session.query(ShockMarket)
                .filter(ShockMarket.end_time.isnot(None))
                .filter(ShockMarket.end_time < now)
                .all()
            )
            expired_ids = [m.market_id for m in expired]
            if expired_ids:
                removed_prices = (
                    session.query(ShockPrice)
                    .filter(ShockPrice.market_id.in_(expired_ids))
                    .delete(synchronize_session=False)
                )
                removed_markets = (
                    session.query(ShockMarket)
                    .filter(ShockMarket.market_id.in_(expired_ids))
                    .delete(synchronize_session=False)
                )
                session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to purge expired markets: {e}")
        finally:
            session.close()
        return {"markets": removed_markets, "prices": removed_prices}
