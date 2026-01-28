"""
Term Structure Arbitrage Strategy.

Detects price divergence between same-event markets with different tenors.
When short-term and long-term markets move differently, there may be
an arbitrage opportunity.

Core Logic:
1. Group markets by event_id
2. Sort by tenor_days (short vs long)
3. Calculate delta = current_mid - p0 for each
4. Trigger if abs(delta_short - delta_long) > threshold
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fingent.core.logging import LoggerMixin
from fingent.domain.models import (
    PolymarketMarket,
    PolymarketQuote,
    ArbSnapshot,
    ArbOpportunity,
    ArbOpportunityLeg,
)


def estimate_costs(quote_short: PolymarketQuote, quote_long: PolymarketQuote) -> float:
    """
    Estimate trading costs (spread + slippage).

    Args:
        quote_short: Quote for short-term market
        quote_long: Quote for long-term market

    Returns:
        Estimated cost as probability units
    """
    # Average spread in probability units
    avg_spread_bps = (quote_short.spread_bps + quote_long.spread_bps) / 2
    cost = avg_spread_bps / 10000  # Convert bps to decimal

    # Add slippage estimate for low depth
    min_depth = min(
        quote_short.depth_bid, quote_short.depth_ask,
        quote_long.depth_bid, quote_long.depth_ask
    )
    if min_depth < 500:  # Less than $500 depth
        cost *= 2  # Double the cost estimate

    return cost


def confidence_from_liquidity(
    quote_short: PolymarketQuote,
    quote_long: PolymarketQuote,
) -> float:
    """
    Calculate confidence score based on liquidity metrics.

    Args:
        quote_short: Quote for short-term market
        quote_long: Quote for long-term market

    Returns:
        Confidence score (0-1)
    """
    factors = []

    # Volume factor
    vol_s = quote_short.volume_24h or 0
    vol_l = quote_long.volume_24h or 0
    min_vol = min(vol_s, vol_l)
    vol_score = min(min_vol / 10000, 1.0)  # $10k = 1.0
    factors.append(vol_score)

    # Depth factor
    min_depth = min(quote_short.depth_bid, quote_long.depth_bid)
    depth_score = min(min_depth / 2000, 1.0)  # $2k = 1.0
    factors.append(depth_score)

    # Spread factor (lower is better)
    max_spread = max(quote_short.spread_bps, quote_long.spread_bps)
    spread_score = max(0, 1 - max_spread / 300)  # 300 bps = 0
    factors.append(spread_score)

    return sum(factors) / len(factors) if factors else 0.0


class TermStructureStrategy(LoggerMixin):
    """
    Term Structure Arbitrage Strategy.

    Detects opportunities when same-event markets with different
    expiration dates move out of sync.
    """

    def __init__(self, config: dict):
        """
        Initialize strategy.

        Args:
            config: Strategy configuration from config.yaml
        """
        ts_config = config.get("term_structure", {})
        self.delta_threshold = ts_config.get("delta_threshold", 0.05)
        self.trigger_window_minutes = ts_config.get("trigger_window_minutes", 120)
        self.max_markets_per_event = ts_config.get("max_markets_per_event", 10)

    def evaluate(
        self,
        event_id: str,
        markets: list[PolymarketMarket],
        quotes: dict[str, PolymarketQuote],
        snapshots: dict[str, ArbSnapshot],
        trigger_ts: Optional[datetime] = None,
    ) -> Optional[ArbOpportunity]:
        """
        Evaluate term structure for arbitrage opportunity.

        Args:
            event_id: Polymarket event ID
            markets: List of markets in the event
            quotes: Current quotes by market_id
            snapshots: Initial snapshots by market_id
            trigger_ts: Timestamp when news triggered (for window check)

        Returns:
            ArbOpportunity if detected, None otherwise
        """
        # 1. Filter active markets with quotes
        active = [
            m for m in markets
            if m.active and m.market_id in quotes and m.market_id in snapshots
        ]

        if len(active) < 2:
            self.logger.debug(f"Event {event_id}: Not enough active markets ({len(active)})")
            return None

        # 2. Sort by tenor_days
        active.sort(key=lambda x: x.tenor_days)

        # 3. Take short (nearest) and long (farthest)
        # Limit to configured max markets
        active = active[:self.max_markets_per_event]
        short = active[0]
        long = active[-1]

        # 4. Get snapshots
        snap_s = snapshots.get(short.market_id)
        snap_l = snapshots.get(long.market_id)
        if not snap_s or not snap_l:
            return None

        # 5. Check trigger window
        now = datetime.now(timezone.utc)
        if trigger_ts:
            first_seen = trigger_ts
        else:
            try:
                first_seen = datetime.fromisoformat(snap_s.first_seen_ts.replace("Z", "+00:00"))
            except Exception:
                first_seen = now

        window_elapsed = (now - first_seen).total_seconds() / 60
        if window_elapsed > self.trigger_window_minutes:
            self.logger.debug(
                f"Event {event_id}: Outside trigger window "
                f"({window_elapsed:.1f} > {self.trigger_window_minutes} min)"
            )
            return None

        # 6. Calculate deltas
        quote_s = quotes[short.market_id]
        quote_l = quotes[long.market_id]

        delta_s = quote_s.mid - snap_s.p0
        delta_l = quote_l.mid - snap_l.p0

        delta_diff = abs(delta_s - delta_l)

        # 7. Check threshold
        if delta_diff < self.delta_threshold:
            self.logger.debug(
                f"Event {event_id}: Delta diff {delta_diff:.4f} < threshold {self.delta_threshold}"
            )
            return None

        # 8. Estimate costs and edge
        costs = estimate_costs(quote_s, quote_l)
        edge = delta_diff - costs

        if edge <= 0:
            self.logger.debug(f"Event {event_id}: Negative edge after costs ({edge:.4f})")
            return None

        # 9. Calculate confidence
        confidence = confidence_from_liquidity(quote_s, quote_l)

        # 10. Build opportunity
        timestamp = datetime.now(timezone.utc).isoformat()

        legs = [
            ArbOpportunityLeg(
                market_id=short.market_id,
                question=short.question,
                tenor_days=short.tenor_days,
                side="SHORT_LEG",
                current_mid=quote_s.mid,
                delta=delta_s,
            ).to_dict(),
            ArbOpportunityLeg(
                market_id=long.market_id,
                question=long.question,
                tenor_days=long.tenor_days,
                side="LONG_LEG",
                current_mid=quote_l.mid,
                delta=delta_l,
            ).to_dict(),
        ]

        evidence = {
            "trigger_ts": trigger_ts.isoformat() if trigger_ts else None,
            "window_elapsed_min": window_elapsed,
            "short_p0": snap_s.p0,
            "long_p0": snap_l.p0,
            "short_quote": quote_s.to_dict(),
            "long_quote": quote_l.to_dict(),
            "delta_short": delta_s,
            "delta_long": delta_l,
            "estimated_costs": costs,
        }

        opportunity = ArbOpportunity(
            id=str(uuid.uuid4()),
            timestamp=timestamp,
            type="TERM_STRUCTURE",
            event_id=event_id,
            legs=legs,
            delta_diff=delta_diff,
            edge=edge,
            confidence=confidence,
            evidence=evidence,
            risk_flags=[],
            status="CANDIDATE",
        )

        self.logger.info(
            f"Opportunity detected: event={event_id}, "
            f"delta_diff={delta_diff:.4f}, edge={edge:.4f}, conf={confidence:.2f}"
        )

        return opportunity
