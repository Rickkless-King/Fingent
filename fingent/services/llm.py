"""
LLM service for report generation.

Supports:
- DeepSeek (primary)
- Qwen (fallback)

IMPORTANT: LLM is only used for text generation, NOT for alert decisions.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from openai import OpenAI

from fingent.core.cache import get_llm_cache
from fingent.core.config import Settings, get_settings
from fingent.core.errors import LLMError
from fingent.core.logging import get_logger

logger = get_logger("llm")


class LLMService(ABC):
    """Abstract base class for LLM services."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Generate text response."""
        pass


class DeepSeekService(LLMService):
    """DeepSeek LLM service."""

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
        )
        self.model = model
        self.cache = get_llm_cache()

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Generate text using DeepSeek API."""
        # Check cache
        cache_key = f"deepseek:{hash(prompt + str(system_prompt))}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            result = response.choices[0].message.content
            self.cache.set(cache_key, result)

            logger.debug(f"DeepSeek generated {len(result)} chars")
            return result

        except Exception as e:
            logger.error(f"DeepSeek API error: {e}")
            raise LLMError(
                f"DeepSeek generation failed: {e}",
                provider="deepseek",
                model=self.model,
            ) from e


class QwenService(LLMService):
    """Qwen (Tongyi) LLM service via DashScope."""

    def __init__(self, api_key: str, model: str = "qwen-turbo"):
        self.api_key = api_key
        self.model = model
        self.cache = get_llm_cache()

        # DashScope uses OpenAI-compatible API
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Generate text using Qwen API."""
        cache_key = f"qwen:{hash(prompt + str(system_prompt))}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            result = response.choices[0].message.content
            self.cache.set(cache_key, result)

            logger.debug(f"Qwen generated {len(result)} chars")
            return result

        except Exception as e:
            logger.error(f"Qwen API error: {e}")
            raise LLMError(
                f"Qwen generation failed: {e}",
                provider="qwen",
                model=self.model,
            ) from e


class FallbackLLMService(LLMService):
    """LLM service with fallback support."""

    def __init__(self, primary: LLMService, fallback: Optional[LLMService] = None):
        self.primary = primary
        self.fallback = fallback

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Generate with fallback on primary failure."""
        try:
            return self.primary.generate(
                prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError as e:
            if self.fallback:
                logger.warning(f"Primary LLM failed, trying fallback: {e}")
                return self.fallback.generate(
                    prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            raise


def create_llm_service(settings: Optional[Settings] = None) -> Optional[LLMService]:
    """
    Create LLM service based on configuration.

    Returns None if no LLM API keys are configured.
    """
    settings = settings or get_settings()

    primary = None
    fallback = None

    # Create primary (DeepSeek)
    if settings.deepseek_api_key:
        primary = DeepSeekService(
            api_key=settings.deepseek_api_key,
            model=settings.llm_model or "deepseek-chat",
        )
        logger.info("Created DeepSeek LLM service")

    # Create fallback (Qwen)
    if settings.dashscope_api_key:
        qwen = QwenService(
            api_key=settings.dashscope_api_key,
            model="qwen-turbo",
        )
        if primary:
            fallback = qwen
            logger.info("Created Qwen as fallback LLM")
        else:
            primary = qwen
            logger.info("Created Qwen as primary LLM")

    if not primary:
        logger.warning("No LLM API keys configured")
        return None

    if fallback:
        return FallbackLLMService(primary, fallback)

    return primary


# ===========================================
# Report Generation Prompts
# ===========================================

SYSTEM_PROMPT = """你是一个专业的金融分析师，负责生成简洁、专业的市场分析报告。

要求：
1. 使用中文
2. 简洁明了，避免冗余
3. 重点突出，先说结论
4. 包含具体数据支撑
5. 避免使用emoji（除非明确要求）"""

SUMMARY_PROMPT_TEMPLATE = """基于以下市场数据和信号，生成一段简洁的市场分析摘要（100-200字）：

## 信号汇总
整体方向: {direction}
评分: {score}
关键信号:
{key_signals}

## 告警
{alerts}

## 要求
1. 先给出整体判断
2. 列出2-3个关键观察
3. 如有告警，简要说明
"""


def generate_report_summary(
    llm: LLMService,
    signals_summary: dict[str, Any],
    alerts: list[dict[str, Any]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2000,
) -> str:
    """
    Generate report summary using LLM.

    Args:
        llm: LLM service instance
        signals_summary: Aggregated signals
        alerts: Triggered alerts

    Returns:
        Generated summary text
    """
    # Format key signals
    key_signals_text = ""
    for sig in signals_summary.get("key_signals", [])[:5]:
        key_signals_text += f"- {sig.get('name')}: {sig.get('direction')} ({sig.get('score'):.2f})\n"

    # Format alerts
    alerts_text = "无" if not alerts else ""
    for alert in alerts[:3]:
        alerts_text += f"- {alert.get('title')}: {alert.get('message')}\n"

    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        direction=signals_summary.get("overall_direction", "neutral"),
        score=signals_summary.get("overall_score", 0),
        key_signals=key_signals_text or "无显著信号",
        alerts=alerts_text,
    )

    return llm.generate(
        prompt,
        system_prompt=SYSTEM_PROMPT,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ===========================================
# Morning Brief Generation (晨报/简报)
# ===========================================

MORNING_BRIEF_SYSTEM = """你是一位资深金融分析师，每天早上为投资者撰写简短的市场晨报。

风格要求：
1. 语言简洁有力，像新闻主播一样专业
2. 先说结论，再说依据
3. 用"今日"、"目前"、"值得关注"等词汇增加时效感
4. 对普通投资者友好，避免过于技术化的术语
5. 如果有风险，明确提示
6. 不使用 emoji

