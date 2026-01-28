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
