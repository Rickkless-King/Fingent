"""
Prediction market monitoring service.

Low-frequency full scan builds hotspot list.
High-frequency scan monitors hotspots for probability shocks and sends Telegram alerts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fingent.arb.engine import ArbEngine
from fingent.core.config import get_settings, load_yaml_config
from fingent.core.logging import LoggerMixin
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import PolymarketMarket
from fingent.providers.polymarket import PolymarketProvider
from fingent.services.telegram import create_telegram_service
from fingent.services.shock_store import ShockStore


@dataclass
class HotspotItem:
    event_id: str
    event_title: str
    market: dict[str, Any]
    activity_score: float
    last_seen: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_title": self.event_title,
            "market": self.market,
            "activity_score": self.activity_score,
            "last_seen": self.last_seen,
        }


class ShockMonitor(LoggerMixin):
    """
    Monitor for probability shocks.
    """

    def __init__(
        self,
        provider: Optional[PolymarketProvider] = None,
        config: Optional[dict] = None,
    ):
        settings = get_settings()
        self.settings = settings
        full_config = config or load_yaml_config()
        self.config = full_config
        self.monitor_cfg = full_config.get("monitoring", {})
        self.arb_cfg = full_config.get("arbitrage", {})

        self.enabled = self.monitor_cfg.get("enabled", True)

        self.provider = provider or PolymarketProvider()
        self.store = ShockStore()
        self.engine = ArbEngine(provider=self.provider, config=self.arb_cfg, shock_store=self.store)

        self.data_dir = Path("data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.hotspots_path = self.data_dir / "hotspots.json"
        self.cooldown_path = self.data_dir / "shock_cooldown.json"

    def scan_full(self) -> list[dict[str, Any]]:
        """
        Low-frequency full scan to build hotspot list.
        """
        if not self.enabled:
            self.logger.info("Monitoring disabled; skipping full scan")
            return []

        # Purge expired markets before rebuilding hotspots
        purge_stats = self.store.purge_expired()
        if purge_stats.get("markets"):
            self.logger.info(
                f"Purged expired markets: {purge_stats.get('markets')} "
                f"(prices: {purge_stats.get('prices')})"
            )

        hotspots = self._collect_hotspots()
        self._save_hotspots(hotspots)
        self.logger.info(f"Hotspots updated: {len(hotspots)} items")
        return hotspots

    def scan_hotspots(self) -> list[dict[str, Any]]:
        """
        High-frequency scan for probability shocks on hotspots.
        """
        if not self.enabled:
            self.logger.info("Monitoring disabled; skipping hotspot scan")
            return []

        hotspots = self._load_hotspots()
        if not hotspots:
            self.logger.info("No hotspots available; run full scan first")
            return []

        markets: list[PolymarketMarket] = []
        meta: dict[str, dict[str, str]] = {}

        for item in hotspots:
            market_dict = item.get("market", {})
            try:
                market = PolymarketMarket.from_dict(market_dict)
                markets.append(market)
                meta[market.market_id] = {
                    "event_id": item.get("event_id", ""),
                    "event_title": item.get("event_title", ""),
                    "sector": market_dict.get("sector", ""),
                }
            except Exception:
                continue

        if not markets:
            return []

        result = self.engine.scan_probability_shocks_for_markets(markets, meta)
        shocks = result.get("shocks", [])
        shocks = self._apply_cooldown(shocks)

        if shocks:
            telegram = create_telegram_service()
            for shock in shocks:
                telegram.send_shock(shock)
            self._update_cooldown(shocks)

        self.logger.info(f"Hotspot scan: {len(shocks)} shocks sent")
        return shocks

    # ==============================================
    # Hotspot Collection
    # ==============================================

    def _collect_hotspots(self) -> list[dict[str, Any]]:
        sector_tags = self._resolve_sector_tag_inputs()
        tags = self.arb_cfg.get("polymarket_tags", [])
        tags = [t for t in tags if t]

        max_events_per_tag = self.monitor_cfg.get("max_events_per_tag", 20)
        max_markets_per_event = self.monitor_cfg.get("max_markets_per_event", 10)
        hotspots_limit = self.monitor_cfg.get("hotspots_limit", 30)
        min_volume = self.monitor_cfg.get("hotspot_min_volume", 3000)

        risk_cfg = self.arb_cfg.get("risk", {})
        min_depth_usd = self.monitor_cfg.get("hotspot_min_depth_usd", risk_cfg.get("min_depth_usd", 1000))
        max_spread_bps = self.monitor_cfg.get("hotspot_max_spread_bps", risk_cfg.get("max_spread_bps", 300))
        min_time_to_settle_hours = risk_cfg.get("min_time_to_settle_hours", 12)

        hotspots: list[HotspotItem] = []
        now_ts = format_timestamp(now_utc())

        if sector_tags:
            for sector, tag_inputs in sector_tags.items():
                tag_ids = self.provider.resolve_tag_ids(tag_inputs)
                if not tag_ids:
                    continue
                seen_event_ids: set[str] = set()
                for tag_id in tag_ids:
                    tag_events = self._fetch_tag_events(
                        tag_id=tag_id,
                        limit=200,
                        page_size=200,
                        max_pages=5,
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

                        for market in markets:
                            if not market.active:
                                continue
                            if market.volume < min_volume:
                                continue
                            if min_time_to_settle_hours and market.tenor_days:
                                if market.tenor_days * 24 < min_time_to_settle_hours:
                                    continue

                            quote = self.provider.get_quote(market)
                            if not quote:
                                continue

                            depth_usd = min(quote.depth_bid, quote.depth_ask)
                            if quote.spread_bps > max_spread_bps:
                                continue
                            if depth_usd < min_depth_usd:
                                continue

                            volume_24h = quote.volume_24h or market.volume
                            activity_score = float(volume_24h or 0) + float(depth_usd or 0) * 10.0

                            item = market.to_dict()
                            item["sector"] = sector
                            hotspots.append(
                                HotspotItem(
                                    event_id=ev.event_id,
                                    event_title=ev.title,
                                    market=item,
                                    activity_score=activity_score,
                                    last_seen=now_ts,
                                )
                            )
        elif tags:
            tag_ids = self.provider.resolve_tag_ids(tags)
            if tag_ids:
                events = []
                seen_event_ids = set()
                for tag_id in tag_ids:
                    tag_events = self._fetch_tag_events(
                        tag_id=tag_id,
                        limit=200,
                        page_size=200,
                        max_pages=5,
                        max_events=max_events_per_tag,
                    )
                    for ev in tag_events:
                        if ev.event_id and ev.event_id not in seen_event_ids:
                            events.append(ev)
                            seen_event_ids.add(ev.event_id)

                for ev in events:
                    markets = self._markets_from_event(ev)
                    if not markets:
                        markets = self.provider.get_markets_by_event(ev.event_id, ev.slug)
                    if not markets:
                        continue
                    if max_markets_per_event:
                        markets = markets[:max_markets_per_event]

                    for market in markets:
                        if not market.active:
                            continue
                        if market.volume < min_volume:
                            continue
                        if min_time_to_settle_hours and market.tenor_days:
                            if market.tenor_days * 24 < min_time_to_settle_hours:
                                continue

                        quote = self.provider.get_quote(market)
                        if not quote:
                            continue

                        depth_usd = min(quote.depth_bid, quote.depth_ask)
                        if quote.spread_bps > max_spread_bps:
                            continue
                        if depth_usd < min_depth_usd:
                            continue

                        volume_24h = quote.volume_24h or market.volume
                        activity_score = float(volume_24h or 0) + float(depth_usd or 0) * 10.0

                        hotspots.append(
                            HotspotItem(
                                event_id=ev.event_id,
                                event_title=ev.title,
                                market=market.to_dict(),
                                activity_score=activity_score,
                                last_seen=now_ts,
                            )
                        )

        hotspots.sort(key=lambda x: x.activity_score, reverse=True)
        if hotspots_limit:
            hotspots = hotspots[:hotspots_limit]

        return [h.to_dict() for h in hotspots]

    def _resolve_sector_tag_inputs(self) -> dict[str, list[str]]:
        sector_tags = self.arb_cfg.get("polymarket_sector_tags", {})
        if sector_tags:
            return {k: v for k, v in sector_tags.items() if v}
        sectors = self.arb_cfg.get("polymarket_sectors", {})
        if sectors:
            return {k: v for k, v in sectors.items() if v}
        return {}

    def _fetch_tag_events(
        self,
        tag_id: str,
        limit: int,
        page_size: int = 200,
        max_pages: int = 5,
        max_events: Optional[int] = None,
    ) -> list[Any]:
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
        raw_markets = getattr(ev, "markets_data", None) or []
        if not raw_markets:
            return []
        markets: list[PolymarketMarket] = []
        for item in raw_markets:
            market = self.provider._parse_market(item, ev.event_id)
            if market:
                markets.append(market)
        return markets

    # ==============================================
    # Cooldown & Storage
    # ==============================================

    def _load_hotspots(self) -> list[dict[str, Any]]:
        if not self.hotspots_path.exists():
            return []
        try:
            with self.hotspots_path.open("r", encoding="utf-8") as f:
                return json.load(f) or []
        except Exception:
            return []

    def _save_hotspots(self, hotspots: list[dict[str, Any]]) -> None:
        with self.hotspots_path.open("w", encoding="utf-8") as f:
            json.dump(hotspots, f, ensure_ascii=True, indent=2)

    def _load_cooldown(self) -> dict[str, str]:
        if not self.cooldown_path.exists():
            return {}
        try:
            with self.cooldown_path.open("r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _save_cooldown(self, data: dict[str, str]) -> None:
        with self.cooldown_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True, indent=2)

    def _apply_cooldown(self, shocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cooldown_minutes = self.monitor_cfg.get("cooldown_minutes", 30)
        if cooldown_minutes <= 0:
            return shocks

        cooldown_map = self._load_cooldown()
        now = datetime.now(timezone.utc)
        result = []

        for shock in shocks:
            market_id = shock.get("market_id", "")
            if not market_id:
                continue
            last_ts = cooldown_map.get(market_id)
            if last_ts:
                try:
                    last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    elapsed = (now - last_dt).total_seconds() / 60.0
                    if elapsed < cooldown_minutes:
                        continue
                except Exception:
                    pass
            result.append(shock)

        return result

    def _update_cooldown(self, shocks: list[dict[str, Any]]) -> None:
        cooldown_map = self._load_cooldown()
        now_ts = format_timestamp(now_utc())
        for shock in shocks:
            market_id = shock.get("market_id", "")
            if market_id:
                cooldown_map[market_id] = now_ts
        self._save_cooldown(cooldown_map)


def create_shock_monitor(
    provider: Optional[PolymarketProvider] = None,
    config: Optional[dict] = None,
) -> ShockMonitor:
    return ShockMonitor(provider=provider, config=config)