## 信号解读指南（帮助你用通俗语言解释）

### 方向(Direction)含义：
- bullish（看涨）: 市场情绪乐观，风险偏好上升，股票/风险资产可能上涨
- bearish（看跌）: 市场情绪悲观，避险情绪上升，风险资产可能下跌
- neutral（中性）: 市场方向不明，多空力量均衡
- hawkish（鹰派）: 央行倾向加息/收紧政策，对债券/成长股不利
- dovish（鸽派）: 央行倾向降息/宽松政策，对风险资产有利

### 评分(Score)含义：
- 范围 -1.0 到 +1.0
- +0.5 以上：强看涨信号
- +0.2 到 +0.5：温和看涨
- -0.2 到 +0.2：中性
- -0.5 到 -0.2：温和看跌
- -0.5 以下：强看跌信号

### 常见信号解释：
- hawkish_bias（鹰派倾向）: 美联储可能加息或维持高利率，源自利率水平和通胀数据
- risk_on（风险偏好）: 投资者愿意承担风险，资金流向股票和加密货币
- risk_off（避险模式）: 投资者规避风险，资金流向国债和黄金
- sentiment_bullish（情绪看涨）: 新闻和市场情绪偏向乐观
- sentiment_bearish（情绪看跌）: 新闻和市场情绪偏向悲观
- yield_curve_warning（收益率曲线警告）: 2年期利率高于10年期，历史上预示衰退

### 告警解释：
- vix_spike: VIX恐慌指数飙升，市场波动加剧
- btc_crash: 比特币大跌，可能引发连锁反应
- gold_surge: 黄金大涨，避险需求上升
- yield_curve_inversion: 收益率曲线倒挂加深，衰退风险上升"""

MORNING_BRIEF_PROMPT = """请根据以下市场数据，生成一份简短的市场晨报（3-5句话，约150字）。

## 宏观经济
{macro_summary}

## 市场行情
{market_summary}

## 新闻情绪
{news_summary}

## 信号判断
- 整体方向: {direction} (评分: {score:+.2f})
- 关键信号: {key_signals}

## 风险告警
{alerts}

---
要求：
1. 第一句话给出今日市场整体判断（用通俗语言，如"今日市场偏向乐观"而非"bullish"）
2. 接下来 2-3 句用普通人能理解的语言解释为什么得出这个判断（例如：美联储态度、通胀数据、资金流向）
3. 最后一句给出需要关注的风险或机会，给出具体建议

重要：不要直接输出技术术语如"bullish"、"hawkish"，而是翻译成普通投资者能理解的话。
"""


def generate_morning_brief(
    llm: LLMService,
    state: dict[str, Any],
) -> str:
    """
    Generate morning brief style summary.

    This is the main function for generating user-friendly market summary.

    Args:
        llm: LLM service instance
        state: Full graph state with all data

    Returns:
        Morning brief text (3-5 sentences)
    """
    # Extract data from state
    macro_data = state.get("macro_data", {})
    market_data = state.get("market_data", {})
    news_data = state.get("news_data", {})
    signals = state.get("signals", [])
    alerts = state.get("alerts", [])
    report = state.get("report", {})

    # Format macro summary
    macro_lines = []
    rates = macro_data.get("rates", {})
    if rates.get("fed_funds_rate"):
        macro_lines.append(f"联邦基金利率: {rates['fed_funds_rate']:.2f}%")
    if rates.get("yield_spread_2y10y") is not None:
        spread = rates["yield_spread_2y10y"]
        macro_lines.append(f"2Y-10Y利差: {spread:+.2f}% {'(倒挂)' if spread < 0 else ''}")
    inflation = macro_data.get("inflation", {})
    if inflation.get("cpi_yoy"):
        macro_lines.append(f"CPI同比: {inflation['cpi_yoy']:.1f}%")
    macro_summary = "\n".join(macro_lines) if macro_lines else "无数据"

    # Format market summary
    market_lines = []
    quotes = market_data.get("quotes", {})
    for symbol in ["SPY", "QQQ", "BTC-USDT", "GLD"]:
        q = quotes.get(symbol, {})
        if q.get("price"):
            change = q.get("change_24h", 0) or 0
            market_lines.append(f"{symbol}: ${q['price']:.2f} ({change:+.1%})")
    market_summary = "\n".join(market_lines) if market_lines else "无数据"

    # Format news summary
    articles = news_data.get("articles", [])
    sentiment_avg = news_data.get("sentiment_average", 0)
    news_summary = f"分析 {len(articles)} 篇文章，平均情绪: {sentiment_avg:.2f}"

    # Get signals summary
    signals_summary = report.get("signals_summary", {})
    direction = signals_summary.get("overall_direction", "neutral")
    score = signals_summary.get("overall_score", 0)

    # Format key signals with details for LLM to explain
    key_signals_list = []
    for sig in signals[:5]:
        name = sig.get('name', '')
        sig_direction = sig.get('direction', '')
        sig_score = sig.get('score', 0)
        key_signals_list.append(f"{name}: {sig_direction} (score={sig_score:+.2f})")
    key_signals = "\n".join(key_signals_list) if key_signals_list else "无显著信号"

    # Format alerts
    if alerts:
        alerts_text = "\n".join([f"- {a.get('title')}: {a.get('message')}" for a in alerts[:3]])
    else:
        alerts_text = "无重大风险告警"

    # Build prompt
    prompt = MORNING_BRIEF_PROMPT.format(
        macro_summary=macro_summary,
        market_summary=market_summary,
        news_summary=news_summary,
        direction=direction,
        score=score,
        key_signals=key_signals,
        alerts=alerts_text,
    )

    return llm.generate(
        prompt,
        system_prompt=MORNING_BRIEF_SYSTEM,
        temperature=0.4,
        max_tokens=500,
    )
