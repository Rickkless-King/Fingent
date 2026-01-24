"""
Scheduler service for periodic workflow execution.

Uses APScheduler for local scheduling.
For cloud deployment, use EventBridge/CloudWatch Events instead.
"""

from datetime import datetime
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from fingent.core.config import Settings, get_settings, load_yaml_config
from fingent.core.logging import get_logger

logger = get_logger("scheduler")


class SchedulerService:
    """
    Scheduler service for periodic workflow execution.

    Uses APScheduler with cron triggers.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        config: Optional[dict] = None,
    ):
        self.settings = settings or get_settings()
        self.config = config or load_yaml_config()
        self._scheduler: Optional[BackgroundScheduler] = None
        self._jobs: dict[str, str] = {}  # name -> job_id

    @property
    def scheduler(self) -> BackgroundScheduler:
        """Lazy-initialize scheduler."""
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler(
                timezone=self.settings.timezone,
            )
        return self._scheduler

    def add_job(
        self,
        name: str,
        func: Callable,
        cron: str,
        args: Optional[tuple] = None,
        kwargs: Optional[dict] = None,
    ) -> str:
        """
        Add a scheduled job.

        Args:
            name: Job name
            func: Function to execute
            cron: Cron expression (e.g., "0 7 * * *")
            args: Positional arguments for func
            kwargs: Keyword arguments for func

        Returns:
            Job ID
        """
        # Parse cron expression
        parts = cron.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron}")

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=self.settings.timezone,
        )

        job = self.scheduler.add_job(
            func,
            trigger=trigger,
            args=args or (),
            kwargs=kwargs or {},
            id=name,
            name=name,
            replace_existing=True,
        )

        self._jobs[name] = job.id
        logger.info(f"Added job: {name} with cron '{cron}'")
        return job.id

    def add_interval_job(
        self,
        name: str,
        func: Callable,
        minutes: int,
        args: Optional[tuple] = None,
        kwargs: Optional[dict] = None,
    ) -> str:
        """
        Add a job that runs at fixed intervals.

        Args:
            name: Job name
            func: Function to execute
            minutes: Interval in minutes
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            Job ID
        """
        job = self.scheduler.add_job(
            func,
            "interval",
            minutes=minutes,
            args=args or (),
            kwargs=kwargs or {},
            id=name,
            name=name,
            replace_existing=True,
        )

        self._jobs[name] = job.id
        logger.info(f"Added interval job: {name} every {minutes} minutes")
        return job.id

    def remove_job(self, name: str) -> bool:
        """
        Remove a scheduled job.

        Args:
            name: Job name

        Returns:
            True if removed
        """
        if name in self._jobs:
            self.scheduler.remove_job(self._jobs[name])
            del self._jobs[name]
            logger.info(f"Removed job: {name}")
            return True
        return False

    def start(self) -> None:
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

    def get_jobs(self) -> list[dict[str, Any]]:
        """Get list of scheduled jobs."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return jobs

    def setup_from_config(self, workflow_func: Callable) -> None:
        """
        Setup jobs from config.yaml.

        Args:
            workflow_func: The workflow execution function
        """
        scheduler_config = self.config.get("scheduler", {})

        # Daily report
        daily_config = scheduler_config.get("daily_report", {})
        if daily_config.get("enabled", False):
            cron = daily_config.get("cron", "0 7 * * *")
            self.add_job("daily_report", workflow_func, cron)

        # Intraday check
        intraday_config = scheduler_config.get("intraday_check", {})
        if intraday_config.get("enabled", False):
            minutes = intraday_config.get("interval_minutes", 15)
            self.add_interval_job("intraday_check", workflow_func, minutes)


def create_scheduler_service(
    settings: Optional[Settings] = None,
    config: Optional[dict] = None,
) -> SchedulerService:
    """Create scheduler service."""
    return SchedulerService(settings=settings, config=config)
