"""
Cross Asset node - Analyzes cross-asset correlations and divergences.

Data sources: Finnhub (US equity), OKX (crypto)
Signals produced: risk_on, risk_off, flight_to_safety, etc.
"""

from typing import Any, Optional

from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.signals import SignalDirection, SignalName, create_signal
from fingent.nodes.base import BaseNode
from fingent.providers.finnhub import FinnhubProvider
from fingent.providers.okx import OKXProvider


class CrossAssetNode(BaseNode):
    """
    Cross-asset analysis node.

    Analyzes relationships between:
    - US equities (SPY, QQQ)
    - Safe havens (GLD, TLT)
    - Crypto (BTC, ETH)
    - Volatility (VIX)
    """

    node_name = "cross_asset"

    # Asset symbols to track
    US_EQUITY_SYMBOLS = ["SPY", "QQQ"]
    SAFE_HAVEN_SYMBOLS = ["GLD", "TLT"]
    CRYPTO_SYMBOLS = ["BTC-USDT", "ETH-USDT"]

    def __init__(
        self,
        *args,
        finnhub_provider: Optional[FinnhubProvider] = None,
        okx_provider: Optional[OKXProvider] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.finnhub = finnhub_provider or FinnhubProvider()
        self.okx = okx_provider or OKXProvider()

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Analyze cross-asset relationships.

        Returns:
            State update with market_data and signals
        """
        run_id = self.get_run_id(state)
        existing_signals = self.get_existing_signals(state)
        errors = []
        signals = []

        # Fetch market data
        market_data = self._fetch_market_data(errors)

        # Analyze and produce signals
        if market_data.get("assets"):
            signals = self._analyze_cross_asset(market_data, run_id)

        # Merge signals
        all_signals = self.merge_signals(existing_signals, signals)

        return {
            "market_data": market_data,
            "signals": all_signals,
            "errors": state.get("errors", []) + errors,
        }

    def _fetch_market_data(self, errors: list) -> dict[str, Any]:
        """Fetch market data from all sources."""
        market_data = {
            "timestamp": format_timestamp(now_utc()),
            "assets": {},
            "changes": {},
        }

        # Fetch US equity data
        try:
            for symbol in self.US_EQUITY_SYMBOLS:
                quote = self.finnhub.get_quote(symbol)
                if quote:
                    market_data["assets"][symbol] = quote.to_dict()

                    # Get 7d change
                    changes = self.finnhub.calculate_price_changes(symbol)
                    market_data["changes"][symbol] = changes

            # Fetch safe haven data
            for symbol in self.SAFE_HAVEN_SYMBOLS:
                quote = self.finnhub.get_quote(symbol)
                if quote:
                    market_data["assets"][symbol] = quote.to_dict()
                    changes = self.finnhub.calculate_price_changes(symbol)
                    market_data["changes"][symbol] = changes

            # Fetch VIX
            vix_quote = self.finnhub.get_quote("VIX")
            if vix_quote:
                market_data["assets"]["VIX"] = vix_quote.to_dict()
                market_data["vix_level"] = vix_quote.price

            self.logger.info(f"Fetched {len(market_data['assets'])} equity quotes")

        except Exception as e:
            self.logger.error(f"Failed to fetch equity data: {e}")
            errors.append(self.create_error(f"Finnhub fetch failed: {e}"))

        # Fetch crypto data
        try:
            crypto_tickers = self.okx.get_tickers(self.CRYPTO_SYMBOLS)
            for symbol, ticker in crypto_tickers.items():
                market_data["assets"][symbol] = ticker.to_dict()

                # Get 7d change
                changes = self.okx.calculate_price_changes(symbol)
                market_data["changes"][symbol] = changes

            self.logger.info(f"Fetched {len(crypto_tickers)} crypto quotes")

        except Exception as e:
            self.logger.error(f"Failed to fetch crypto data: {e}")
            errors.append(self.create_error(f"OKX fetch failed: {e}"))

        return market_data

    def _analyze_cross_asset(
        self,
        market_data: dict[str, Any],
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Analyze cross-asset relationships."""
        signals = []
        assets = market_data.get("assets", {})
        changes = market_data.get("changes", {})

        # === Risk On/Off Analysis ===
        risk_signal = self._analyze_risk_sentiment(assets, changes, run_id)
        if risk_signal:
            signals.append(risk_signal)

        # === VIX Analysis ===
        vix_signal = self._analyze_vix(market_data.get("vix_level"), run_id)
        if vix_signal:
            signals.append(vix_signal)

        # === Flight to Safety ===
        safety_signal = self._analyze_flight_to_safety(changes, run_id)
        if safety_signal:
            signals.append(safety_signal)

        # === Crypto Momentum ===
        crypto_signal = self._analyze_crypto_momentum(changes, run_id)
        if crypto_signal:
            signals.append(crypto_signal)

        return signals

    def _analyze_risk_sentiment(
        self,
        assets: dict,
        changes: dict,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze overall risk sentiment."""
        evidence = {}
        risk_score = 0

        # SPY performance - 更细致的评分
        spy_change = changes.get("SPY", {}).get("change_24h")
        if spy_change is not None:
            evidence["spy_24h"] = round(spy_change * 100, 2)  # 转为百分比显示
            if spy_change > 0.015:
                risk_score += 1.5
            elif spy_change > 0.005:
                risk_score += 1
            elif spy_change > 0:
                risk_score += 0.5
            elif spy_change < -0.015:
                risk_score -= 1.5
            elif spy_change < -0.005:
                risk_score -= 1
            elif spy_change < 0:
                risk_score -= 0.5

        # BTC performance (risk asset) - 更细致的评分
        btc_change = changes.get("BTC-USDT", {}).get("change_24h")
        if btc_change is not None:
            evidence["btc_24h"] = round(btc_change * 100, 2)
            if btc_change > 0.03:
                risk_score += 1.5
            elif btc_change > 0.01:
                risk_score += 1
            elif btc_change > 0:
                risk_score += 0.3
            elif btc_change < -0.03:
                risk_score -= 1.5
            elif btc_change < -0.01:
                risk_score -= 1
            elif btc_change < 0:
                risk_score -= 0.3

        # Gold performance (inverse risk)
        gld_change = changes.get("GLD", {}).get("change_24h")
        if gld_change is not None:
            evidence["gold_24h"] = round(gld_change * 100, 2)
            if gld_change > 0.01:
                risk_score -= 0.5  # Gold up = risk off
            elif gld_change < -0.01:
                risk_score += 0.5  # Gold down = risk on

        # 降低阈值，使信号更容易产出
        if risk_score >= 1.0:
            return create_signal(
                name=SignalName.RISK_ON.value,
                direction=SignalDirection.BULLISH.value,
                score=min(risk_score / 3, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.4 + min(abs(risk_score) * 0.1, 0.4),
                evidence=evidence,
            )
        elif risk_score <= -1.0:
            return create_signal(
                name=SignalName.RISK_OFF.value,
                direction=SignalDirection.BEARISH.value,
                score=max(risk_score / 3, -1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.4 + min(abs(risk_score) * 0.1, 0.4),
                evidence=evidence,
            )
        else:
            # 市场情绪中性 - 总是产出一个信号
            return create_signal(
                name="market_neutral",
                direction=SignalDirection.NEUTRAL.value,
                score=risk_score / 3,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.4,
                evidence=evidence,
            )

    def _analyze_vix(
        self,
        vix_level: Optional[float],
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze VIX levels."""
        if vix_level is None:
            return None

        evidence = {"vix_level": round(vix_level, 2)}

        # VIX 长期均值约为 19-20
        if vix_level >= 30:
            # 恐慌
            return create_signal(
                name=SignalName.VIX_SPIKE.value,
                direction=SignalDirection.BEARISH.value,
                score=min((vix_level - 20) / 20, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.9,
                evidence=evidence,
            )
        elif vix_level >= 25:
            # 高波动
            return create_signal(
                name=SignalName.VIX_ELEVATED.value,
                direction=SignalDirection.BEARISH.value,
                score=0.5,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.8,
                evidence=evidence,
            )
        elif vix_level >= 20:
            # 略高于正常
            return create_signal(
                name=SignalName.VIX_ELEVATED.value,
                direction=SignalDirection.BEARISH.value,
                score=0.3,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.6,
                evidence=evidence,
            )
        elif vix_level < 15:
            # 低波动/乐观
            return create_signal(
                name=SignalName.VIX_CALM.value,
                direction=SignalDirection.BULLISH.value,
                score=0.4,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.7,
                evidence=evidence,
            )
        else:
            # 正常波动 (15-20)
            return create_signal(
                name="vix_normal",
                direction=SignalDirection.NEUTRAL.value,
                score=0,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5,
                evidence=evidence,
            )

    def _analyze_flight_to_safety(
        self,
        changes: dict,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Detect flight to safety patterns."""
        evidence = {}

        spy_change = changes.get("SPY", {}).get("change_24h")
        gld_change = changes.get("GLD", {}).get("change_24h")
        tlt_change = changes.get("TLT", {}).get("change_24h")

        if spy_change is None:
            return None

        evidence["spy_24h"] = spy_change

        # Flight to safety: stocks down, gold/bonds up
        if spy_change < -0.01:
            safety_score = 0

            if gld_change and gld_change > 0.005:
                safety_score += 1
                evidence["gold_24h"] = gld_change

            if tlt_change and tlt_change > 0.005:
                safety_score += 1
                evidence["tlt_24h"] = tlt_change

            if safety_score >= 1:
                return create_signal(
                    name=SignalName.FLIGHT_TO_SAFETY.value,
                    direction=SignalDirection.BEARISH.value,
                    score=min(safety_score * 0.4, 1.0),
                    source_node=self.node_name,
                    run_id=run_id,
                    confidence=0.7,
                    evidence=evidence,
                )

        return None

    def _analyze_crypto_momentum(
        self,
        changes: dict,
        run_id: str,
    ) -> Optional[dict[str, Any]]:
        """Analyze crypto market momentum."""
        btc_change = changes.get("BTC-USDT", {}).get("change_24h")
        eth_change = changes.get("ETH-USDT", {}).get("change_24h")

        if btc_change is None:
            return None

        evidence = {"btc_24h": round(btc_change * 100, 2)}
        if eth_change:
            evidence["eth_24h"] = round(eth_change * 100, 2)

        # 计算平均变化
        avg_change = btc_change
        if eth_change:
            avg_change = (btc_change + eth_change) / 2

        # 降低阈值，更敏感地检测动量
        if avg_change > 0.05:
            # 强劲上涨
            return create_signal(
                name=SignalName.CRYPTO_MOMENTUM.value,
                direction=SignalDirection.BULLISH.value,
                score=min(avg_change / 0.1, 1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.7,
                evidence=evidence,
            )
        elif avg_change > 0.02:
            # 温和上涨
            return create_signal(
                name=SignalName.CRYPTO_MOMENTUM.value,
                direction=SignalDirection.BULLISH.value,
                score=avg_change / 0.1,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5,
                evidence=evidence,
            )
        elif avg_change < -0.05:
            # 强劲下跌
            return create_signal(
                name=SignalName.CRYPTO_MOMENTUM.value,
                direction=SignalDirection.BEARISH.value,
                score=max(avg_change / 0.1, -1.0),
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.7,
                evidence=evidence,
            )
        elif avg_change < -0.02:
            # 温和下跌
            return create_signal(
                name=SignalName.CRYPTO_MOMENTUM.value,
                direction=SignalDirection.BEARISH.value,
                score=avg_change / 0.1,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.5,
                evidence=evidence,
            )
        else:
            # 横盘震荡
            return create_signal(
                name="crypto_sideways",
                direction=SignalDirection.NEUTRAL.value,
                score=avg_change / 0.1,
                source_node=self.node_name,
                run_id=run_id,
                confidence=0.4,
                evidence=evidence,
            )
