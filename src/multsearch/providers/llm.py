import httpx
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential
from tenacity.wait import wait_base
from .base import BaseSearchProvider
from ..utils import fetch_prompt, summarize_prompt
from ..logger import log_info
from ..config import config


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc) -> bool:
    """检查异常是否可重试"""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


class _WaitWithRetryAfter(wait_base):
    """等待策略：优先使用 Retry-After 头，否则使用指数退避"""

    def __init__(self, multiplier: float, max_wait: int):
        self._base_wait = wait_random_exponential(multiplier=multiplier, max=max_wait)
        self._protocol_error_base = 3.0

    def __call__(self, retry_state):
        if retry_state.outcome and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                retry_after = self._parse_retry_after(exc.response)
                if retry_after is not None:
                    return retry_after
            if isinstance(exc, httpx.RemoteProtocolError):
                return self._base_wait(retry_state) + self._protocol_error_base
        return self._base_wait(retry_state)

    def _parse_retry_after(self, response: httpx.Response) -> Optional[float]:
        """解析 Retry-After 头（支持秒数或 HTTP 日期格式）"""
        header = response.headers.get("Retry-After")
        if not header:
            return None
        header = header.strip()

        if header.isdigit():
            return float(header)

        try:
            retry_dt = parsedate_to_datetime(header)
            if retry_dt.tzinfo is None:
                retry_dt = retry_dt.replace(tzinfo=timezone.utc)
            delay = (retry_dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delay)
        except (TypeError, ValueError):
            return None


class LLMProvider(BaseSearchProvider):
    """通用 OpenAI 兼容 chat/completions 流式 provider。

    模型本身无联网搜索能力，只负责：基于给定信源 summarize 答案 / fetch 网页内容。
    信源由 Tavily/Firecrawl 在 server.py 侧找好。
    """

    def __init__(self, api_url: str, api_key: str, model: str = "glm-5.2", extra_headers: Optional[dict] = None):
        super().__init__(api_url, api_key)
        self.model = model
        self.extra_headers = extra_headers or {}

    def _base_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

    def get_provider_name(self) -> str:
        return "LLM"

    async def fetch(self, url: str, ctx=None) -> str:
        headers = self._base_headers()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": fetch_prompt,
                },
                {"role": "user", "content": url + "\n获取该网页内容并返回其结构化Markdown格式"},
            ],
            "stream": True,
        }
        if config.max_tokens is not None:
            payload["max_tokens"] = config.max_tokens
        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def summarize(self, query: str, context: str, ctx=None) -> str:
        """让无搜索能力的模型基于给定信源文本回答用户问题（文本模式专用）。"""
        headers = self._base_headers()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": summarize_prompt},
                {"role": "user", "content": f"# 用户问题\n{query}\n\n# 信源\n{context}"},
            ],
            "stream": True,
        }
        # 推理模型（如 glm-5.2）会先消耗 token 做 reasoning，需给足上限
        payload["max_tokens"] = config.max_tokens if config.max_tokens is not None else 8192
        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def _parse_streaming_response(self, response, ctx=None) -> str:
        content = ""
        full_body_buffer = []

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue

            full_body_buffer.append(line)

            # 兼容 "data: {...}" 和 "data:{...}" 两种 SSE 格式
            if line.startswith("data:"):
                if line in ("data: [DONE]", "data:[DONE]"):
                    continue
                try:
                    # 去掉 "data:" 前缀，并去除可能的空格
                    json_str = line[5:].lstrip()
                    data = json.loads(json_str)
                    choices = data.get("choices", [])
                    if choices and len(choices) > 0:
                        delta = choices[0].get("delta", {})
                        if "content" in delta:
                            content += delta["content"]
                except (json.JSONDecodeError, IndexError):
                    continue

        if not content and full_body_buffer:
            try:
                full_text = "".join(full_body_buffer)
                data = json.loads(full_text)
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    content = message.get("content", "")
            except json.JSONDecodeError:
                pass

        await log_info(ctx, f"content: {content}", config.debug_enabled)

        return content

    async def _execute_stream_with_retry(self, headers: dict, payload: dict, ctx=None) -> str:
        """执行带重试机制的流式 HTTP 请求"""
        timeout = httpx.Timeout(connect=6.0, read=120.0, write=10.0, pool=None)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=_WaitWithRetryAfter(config.retry_multiplier, config.retry_max_wait),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                with attempt:
                    async with client.stream(
                        "POST",
                        f"{self.api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        response.raise_for_status()
                        return await self._parse_streaming_response(response, ctx)
