"""
Data persistence service.

Supports:
- SQLite (local development)
- S3 (cloud deployment, future)
"""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from fingent.core.config import Settings, get_settings
from fingent.core.logging import get_logger
from fingent.core.timeutil import format_timestamp, now_utc

logger = get_logger("persistence")

Base = declarative_base()


class RunSnapshot(Base):
    """Database model for workflow run snapshots."""

    __tablename__ = "run_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(100), unique=True, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    state_json = Column(Text, nullable=False)
    report_json = Column(Text, nullable=True)
    alert_count = Column(Integer, default=0)
    signal_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)


class PersistenceService(ABC):
    """Abstract base class for persistence services."""

    @abstractmethod
    def save_snapshot(self, state: dict[str, Any]) -> str:
        """Save workflow state snapshot."""
        pass

    @abstractmethod
    def load_snapshot(self, run_id: str) -> Optional[dict[str, Any]]:
        """Load a specific snapshot by run_id."""
        pass

    @abstractmethod
    def load_latest(self) -> Optional[dict[str, Any]]:
        """Load the most recent snapshot."""
        pass

    @abstractmethod
    def list_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent snapshots."""
        pass


class SQLitePersistence(PersistenceService):
    """
    SQLite-based persistence for local development.

    Stores workflow snapshots in a local SQLite database.
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        settings: Optional[Settings] = None,
    ):
        settings = settings or get_settings()
        self.database_url = database_url or settings.database_url

        # Ensure data directory exists
        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.replace("sqlite:///", ""))
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(self.database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        logger.info(f"Initialized SQLite persistence: {self.database_url}")

    def save_snapshot(self, state: dict[str, Any]) -> str:
        """
        Save workflow state snapshot.

        Args:
            state: GraphState dict

        Returns:
            run_id of saved snapshot
        """
        run_id = state.get("run_id", f"run_{format_timestamp(now_utc())}")

        snapshot = RunSnapshot(
            run_id=run_id,
            timestamp=datetime.utcnow(),
            state_json=json.dumps(state, default=str),
            report_json=json.dumps(state.get("report", {}), default=str),
            alert_count=len(state.get("alerts", [])),
            signal_count=len(state.get("signals", [])),
            error_count=len(state.get("errors", [])),
        )

        session = self.Session()
        try:
            # Check if exists
            existing = session.query(RunSnapshot).filter_by(run_id=run_id).first()
            if existing:
                existing.state_json = snapshot.state_json
                existing.report_json = snapshot.report_json
                existing.alert_count = snapshot.alert_count
                existing.signal_count = snapshot.signal_count
                existing.error_count = snapshot.error_count
            else:
                session.add(snapshot)

            session.commit()
            logger.info(f"Saved snapshot: {run_id}")
            return run_id

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save snapshot: {e}")
            raise
        finally:
            session.close()

    def load_snapshot(self, run_id: str) -> Optional[dict[str, Any]]:
        """Load snapshot by run_id."""
        session = self.Session()
        try:
            snapshot = session.query(RunSnapshot).filter_by(run_id=run_id).first()
            if snapshot:
                return json.loads(snapshot.state_json)
            return None
        finally:
            session.close()

    def load_latest(self) -> Optional[dict[str, Any]]:
        """Load most recent snapshot."""
        session = self.Session()
        try:
            snapshot = (
                session.query(RunSnapshot)
                .order_by(RunSnapshot.timestamp.desc())
                .first()
            )
            if snapshot:
                return json.loads(snapshot.state_json)
            return None
        finally:
            session.close()

    def list_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent snapshots (metadata only)."""
        session = self.Session()
        try:
            snapshots = (
                session.query(RunSnapshot)
                .order_by(RunSnapshot.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "run_id": s.run_id,
                    "timestamp": s.timestamp.isoformat(),
                    "alert_count": s.alert_count,
                    "signal_count": s.signal_count,
                    "error_count": s.error_count,
                }
                for s in snapshots
            ]
        finally:
            session.close()

    def get_report(self, run_id: str) -> Optional[dict[str, Any]]:
        """Get report from a specific run."""
        session = self.Session()
        try:
            snapshot = session.query(RunSnapshot).filter_by(run_id=run_id).first()
            if snapshot and snapshot.report_json:
                return json.loads(snapshot.report_json)
            return None
        finally:
            session.close()


class S3Persistence(PersistenceService):
    """
    S3-based persistence for cloud deployment.

    TODO: Implement when deploying to AWS.
    """

    def __init__(self, bucket: str, prefix: str = "fingent/"):
        self.bucket = bucket
        self.prefix = prefix
        logger.warning("S3Persistence not yet implemented")

    def save_snapshot(self, state: dict[str, Any]) -> str:
        raise NotImplementedError("S3Persistence not yet implemented")

    def load_snapshot(self, run_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError("S3Persistence not yet implemented")

    def load_latest(self) -> Optional[dict[str, Any]]:
        raise NotImplementedError("S3Persistence not yet implemented")

    def list_snapshots(self, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError("S3Persistence not yet implemented")


def create_persistence_service(
    settings: Optional[Settings] = None,
) -> PersistenceService:
    """
    Create persistence service based on environment.

    Returns SQLite for local, S3 for AWS.
    """
    settings = settings or get_settings()

    if settings.is_aws:
        # TODO: Return S3Persistence when implemented
        logger.warning("AWS environment but S3 not implemented, using SQLite")

    return SQLitePersistence(settings=settings)
