"""
Telegram notification service.

Sends alerts and reports to configured Telegram chat.
"""

import asyncio
from typing import Any, Optional

from fingent.core.config import Settings, get_settings
from fingent.core.logging import get_logger

logger = get_logger("telegram")


class TelegramService:
    """
    Telegram notification service.

    Sends messages to a configured chat using a bot.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
        settings: Optional[Settings] = None,
    ):
        settings = settings or get_settings()

        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.enabled = enabled and settings.telegram_enabled

        self._bot = None

        if self.enabled and (not self.bot_token or not self.chat_id):
            logger.warning("Telegram enabled but credentials not configured")
            self.enabled = False

    async def _get_bot(self):
        """Lazy-initialize Telegram bot."""
        if self._bot is None:
            try:
                from telegram import Bot
                self._bot = Bot(token=self.bot_token)
            except ImportError:
                logger.error("python-telegram-bot not installed")
                self.enabled = False
                return None
        return self._bot

    async def send_message_async(
        self,
        text: str,
        parse_mode: str = "Markdown",
    ) -> bool:
        """
        Send message asynchronously.

        Args:
            text: Message text
            parse_mode: Parse mode (Markdown, HTML, None)

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping message")
            return False

        try:
            bot = await self._get_bot()
            if bot is None:
                return False

            await bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            logger.info("Telegram message sent")
            return True

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def send_message(
        self,
        text: str,
        parse_mode: str = "Markdown",
    ) -> bool:
        """
        Send message synchronously.

        Wrapper around async method for convenience.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in async context, create task
                asyncio.create_task(
                    self.send_message_async(text, parse_mode)
                )
                return True
            else:
                return loop.run_until_complete(
                    self.send_message_async(text, parse_mode)
                )
        except RuntimeError:
            # No event loop, create new one
            return asyncio.run(
                self.send_message_async(text, parse_mode)
            )

    def send_alert(self, alert: dict[str, Any]) -> bool:
        """
        Send an alert notification.

        Args:
            alert: Alert dict from state

        Returns:
            True if sent successfully
        """
        severity_emoji = {
            "low": "📢",
            "medium": "⚠️",
            "high": "🚨",
            "critical": "🔴",
        }
        emoji = severity_emoji.get(alert.get("severity", "medium"), "⚠️")

        text = f"""{emoji} *{alert.get('title', 'Alert')}*

{alert.get('message', '')}

📊 当前值: `{alert.get('current_value')}`
📏 阈值: `{alert.get('threshold')}`
⏰ 时间: {alert.get('triggered_at', '')}"""

        return self.send_message(text)

    def send_alerts(self, alerts: list[dict[str, Any]]) -> int:
        """
        Send multiple alerts.

        Args:
            alerts: List of alert dicts

        Returns:
            Number of alerts sent successfully
        """
        sent = 0
        for alert in alerts:
            if self.send_alert(alert):
                sent += 1
        return sent

    def send_report_summary(
        self,
        report: dict[str, Any],
    ) -> bool:
        """
        Send report summary notification.

        Args:
            report: Report dict from state

        Returns:
            True if sent successfully
        """
        signals_summary = report.get("signals_summary", {})
        direction = signals_summary.get("overall_direction", "neutral")
        score = signals_summary.get("overall_score", 0)

        direction_emoji = {
            "bullish": "🟢",
            "bearish": "🔴",
            "neutral": "⚪",
            "hawkish": "🦅",
            "dovish": "🕊️",
        }
        emoji = direction_emoji.get(direction, "⚪")

        alerts = report.get("alerts", [])
        alert_text = ""
        if alerts:
            alert_text = f"\n\n🚨 *告警 ({len(alerts)})*:"
            for alert in alerts[:3]:
                alert_text += f"\n• {alert.get('title')}"

        text = f"""📊 *Fingent 分析报告*

{emoji} *方向*: {direction.upper()} ({score:+.2f})
📈 *信号*: {signals_summary.get('signal_count', 0)} 个
{alert_text}

{report.get('summary', '')[:500]}

⏰ {report.get('timestamp', '')}"""

        return self.send_message(text)

    def send_shock(self, shock: dict[str, Any]) -> bool:
        """
        Send a probability shock notification.
        """
        event_title = shock.get("event_title") or "Unknown Event"
        question = shock.get("question") or "Unknown Market"
        delta = shock.get("delta", 0)
        current_mid = shock.get("current_mid", 0)
        baseline_mid = shock.get("baseline_mid", 0)
        volume_24h = shock.get("volume_24h")
        depth_usd = shock.get("depth_usd")
        spread_bps = shock.get("spread_bps")
        timestamp = shock.get("timestamp", "")

        text = (
            "⚡ Probability Shock\n\n"
            f"Event: {event_title}\n"
            f"Market: {question}\n"
            f"Delta: {delta:+.2%}\n"
            f"Prob: {current_mid:.2%} (baseline {baseline_mid:.2%})\n"
            f"Volume24h: {volume_24h if volume_24h is not None else 'N/A'}\n"
            f"Depth: {depth_usd if depth_usd is not None else 'N/A'}\n"
            f"Spread: {spread_bps if spread_bps is not None else 'N/A'} bps\n"
            f"Time: {timestamp}"
        )

        return self.send_message(text, parse_mode=None)


def create_telegram_service(
    settings: Optional[Settings] = None,
) -> TelegramService:
    """Create Telegram service from settings."""
    return TelegramService(settings=settings)
