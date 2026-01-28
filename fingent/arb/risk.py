"""
Risk Manager for Arbitrage Opportunities.

Filters opportunities based on:
- Volume: Minimum 24h volume
- Spread: Maximum bid-ask spread
- Depth: Minimum orderbook depth
- Time: Minimum time to settlement
- Cooldown: Prevent spam for same event

Only opportunities passing all hard filters proceed to notification.
"""

import time
from typing import Optional

from fingent.core.logging import LoggerMixin
from fingent.domain.models import ArbOpportunity, PolymarketQuote, PolymarketMarket


class RiskManager(LoggerMixin):
    """
    Risk filter for arbitrage opportunities.

    Hard filters result in FILTERED status.
    Soft filters add risk_flags but allow progression.
    """

    def __init__(self, config: dict):
        """
        Initialize risk manager.

        Args:
            config: Risk configuration from config.yaml['arbitrage']['risk']
        """
        self.min_volume_24h = config.get("min_volume_24h", 5000)
        self.max_spread_bps = config.get("max_spread_bps", 300)
        self.min_depth_usd = config.get("min_depth_usd", 1000)
        self.min_time_to_settle_hours = config.get("min_time_to_settle_hours", 12)
        self.cooldown_seconds = config.get("cooldown_seconds", 900)

        # Track last alert time per event
        self._last_alert: dict[str, float] = {}

    def filter(
        self,
        opportunity: ArbOpportunity,
        quotes: dict[str, PolymarketQuote],
        markets: Optional[dict[str, PolymarketMarket]] = None,
    ) -> ArbOpportunity:
        """
        Apply risk filters to opportunity.

        Args:
            opportunity: Candidate opportunity
            quotes: Current quotes by market_id
            markets: Market metadata by market_id (optional, for tenor check)

        Returns:
            Opportunity with updated risk_flags and status
        """
        flags = []
        hard_fail = False

        for leg in opportunity.legs:
            market_id = leg.get("market_id", "")
            quote = quotes.get(market_id)

            if not quote:
                flags.append(f"MISSING_QUOTE:{market_id}")
                hard_fail = True
                continue

            # Volume check (hard filter)
            if quote.volume_24h is not None and quote.volume_24h < self.min_volume_24h:
                flags.append(f"LOW_VOLUME:{market_id}:{quote.volume_24h:.0f}")
                hard_fail = True

            # Spread check (hard filter)
            if quote.spread_bps > self.max_spread_bps:
                flags.append(f"WIDE_SPREAD:{market_id}:{quote.spread_bps:.0f}bps")
                hard_fail = True

            # Depth check (soft filter - warn but allow)
            min_depth = min(quote.depth_bid, quote.depth_ask)
            if min_depth < self.min_depth_usd:
                flags.append(f"LOW_DEPTH:{market_id}:{min_depth:.0f}")
                # Note: soft filter, doesn't set hard_fail

            # Time to settle check (if markets provided)
            if markets:
                market = markets.get(market_id)
                if market and market.tenor_days * 24 < self.min_time_to_settle_hours:
                    flags.append(f"TOO_CLOSE_TO_SETTLE:{market_id}:{market.tenor_days}d")
                    hard_fail = True

        # Cooldown check
        event_id = opportunity.event_id
        now = time.time()
        last_alert = self._last_alert.get(event_id, 0)

        if now - last_alert < self.cooldown_seconds:
            remaining = self.cooldown_seconds - (now - last_alert)
            flags.append(f"COOLDOWN:{event_id}:{remaining:.0f}s")
            hard_fail = True

        # Update opportunity
        opportunity.risk_flags = flags
        opportunity.status = "FILTERED" if hard_fail else "CANDIDATE"

        # Update cooldown tracker if passing
        if opportunity.status == "CANDIDATE":
            self._last_alert[event_id] = now

        if flags:
            self.logger.info(
                f"Risk check for {event_id}: "
                f"status={opportunity.status}, flags={flags}"
            )

        return opportunity

    def reset_cooldown(self, event_id: str) -> None:
        """
        Reset cooldown for an event.

        Args:
            event_id: Event ID to reset
        """
        if event_id in self._last_alert:
            del self._last_alert[event_id]
            self.logger.info(f"Reset cooldown for event {event_id}")

    def get_cooldown_remaining(self, event_id: str) -> float:
        """
        Get remaining cooldown time for an event.

        Args:
            event_id: Event ID to check

        Returns:
            Remaining seconds, 0 if no cooldown
        """
        last_alert = self._last_alert.get(event_id, 0)
        elapsed = time.time() - last_alert
        remaining = max(0, self.cooldown_seconds - elapsed)
        return remaining
