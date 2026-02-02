"""
Arbitrage Engine.

Main orchestrator for Polymarket arbitrage detection.

Flow:
1. Trigger: News keyword match or manual scan
2. Recall: Find relevant Polymarket events/markets
3. Snapshot: Record initial prices (P0)
4. Monitor: Poll quotes periodically
5. Detect: Run term structure strategy
6. Filter: Apply risk controls
7. Notify: Alert via Telegram (optional)
8. Store: Save to persistence layer
"""

import re
from datetime import datetime, timezone
from typing import Any, Optional

from fingent.core.config import get_settings, load_yaml_config
from fingent.core.logging import LoggerMixin
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import (
    PolymarketMarket,
    PolymarketQuote,
    ArbSnapshot,
    ArbOpportunity,
)
from fingent.providers.polymarket import PolymarketProvider
from fingent.providers.finnhub import FinnhubProvider
from fingent.arb.strategy import TermStructureStrategy
from fingent.arb.risk import RiskManager


class ArbEngine(LoggerMixin):
    """
    Polymarket Arbitrage Detection Engine.

    Coordinates the full arbitrage detection pipeline.
    """

    def __init__(
        self,
        provider: Optional[PolymarketProvider] = None,
        config: Optional[dict] = None,
        shock_store: Optional[Any] = None,
    ):
        """
        Initialize arbitrage engine.

        Args:
            provider: Polymarket provider instance
            config: Configuration dict (or load from yaml)
        """
        self.settings = get_settings()

        # Load config
        if config is None:
            full_config = load_yaml_config()
            config = full_config.get("arbitrage", {})
        self.config = config

        # Check if enabled
        self.enabled = config.get("enabled", False)
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled in config")

        # Compile keyword patterns
        self.keyword_patterns = []
        for pattern in config.get("trigger_keywords", []):
            try:
                self.keyword_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                self.logger.warning(f"Invalid regex pattern '{pattern}': {e}")

        # Load synonym map for keyword expansion
        self.synonym_map = config.get("synonym_map", {})

        # Initialize provider
        self.provider = provider or PolymarketProvider()
        self.shock_store = shock_store

        # Initialize strategy and risk manager
        self.strategy = TermStructureStrategy(config)
        self.risk_manager = RiskManager(config.get("risk", {}))

        # In-memory snapshot store (can be moved to Redis/SQLite later)
        self._snapshots: dict[str, ArbSnapshot] = {}

        # Separate snapshot store for probability shock baseline
        self._shock_snapshots: dict[str, ArbSnapshot] = {}

        # Track detected opportunities
        self._opportunities: list[ArbOpportunity] = []

    def check_news_trigger(self, headline: str, summary: str = "") -> list[str]:
        """
        Check if news matches trigger keywords.

        Args:
            headline: News headline
            summary: News summary (optional)

        Returns:
            List of matched keywords
        """
        text = f"{headline} {summary}"
        matched = []

        for pattern in self.keyword_patterns:
            match = pattern.search(text)
            if match:
                matched.append(match.group())

        return matched

    def scan_markets(
        self,
        keywords: Optional[list[str]] = None,
    ) -> dict[str, list[PolymarketMarket]]:
        """
        Scan Polymarket for relevant markets.

        Args:
            keywords: Keywords to search (uses config if not provided)

        Returns:
            Dict mapping event_id -> list of markets
        """
        if not self.enabled or not self.provider.is_enabled:
            return {}

        if keywords is None:
            # Extract keywords from patterns
            keywords = []
            for pattern in self.keyword_patterns:
                # Simple extraction: use pattern string as keyword
                # In production, maintain a separate keyword list
                raw = pattern.pattern.replace("(", "").replace(")", "").replace("|", " ")
                keywords.extend(raw.split())

        # Dedupe and clean
        keywords = list(set(k.strip() for k in keywords if len(k) > 2))[:20]

        self.logger.info(f"Scanning markets for keywords: {keywords[:5]}...")

        risk_config = self.config.get("risk", {})
        min_volume = risk_config.get("min_volume_24h", 5000)

        return self.provider.get_markets_for_arb(
            keywords=keywords,
            min_volume=min_volume,
            min_markets_per_event=2,
            synonym_map=self.synonym_map,
        )

    def create_snapshots(
        self,
        markets: list[PolymarketMarket],
        news_id: str = "manual",
    ) -> dict[str, ArbSnapshot]:
        """
        Create initial snapshots for markets.

        Only creates snapshot if not already exists.

        Args:
            markets: List of markets to snapshot
            news_id: Identifier for triggering news

        Returns:
            Dict of market_id -> ArbSnapshot
        """
        snapshots = {}
        timestamp = format_timestamp(now_utc())

        for market in markets:
            if market.market_id in self._snapshots:
                # Already have snapshot
                snapshots[market.market_id] = self._snapshots[market.market_id]
                continue

            # Get current quote
            quote = self.provider.get_quote(market)
            if not quote:
                continue

            snapshot = ArbSnapshot(
                market_id=market.market_id,
                news_id=news_id,
                first_seen_ts=timestamp,
                p0=quote.mid,
                quote0=quote.to_dict(),
                volume0=quote.volume_24h,
            )

            self._snapshots[market.market_id] = snapshot
            snapshots[market.market_id] = snapshot

            self.logger.debug(
                f"Created snapshot for {market.market_id}: p0={quote.mid:.4f}"
            )

        return snapshots

    def detect_opportunities(
        self,
        event_markets: dict[str, list[PolymarketMarket]],
        trigger_ts: Optional[datetime] = None,
    ) -> list[ArbOpportunity]:
        """
        Detect arbitrage opportunities across events.

        Args:
            event_markets: Dict of event_id -> markets
            trigger_ts: Timestamp of triggering event

        Returns:
            List of detected opportunities (before risk filter)
        """
        if not self.enabled:
            return []

        opportunities = []

        for event_id, markets in event_markets.items():
            # Ensure snapshots exist
            snapshots = self.create_snapshots(markets)

            # Get current quotes
            quotes = self.provider.get_quotes_batch(markets)

            if len(quotes) < 2:
                self.logger.debug(f"Event {event_id}: Not enough quotes ({len(quotes)})")
                continue

            # Get snapshots for this event's markets
            event_snapshots = {
                m.market_id: self._snapshots[m.market_id]
                for m in markets
                if m.market_id in self._snapshots
            }

            # Run strategy
            opportunity = self.strategy.evaluate(
                event_id=event_id,
                markets=markets,
                quotes=quotes,
                snapshots=event_snapshots,
                trigger_ts=trigger_ts,
            )

            if opportunity:
                opportunities.append(opportunity)

        return opportunities

    def filter_opportunities(
        self,
        opportunities: list[ArbOpportunity],
        event_markets: dict[str, list[PolymarketMarket]],
    ) -> list[ArbOpportunity]:
        """
        Apply risk filters to opportunities.

        Args:
            opportunities: Raw opportunities from detection
            event_markets: Market data for context

        Returns:
            Filtered opportunities (only CANDIDATE status)
        """
        filtered = []

        for opp in opportunities:
            # Get markets and quotes for this event
            markets = event_markets.get(opp.event_id, [])
            markets_dict = {m.market_id: m for m in markets}
            quotes = self.provider.get_quotes_batch(markets)

            # Apply risk filter
            opp = self.risk_manager.filter(opp, quotes, markets_dict)

            if opp.status == "CANDIDATE":
                opp.status = "CONFIRMED"
                filtered.append(opp)
                self._opportunities.append(opp)

        return filtered

    def run_scan(
        self,
        keywords: Optional[list[str]] = None,
        trigger_ts: Optional[datetime] = None,
    ) -> list[ArbOpportunity]:
        """
        Run a full arbitrage scan.

        Args:
            keywords: Keywords to search (optional)
            trigger_ts: Trigger timestamp (optional)

        Returns:
            List of confirmed opportunities
        """
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled")
            return []

        self.logger.info("Starting arbitrage scan...")

        # 1. Scan markets
        event_markets = self.scan_markets(keywords)
        self.logger.info(f"Found {len(event_markets)} events with multiple markets")

        if not event_markets:
            return []

        # 2. Detect opportunities
        raw_opportunities = self.detect_opportunities(event_markets, trigger_ts)
        self.logger.info(f"Detected {len(raw_opportunities)} raw opportunities")

        if not raw_opportunities:
            return []

        # 3. Filter
        confirmed = self.filter_opportunities(raw_opportunities, event_markets)
        self.logger.info(f"Confirmed {len(confirmed)} opportunities after risk filter")

        return confirmed

    def process_news(
        self,
        headline: str,
        summary: str = "",
        news_id: str = "",
    ) -> list[ArbOpportunity]:
        """
        Process a news event and check for arbitrage.

        Args:
            headline: News headline
            summary: News summary
            news_id: Unique news identifier

        Returns:
            List of confirmed opportunities
        """
        if not self.enabled:
            return []

        # Check trigger
        matched_keywords = self.check_news_trigger(headline, summary)

        if not matched_keywords:
            self.logger.debug(f"No keyword match for: {headline[:50]}...")
            return []

        self.logger.info(
            f"News triggered: '{headline[:50]}...' "
            f"(matched: {matched_keywords[:3]})"
        )

        # Run scan with matched keywords
        return self.run_scan(
            keywords=matched_keywords,
            trigger_ts=datetime.now(timezone.utc),
        )

    def get_opportunities(self) -> list[dict]:
        """
        Get all detected opportunities.

        Returns:
            List of opportunity dicts
        """
        return [o.to_dict() for o in self._opportunities]

    def get_snapshots(self) -> dict[str, dict]:
        """
        Get all snapshots.

        Returns:
            Dict of market_id -> snapshot dict
        """
        return {k: v.to_dict() for k, v in self._snapshots.items()}

    def clear_snapshots(self, older_than_hours: float = 6) -> int:
        """
        Clear old snapshots.

        Args:
            older_than_hours: Remove snapshots older than this

        Returns:
            Number of snapshots removed
        """
        now = datetime.now(timezone.utc)
        to_remove = []

        for market_id, snapshot in self._snapshots.items():
            try:
                ts = datetime.fromisoformat(snapshot.first_seen_ts.replace("Z", "+00:00"))
                age_hours = (now - ts).total_seconds() / 3600
                if age_hours > older_than_hours:
                    to_remove.append(market_id)
            except Exception:
                pass

        for market_id in to_remove:
            del self._snapshots[market_id]

        if to_remove:
            self.logger.info(f"Cleared {len(to_remove)} old snapshots")

        return len(to_remove)

    # ==============================================
    # Probability Shock Monitoring
    # ==============================================

    def scan_probability_shocks(
        self,
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Scan Polymarket markets for fast probability shocks.

        Uses a rolling baseline snapshot per market. If the baseline age exceeds
        lookback window, it is reset. Only changes above delta_threshold are returned.
        """
        result = {
            "timestamp": format_timestamp(now_utc()),
            "enabled": self.enabled,
            "tags_used": [],
            "events_scanned": 0,
            "markets_scanned": 0,
            "shocks_found": 0,
            "shocks": [],
            "all_deltas": [],  # All computed deltas (for UI Top Movers, Chart)
            "latest_quotes": {},
            "errors": [],
        }

        if not self.enabled:
            result["errors"].append("Arbitrage engine is disabled")
            return result

        shock_cfg = self.config.get("probability_shock", {})
        if not shock_cfg.get("enabled", True):
            result["errors"].append("Probability shock monitoring is disabled")
            return result

        if not self.provider.is_enabled:
            result["errors"].append("Polymarket provider is disabled")
            return result

        # Config
        max_results = shock_cfg.get("max_results", 20)
        max_events_per_tag = shock_cfg.get("max_events_per_tag", 20)
        events_limit = shock_cfg.get("events_limit", 50)
        events_page_size = shock_cfg.get("events_page_size", 200)
        events_max_pages = shock_cfg.get("events_max_pages", 10)

        term_cfg = self.config.get("term_structure", {})
        max_markets_per_event = term_cfg.get("max_markets_per_event", 10)

        shocks = []
        all_deltas = []
        latest_quotes = {}

        try:
            sector_tag_inputs = self._resolve_sector_tag_inputs()
            if sector_tag_inputs:
                markets_to_scan: list[PolymarketMarket] = []
                market_meta: dict[str, dict[str, str]] = {}
                seen_event_ids: set[str] = set()

                for sector, tag_inputs in sector_tag_inputs.items():
                    tag_ids = self.provider.resolve_tag_ids(tag_inputs)
                    if not tag_ids:
                        continue

                    for tag_id in tag_ids:
                        tag_events = self._fetch_tag_events(
                            tag_id=tag_id,
                            limit=events_limit,
                            page_size=events_page_size,
                            max_pages=events_max_pages,
                            max_events=max_events_per_tag,
                        )

                        for ev in tag_events:
                            if not ev.event_id or ev.event_id in seen_event_ids:
                                continue
                            seen_event_ids.add(ev.event_id)
                            markets = self._markets_from_event(ev)
                            if not markets:
                                markets = self.provider.get_markets_by_event(ev.event_id, ev.slug)
                            if not markets:
                                continue
                            if max_markets_per_event:
                                markets = markets[:max_markets_per_event]
                            result["markets_scanned"] += len(markets)
                            for market in markets:
                                market_meta[market.market_id] = {
                                    "event_id": ev.event_id,
                                    "event_title": ev.title,
                                    "sector": sector,
                                }
                                markets_to_scan.append(market)

                result["events_scanned"] = len(seen_event_ids)

                if self.shock_store:
                    self._seed_baselines(markets_to_scan)
                shocks, latest_quotes, all_deltas = self._scan_markets_for_shocks(markets_to_scan, market_meta)
            else:
                # Tags fallback
                tags = tags or self.config.get("polymarket_tags", [])
                tags = [t for t in tags if t]
                result["tags_used"] = tags

                if not tags:
                    result["errors"].append("No Polymarket tags configured")
                    return result

                tag_ids = self.provider.resolve_tag_ids(tags)
                if not tag_ids:
                    result["errors"].append("No Polymarket tag ids resolved")
                    return result
                result["tags_used"] = tag_ids

                events: list[Any] = []
                seen_event_ids = set()
                for tag_id in tag_ids:
                    tag_events = self._fetch_tag_events(
                        tag_id=tag_id,
                        limit=events_limit,
                        page_size=events_page_size,
                        max_pages=events_max_pages,
                        max_events=max_events_per_tag,
                    )
                    for ev in tag_events:
                        if ev.event_id and ev.event_id not in seen_event_ids:
                            events.append(ev)
                            seen_event_ids.add(ev.event_id)

                result["events_scanned"] = len(events)

                markets_to_scan: list[PolymarketMarket] = []
                market_meta: dict[str, dict[str, str]] = {}

                for ev in events:
                    markets = self._markets_from_event(ev)
                    if not markets:
                        markets = self.provider.get_markets_by_event(ev.event_id, ev.slug)
                    if not markets:
                        continue
                    if max_markets_per_event:
                        markets = markets[:max_markets_per_event]
                    result["markets_scanned"] += len(markets)
                    for market in markets:
                        market_meta[market.market_id] = {
                            "event_id": ev.event_id,
                            "event_title": ev.title,
                        }
                        markets_to_scan.append(market)

                if self.shock_store:
                    self._seed_baselines(markets_to_scan)
                shocks, latest_quotes, all_deltas = self._scan_markets_for_shocks(markets_to_scan, market_meta)

            # Sort by absolute delta
            shocks.sort(key=lambda x: abs(x.get("delta", 0)), reverse=True)
            all_deltas.sort(key=lambda x: abs(x.get("delta", 0)), reverse=True)
            if max_results:
                shocks = shocks[:max_results]

            result["shocks_found"] = len(shocks)
            result["shocks"] = shocks
            result["all_deltas"] = all_deltas  # All deltas for UI (Top Movers, Chart)
            result["latest_quotes"] = latest_quotes
            if self.shock_store:
                self._record_latest_quotes(latest_quotes)

        except Exception as e:
            self.logger.error(f"Probability shock scan error: {e}")
            result["errors"].append(str(e))

        return result

    def _fetch_recent_events(
        self,
        limit: int,
        page_size: int = 200,
        max_pages: int = 10,
    ) -> list[Any]:
        """Fetch recent events by paging and filtering expired."""
        events: list[Any] = []
        now = datetime.now(timezone.utc)
        offset = 0
        pages = 0

        while len(events) < limit and pages < max_pages:
            batch = self.provider.get_events(tag=None, active=True, limit=page_size, offset=offset)
            if not batch:
                break
            for ev in batch:
                if self._is_event_active(ev, now):
                    events.append(ev)
                    if len(events) >= limit:
                        break
            offset += page_size
            pages += 1

        return events

    def _fetch_tag_events(
        self,
        tag_id: str,
        limit: int,
        page_size: int = 200,
        max_pages: int = 5,
        max_events: Optional[int] = None,
    ) -> list[Any]:
        """Fetch recent events for a tag id with paging."""
        events: list[Any] = []
        offset = 0
        pages = 0
        cap = max_events or limit

        while len(events) < cap and pages < max_pages:
            batch = self.provider.get_events(
                tag_id=tag_id,
                active=True,
                closed=False,
                limit=min(page_size, limit),
                offset=offset,
                order="id",
                ascending=False,
            )
            if not batch:
                break
            for ev in batch:
                events.append(ev)
                if len(events) >= cap:
                    break
            offset += page_size
            pages += 1

        return events

    def _markets_from_event(self, ev) -> list[PolymarketMarket]:
        """Parse markets directly from event payload when available."""
        raw_markets = getattr(ev, "markets_data", None) or []
        if not raw_markets:
            return []
        markets: list[PolymarketMarket] = []
        for item in raw_markets:
            market = self.provider._parse_market(item, ev.event_id)
            if market:
                markets.append(market)
        return markets

    def _resolve_sector_tag_inputs(self) -> dict[str, list[str]]:
        """Resolve sector -> tag inputs (slugs/keywords) from config."""
        sector_tags = self.config.get("polymarket_sector_tags", {})
        if sector_tags:
            return {k: v for k, v in sector_tags.items() if v}

        sectors = self.config.get("polymarket_sectors", {})
        if sectors:
            return {k: v for k, v in sectors.items() if v}

        return {}

    @staticmethod
    def _is_event_active(ev, now: datetime) -> bool:
        """Check if event end_date is in the future (if available)."""
        end_date = getattr(ev, "end_date", None) or getattr(ev, "end_time", None)
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                return end_dt > now
            except Exception:
                pass
        return True

    @staticmethod
    def _match_event_sector(ev, sectors: dict[str, list[str]], category_map: dict[str, list[str]]) -> Optional[str]:
        """Match event to a sector based on category or keyword hits."""
        category = (getattr(ev, "category", None) or "").strip()
        if category_map:
            for sector, cats in category_map.items():
                if category in cats:
                    return sector
            # If category map is present and no match, skip keyword fallback
            return None

        # Fallback to keyword match on title/description
        text = f"{getattr(ev, 'title', '')} {getattr(ev, 'description', '')}".lower()
        for sector, keywords in sectors.items():
            for kw in keywords:
                kw_l = kw.lower()
                if " " in kw_l:
                    if kw_l in text:
                        return sector
                else:
                    # Word boundary match for short tokens
                    import re
                    if re.search(rf"\\b{re.escape(kw_l)}\\b", text):
                        return sector
        return None

    def scan_probability_shocks_for_markets(
        self,
        markets: list[PolymarketMarket],
        market_meta: Optional[dict[str, dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """
        Scan a specific list of markets for probability shocks.

        Args:
            markets: List of PolymarketMarket objects
            market_meta: Optional metadata per market_id (event_id/title)
        """
        result = {
            "timestamp": format_timestamp(now_utc()),
            "enabled": self.enabled,
            "tags_used": [],
            "events_scanned": 0,
            "markets_scanned": len(markets),
            "shocks_found": 0,
            "shocks": [],
            "all_deltas": [],
            "latest_quotes": {},
            "errors": [],
        }

        if not self.enabled:
            result["errors"].append("Arbitrage engine is disabled")
            return result

        shock_cfg = self.config.get("probability_shock", {})
        if not shock_cfg.get("enabled", True):
            result["errors"].append("Probability shock monitoring is disabled")
            return result

        if not self.provider.is_enabled:
            result["errors"].append("Polymarket provider is disabled")
            return result

        try:
            if self.shock_store:
                self._seed_baselines(markets)
            shocks, latest_quotes, all_deltas = self._scan_markets_for_shocks(markets, market_meta or {})
            result["shocks_found"] = len(shocks)
            result["shocks"] = shocks
            result["all_deltas"] = all_deltas
            result["latest_quotes"] = latest_quotes
            if self.shock_store:
                self._record_latest_quotes(latest_quotes)
        except Exception as e:
            self.logger.error(f"Probability shock scan error: {e}")
            result["errors"].append(str(e))

        return result

    def _scan_markets_for_shocks(
        self,
        markets: list[PolymarketMarket],
        market_meta: Optional[dict[str, dict[str, str]]] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Evaluate a list of markets for probability shocks.

        Returns:
            Tuple of (shocks, latest_quotes, all_deltas)
        """
        shock_cfg = self.config.get("probability_shock", {})
        lookback_minutes = shock_cfg.get("lookback_minutes", 60)
        min_age_seconds = shock_cfg.get("min_age_seconds", 60)
        delta_threshold = shock_cfg.get("delta_threshold", 0.05)
        allow_illiquid = shock_cfg.get("allow_illiquid_quotes", False)
        allow_stale = shock_cfg.get("allow_stale_baseline", False)

        risk_cfg = self.config.get("risk", {})
        min_volume = risk_cfg.get("min_volume_24h", 5000)
        max_spread_bps = risk_cfg.get("max_spread_bps", 300)
        min_depth_usd = risk_cfg.get("min_depth_usd", 1000)
        min_time_to_settle_hours = risk_cfg.get("min_time_to_settle_hours", 12)

        now = datetime.now(timezone.utc)
        shocks: list[dict[str, Any]] = []
        all_deltas: list[dict[str, Any]] = []  # Track ALL deltas for UI
        latest_quotes: dict[str, dict[str, Any]] = {}
        meta = market_meta or {}

        for market in markets:
            if not market.active:
                continue

            # Basic volume filter
            if market.volume < min_volume:
                continue

            # Time to settle filter
            if min_time_to_settle_hours and market.tenor_days:
                if market.tenor_days * 24 < min_time_to_settle_hours:
                    continue

            quote = self.provider.get_quote(market)
            if not quote:
                continue

            latest_quotes[market.market_id] = {
                "market_id": market.market_id,
                "event_id": meta.get(market.market_id, {}).get("event_id", market.event_id),
                "question": market.question,
                "mid": quote.mid,
                "end_time": market.end_time,
                "timestamp": datetime.now(timezone.utc),
            }

            # Liquidity/spread filters
            depth_usd = min(quote.depth_bid, quote.depth_ask)
            if not allow_illiquid:
                if quote.spread_bps > max_spread_bps:
                    continue
                if depth_usd < min_depth_usd:
                    continue

            # Baseline snapshot
            snapshot = self._shock_snapshots.get(market.market_id)
            if not snapshot:
                baseline = None
                if self.shock_store:
                    baseline = self.shock_store.get_latest_price(market.market_id)
                if baseline:
                    baseline_ts = baseline["timestamp"]
                    # Ensure baseline_ts is timezone-aware
                    if baseline_ts.tzinfo is None:
                        baseline_ts = baseline_ts.replace(tzinfo=timezone.utc)
                    age_seconds = (now - baseline_ts).total_seconds()
                    if age_seconds >= min_age_seconds or allow_stale:
                        self._shock_snapshots[market.market_id] = ArbSnapshot(
                            market_id=market.market_id,
                            news_id="shock_store",
                            first_seen_ts=baseline_ts.isoformat(),
                            p0=baseline["mid"],
                            quote0=None,
                            volume0=None,
                        )
                        snapshot = self._shock_snapshots[market.market_id]
                    else:
                        self._shock_snapshots[market.market_id] = ArbSnapshot(
                            market_id=market.market_id,
                            news_id="shock_scan",
                            first_seen_ts=format_timestamp(now_utc()),
                            p0=quote.mid,
                            quote0=quote.to_dict(),
                            volume0=quote.volume_24h,
                        )
                        continue
                else:
                    self._shock_snapshots[market.market_id] = ArbSnapshot(
                        market_id=market.market_id,
                        news_id="shock_scan",
                        first_seen_ts=format_timestamp(now_utc()),
                        p0=quote.mid,
                        quote0=quote.to_dict(),
                        volume0=quote.volume_24h,
                    )
                    continue

            # Age check
            try:
                baseline_ts = datetime.fromisoformat(
                    snapshot.first_seen_ts.replace("Z", "+00:00")
                )
                age_seconds = (now - baseline_ts).total_seconds()
            except Exception:
                # Reset baseline on parse failure
                self._shock_snapshots[market.market_id] = ArbSnapshot(
                    market_id=market.market_id,
                    news_id="shock_scan",
                    first_seen_ts=format_timestamp(now_utc()),
                    p0=quote.mid,
                    quote0=quote.to_dict(),
                    volume0=quote.volume_24h,
                )
                continue

            # Reset baseline if too old
            if age_seconds > lookback_minutes * 60 and not allow_stale:
                self._shock_snapshots[market.market_id] = ArbSnapshot(
                    market_id=market.market_id,
                    news_id="shock_scan",
                    first_seen_ts=format_timestamp(now_utc()),
                    p0=quote.mid,
                    quote0=quote.to_dict(),
                    volume0=quote.volume_24h,
                )
                continue

            # Skip if baseline too fresh
            if age_seconds < min_age_seconds:
                continue

            delta = quote.mid - snapshot.p0

            # Build delta entry (used for both shocks and all_deltas)
            meta_entry = meta.get(market.market_id, {})
            delta_entry = {
                "event_id": meta_entry.get("event_id", market.event_id),
                "event_title": meta_entry.get("event_title", ""),
                "sector": meta_entry.get("sector", ""),
                "market_id": market.market_id,
                "question": market.question,
                "tags": market.tags,
                "current_mid": quote.mid,
                "baseline_mid": snapshot.p0,
                "delta": delta,
                "age_minutes": age_seconds / 60.0,
                "volume_24h": quote.volume_24h or market.volume,
                "liquidity": market.liquidity,
                "spread_bps": quote.spread_bps,
                "depth_usd": depth_usd,
                "end_time": market.end_time,
                "tenor_days": market.tenor_days,
                "timestamp": format_timestamp(now_utc()),
            }

            # Always add to all_deltas for UI
            all_deltas.append(delta_entry)

            # Only add to shocks if above threshold
            if abs(delta) >= delta_threshold:
                shocks.append(delta_entry)

        return shocks, latest_quotes, all_deltas

    def _seed_baselines(self, markets: list[PolymarketMarket]) -> None:
        """Seed in-memory baselines from shock store."""
        if not self.shock_store:
            return
        shock_cfg = self.config.get("probability_shock", {})
        lookback_minutes = shock_cfg.get("lookback_minutes", 60)
        min_age_seconds = shock_cfg.get("min_age_seconds", 60)
        for market in markets:
            if market.market_id in self._shock_snapshots:
                continue
            baseline = self.shock_store.get_baseline(
                market_id=market.market_id,
                lookback_minutes=lookback_minutes,
                min_age_seconds=min_age_seconds,
            )
            if baseline:
                self._shock_snapshots[market.market_id] = ArbSnapshot(
                    market_id=market.market_id,
                    news_id="shock_store",
                    first_seen_ts=baseline["timestamp"].isoformat(),
                    p0=baseline["mid"],
                    quote0=None,
                    volume0=None,
                )

    def _record_latest_quotes(self, latest_quotes: dict[str, dict[str, Any]]) -> None:
        """Persist latest quotes to shock store."""
        if not self.shock_store:
            return
        for market_id, info in latest_quotes.items():
            end_time = info.get("end_time")
            if isinstance(end_time, str) and end_time:
                try:
                    end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                except Exception:
                    end_time = None
            self.shock_store.record_price(
                market_id=market_id,
                event_id=info.get("event_id"),
                question=info.get("question"),
                mid=info.get("mid", 0.0),
                timestamp=info.get("timestamp"),
                end_time=end_time,
            )

    # ==============================================
    # News Integration (Multi-provider with fallback)
    # ==============================================

    def scan_news(self) -> list[ArbOpportunity]:
        """
        Scan news from multiple providers and trigger arbitrage detection.

        Uses NewsRouter for intelligent provider selection with fallback.

        Returns:
            List of confirmed opportunities across all triggered news
        """
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled")
            return []

        try:
            from fingent.providers.news_router import get_news_router
            news_router = get_news_router()
        except Exception as e:
            self.logger.error(f"Failed to initialize NewsRouter: {e}")
            # Fallback to Finnhub only
            return self.scan_finnhub_news()

        # Fetch news from best available provider
        self.logger.info("Fetching news from available providers...")
        news_items = news_router.get_market_news(limit=30)

        if not news_items:
            self.logger.info("No news items found from any provider")
            return []

        self.logger.info(f"Processing {len(news_items)} news items...")

        all_opportunities = []
        triggered_count = 0

        for item in news_items:
            opportunities = self.process_news(
                headline=item.title,
                summary=item.summary,
                news_id=item.url or item.title[:50],
            )

            if opportunities:
                triggered_count += 1
                all_opportunities.extend(opportunities)

        self.logger.info(
            f"News scan complete: {triggered_count} triggered, "
            f"{len(all_opportunities)} opportunities found"
        )

        return all_opportunities

    def scan_finnhub_news(
        self,
        finnhub_provider: Optional[FinnhubProvider] = None,
        category: str = "general",
    ) -> list[ArbOpportunity]:
        """
        Scan Finnhub news only (legacy method, kept for backwards compatibility).

        For multi-provider support, use scan_news() instead.

        Args:
            finnhub_provider: Finnhub provider instance (creates one if not provided)
            category: News category (general, forex, crypto, merger)

        Returns:
            List of confirmed opportunities across all triggered news
        """
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled")
            return []

        # Initialize Finnhub provider
        if finnhub_provider is None:
            try:
                finnhub_provider = FinnhubProvider()
            except Exception as e:
                self.logger.error(f"Failed to initialize Finnhub provider: {e}")
                return []

        # Fetch news
        self.logger.info(f"Fetching {category} news from Finnhub...")
        news_items = finnhub_provider.get_market_news(category)

        if not news_items:
            self.logger.info("No news items found")
            return []

        self.logger.info(f"Processing {len(news_items)} news items...")

        all_opportunities = []
        triggered_count = 0

        for item in news_items:
            opportunities = self.process_news(
                headline=item.title,
                summary=item.summary,
                news_id=item.url or item.title[:50],
            )

            if opportunities:
                triggered_count += 1
                all_opportunities.extend(opportunities)

        self.logger.info(
            f"News scan complete: {triggered_count} triggered, "
            f"{len(all_opportunities)} opportunities found"
        )

        return all_opportunities

    def run_full_pipeline(
        self,
        use_finnhub: bool = True,
        finnhub_category: str = "general",
    ) -> dict[str, Any]:
        """
        Run the full arbitrage pipeline.

        1. Fetch news from available providers (multi-provider with fallback)
        2. Check for keyword triggers
        3. Scan Polymarket for matching events
        4. Detect term structure opportunities
        5. Apply risk filters
        6. Return results

        Args:
            use_finnhub: Whether to use news for trigger (now uses NewsRouter)
            finnhub_category: News category (kept for backwards compatibility)

        Returns:
            Pipeline result dict with stats and opportunities
        """
        result = {
            "timestamp": format_timestamp(now_utc()),
            "enabled": self.enabled,
            "news_scanned": 0,
            "news_triggered": 0,
            "news_providers_used": [],
            "events_found": 0,
            "opportunities_raw": 0,
            "opportunities_confirmed": 0,
            "opportunities": [],
            "errors": [],
        }

        if not self.enabled:
            result["errors"].append("Arbitrage engine is disabled")
            return result

        try:
            if use_finnhub:
                # Use NewsRouter for multi-provider support
                try:
                    from fingent.providers.news_router import get_news_router
                    news_router = get_news_router()
                    news_items = news_router.get_market_news(limit=30)

                    # Get provider stats
                    stats = news_router.get_stats()
                    result["news_providers_used"] = [
                        name for name, s in stats.items()
                        if s.get("calls_today", 0) > 0
                    ]

                except Exception as e:
                    self.logger.warning(f"NewsRouter failed, falling back to Finnhub: {e}")
                    finnhub = FinnhubProvider()
                    news_items = finnhub.get_market_news(finnhub_category)
                    result["news_providers_used"] = ["finnhub"]

                result["news_scanned"] = len(news_items)

                for item in news_items:
                    matched = self.check_news_trigger(item.title, item.summary)
                    if matched:
                        result["news_triggered"] += 1

                opportunities = self.scan_news()
            else:
                # Manual scan without news trigger
                opportunities = self.run_scan()

            result["opportunities_confirmed"] = len(opportunities)
            result["opportunities"] = [o.to_dict() for o in opportunities]

        except Exception as e:
            self.logger.error(f"Pipeline error: {e}")
            result["errors"].append(str(e))

        return result
