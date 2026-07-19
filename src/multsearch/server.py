import sys
from pathlib import Path
from collections import OrderedDict

# 支持直接运行：添加 src 目录到 Python 路径
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fastmcp import FastMCP, Context
from typing import Annotated, Optional
from pydantic import Field

# 尝试使用绝对导入（支持 mcp run）
try:
    from multsearch.providers.llm import LLMProvider
    from multsearch.logger import log_info
    from multsearch.config import config
    from multsearch.sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
except ImportError:
    from .providers.llm import LLMProvider
    from .logger import log_info
    from .config import config
    from .sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources

import asyncio

mcp = FastMCP("multsearch")

_SOURCES_CACHE = SourcesCache(max_size=256)
_AVAILABLE_MODELS_CACHE: dict[tuple[str, str], list[str]] = {}
_AVAILABLE_MODELS_LOCK = asyncio.Lock()


class _ResultCache:
    """web_search 响应去重缓存（同 query+model 命中直接返回，LRU）。"""

    def __init__(self, max_size: int = 128):
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[tuple[str, str], dict] = OrderedDict()

    async def get(self, namespace: str, key: str) -> dict | None:
        async with self._lock:
            k = (namespace, key)
            v = self._cache.get(k)
            if v is not None:
                self._cache.move_to_end(k)
            return v

    async def set(self, namespace: str, key: str, value: dict) -> None:
        async with self._lock:
            k = (namespace, key)
            self._cache[k] = value
            self._cache.move_to_end(k)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)


_RESULT_CACHE = _ResultCache()


async def _fetch_available_models(api_url: str, api_key: str) -> list[str]:
    import httpx

    models_url = f"{api_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            models_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

    models: list[str] = []
    for item in (data or {}).get("data", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models


async def _get_available_models_cached(api_url: str, api_key: str) -> list[str]:
    key = (api_url, api_key)
    async with _AVAILABLE_MODELS_LOCK:
        if key in _AVAILABLE_MODELS_CACHE:
            return _AVAILABLE_MODELS_CACHE[key]

    try:
        models = await _fetch_available_models(api_url, api_key)
    except Exception:
        models = []

    async with _AVAILABLE_MODELS_LOCK:
        _AVAILABLE_MODELS_CACHE[key] = models
    return models


def _extra_results_to_sources(
    tavily_results: list[dict] | None,
    firecrawl_results: list[dict] | None,
    searxng_results: list[dict] | None = None,
) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()

    if firecrawl_results:
        for r in firecrawl_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "firecrawl"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            desc = (r.get("description") or "").strip()
            if desc:
                item["description"] = desc
            sources.append(item)

    if tavily_results:
        for r in tavily_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "tavily"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            content = (r.get("content") or "").strip()
            if content:
                item["description"] = content
            sources.append(item)

    if searxng_results:
        for r in searxng_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "searxng"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            content = (r.get("content") or "").strip()
            if content:
                item["description"] = content
            sources.append(item)

    return sources


def _build_source_context(
    tavily_results: list[dict] | None,
    firecrawl_results: list[dict] | None,
    searxng_results: list[dict] | None = None,
) -> str:
    """把 Tavily/Firecrawl/SearXNG 结果组装成编号信源文本，供文本模式模型总结。"""
    blocks: list[str] = []
    idx = 1
    seen: set[str] = set()

    if firecrawl_results:
        for r in firecrawl_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = (r.get("title") or "").strip()
            desc = (r.get("description") or "").strip()
            lines = [f"[{idx}] {title}".rstrip()]
            lines.append(f"URL: {url}")
            if desc:
                lines.append(f"内容: {desc}")
            blocks.append("\n".join(lines))
            idx += 1

    if tavily_results:
        for r in tavily_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip()
            lines = [f"[{idx}] {title}".rstrip()]
            lines.append(f"URL: {url}")
            if content:
                lines.append(f"内容: {content}")
            blocks.append("\n".join(lines))
            idx += 1

    if searxng_results:
        for r in searxng_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip()
            lines = [f"[{idx}] {title}".rstrip()]
            lines.append(f"URL: {url}")
            if content:
                lines.append(f"内容: {content}")
            blocks.append("\n".join(lines))
            idx += 1

    return "\n\n".join(blocks)


@mcp.tool(
    name="web_search",
    output_schema=None,
    description="""
    Performs a deep web search: Tavily + Firecrawl find sources, then the LLM (default GLM-5.2 via Ark) synthesizes a cited answer.

    Returns:
      - session_id      string  pass to get_sources to retrieve full source list
      - content         string  LLM's source-based answer (with [n] inline citations)
      - sources_count   int
      - cached          bool    true if response was served from in-memory result cache
    """,
    meta={"version": "4.0.0", "author": "multsearch"},
)
async def web_search(
    query: Annotated[str, "Clear, self-contained natural-language search query."],
    model: Annotated[str, "Optional model ID for this request only. Used ONLY when user explicitly provided."] = "",
    extra_sources: Annotated[int, "Number of additional reference results from Tavily/Firecrawl. Set 0 to disable. Default 0 = auto quota (Tavily 8 + Firecrawl 6)."] = 0,
) -> dict:
    # ── Cached response shortcut ────────────────────────────────────────
    cache_payload = f"{model}|{query}"
    cached = await _RESULT_CACHE.get("web_search", cache_payload)
    if cached and isinstance(cached, dict):
        new_sid = new_session_id()
        await _SOURCES_CACHE.set(new_sid, cached.get("sources", []))
        return {
            "session_id": new_sid,
            "content": cached.get("content", ""),
            "sources_count": len(cached.get("sources", [])),
            "cached": True,
        }

    session_id = new_session_id()
    try:
        api_url = config.api_url
        api_key = config.api_key
    except ValueError as e:
        await _SOURCES_CACHE.set(session_id, [])
        return {"session_id": session_id, "content": f"配置错误: {str(e)}", "sources_count": 0}

    effective_model = config.model
    if model:
        available = await _get_available_models_cached(api_url, api_key)
        if available and model not in available:
            await _SOURCES_CACHE.set(session_id, [])
            return {"session_id": session_id, "content": f"无效模型: {model}", "sources_count": 0}
        effective_model = model

    llm_provider = LLMProvider(api_url, api_key, effective_model)

    # 计算信源配额
    has_tavily = bool(config.tavily_api_key)
    has_firecrawl = bool(config.firecrawl_api_key)
    has_searxng = config.searxng_enabled and bool(config.searxng_api_url)

    firecrawl_count = 0
    tavily_count = 0
    searxng_count = 0
    if extra_sources > 0:
        # 用户指定总数，在所有可用源间均分（整除），余数依次补给 searxng（免费，多承担）
        available = [name for name, ok in (
            ("searxng", has_searxng),
            ("firecrawl", has_firecrawl),
            ("tavily", has_tavily),
        ) if ok]
        if available:
            base = extra_sources // len(available)
            rem = extra_sources % len(available)
            for i, name in enumerate(available):
                cnt = base + (1 if i < rem else 0)
                if name == "searxng":
                    searxng_count = cnt
                elif name == "firecrawl":
                    firecrawl_count = cnt
                elif name == "tavily":
                    tavily_count = cnt
    else:
        # 默认配额，保证总有信源可总结；SearXNG 免费且多引擎容错，多给
        if has_tavily:
            tavily_count = 8
        if has_firecrawl:
            firecrawl_count = 6
        if has_searxng:
            searxng_count = 8

    async def _safe_tavily() -> list[dict] | None:
        try:
            if tavily_count:
                return await _call_tavily_search(query, tavily_count)
        except Exception:
            return None

    async def _safe_firecrawl() -> list[dict] | None:
        try:
            if firecrawl_count:
                return await _call_firecrawl_search(query, firecrawl_count)
        except Exception:
            return None

    async def _safe_searxng() -> list[dict] | None:
        try:
            if searxng_count:
                return await _call_searxng_search(query, searxng_count)
        except Exception:
            return None

    # 文本模式：先并行抓信源，再让模型基于信源总结
    src_coros: list = []
    if tavily_count > 0:
        src_coros.append(_safe_tavily())
    if firecrawl_count > 0:
        src_coros.append(_safe_firecrawl())
    if searxng_count > 0:
        src_coros.append(_safe_searxng())
    src_gathered = await asyncio.gather(*src_coros) if src_coros else []

    tavily_results: list[dict] | None = None
    firecrawl_results: list[dict] | None = None
    searxng_results: list[dict] | None = None
    sidx = 0
    if tavily_count > 0:
        tavily_results = src_gathered[sidx]
        sidx += 1
    if firecrawl_count > 0:
        firecrawl_results = src_gathered[sidx]
        sidx += 1
    if searxng_count > 0:
        searxng_results = src_gathered[sidx]

    all_sources = _extra_results_to_sources(tavily_results, firecrawl_results, searxng_results)

    context = _build_source_context(tavily_results, firecrawl_results, searxng_results)
    llm_result = ""
    if context:
        try:
            llm_result = await llm_provider.summarize(query, context)
        except Exception:
            llm_result = ""
    # 模型可能附了信源列表，剥离；信源以 Tavily/Firecrawl/SearXNG 实际结果为准
    answer, _ = split_answer_and_sources(llm_result)
    if not answer:
        answer = llm_result.strip()

    await _SOURCES_CACHE.set(session_id, all_sources)
    await _RESULT_CACHE.set("web_search", cache_payload, {"content": answer, "sources": all_sources})
    return {"session_id": session_id, "content": answer, "sources_count": len(all_sources), "cached": False}


@mcp.tool(
    name="get_sources",
    description="""
    When you feel confused or curious about the search response content, use the session_id returned by web_search to invoke the this tool to obtain the corresponding list of information sources.
    Retrieve all cached sources for a previous web_search call.
    Provide the session_id returned by web_search to get the full source list.
    """,
    meta={"version": "1.0.0", "author": "multsearch"},
)
async def get_sources(
    session_id: Annotated[str, "Session ID from previous web_search call."]
) -> dict:
    sources = await _SOURCES_CACHE.get(session_id)
    if sources is None:
        return {
            "session_id": session_id,
            "sources": [],
            "sources_count": 0,
            "error": "session_id_not_found_or_expired",
        }
    return {"session_id": session_id, "sources": sources, "sources_count": len(sources)}


@mcp.tool(
    name="gemini_search",
    description="""
    Performs a web search via Gemini + Google Search grounding (Google's index).
    Independent of Tavily/Firecrawl — use as a second search path for cross-validation.
    Returns the grounded answer plus Google-sourced citations.
    Requires GEMINI_API_KEY (get a free key at https://aistudio.google.com/apikey).
    """,
    meta={"version": "1.0.0", "author": "multsearch"},
)
async def gemini_search(
    query: Annotated[str, "Clear natural-language search query."],
) -> dict:
    session_id = new_session_id()
    result = await _call_gemini_search(query)
    if result is None:
        await _SOURCES_CACHE.set(session_id, [])
        return {
            "session_id": session_id,
            "content": "Gemini 搜索未配置或调用失败:检查 GEMINI_API_KEY 是否设置、代理是否开启(Google API 需走代理)。",
            "sources_count": 0,
        }
    await _SOURCES_CACHE.set(session_id, result["sources"])
    return {
        "session_id": session_id,
        "content": result["answer"],
        "sources_count": len(result["sources"]),
        "cached": False,
    }


async def _call_tavily_extract(url: str) -> str | None:
    import httpx
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return None
    endpoint = f"{api_url.rstrip('/')}/extract"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"urls": [url], "format": "markdown"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            if data.get("results") and len(data["results"]) > 0:
                content = data["results"][0].get("raw_content", "")
                return content if content and content.strip() else None
            return None
    except Exception:
        return None


async def _call_tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    import httpx
    api_key = config.tavily_api_key
    if not api_key:
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", ""), "score": r.get("score", 0)}
                for r in results
            ] if results else None
    except Exception:
        return None


async def _call_firecrawl_search(query: str, limit: int = 14) -> list[dict] | None:
    import httpx
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"query": query, "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            results = data.get("data", {}).get("web", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "description": r.get("description", "")}
                for r in results
            ] if results else None
    except Exception:
        return None


async def _call_searxng_search(query: str, max_results: int = 8) -> list[dict] | None:
    """调用 SearXNG 元搜索（默认 google+bing+duckduckgo 聚合），返回统一结构信源。

    SearXNG 为自建/公网实例，默认直连（trust_env=False 不走 HTTP_PROXY）；
    实例需在 settings.yml 开启 json 输出格式。返回字段对齐 Tavily（title/url/content）。
    """
    import httpx
    if not config.searxng_enabled or not config.searxng_api_url:
        return None
    endpoint = f"{config.searxng_api_url.rstrip('/')}/search"
    params = {
        "q": query,
        "format": "json",
        "engines": config.searxng_engines,
        "language": "auto",
        "safesearch": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
            response = await client.get(
                endpoint,
                params=params,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        results = data.get("results", []) or []
        out: list[dict] = []
        for r in results[:max_results]:
            url = (r.get("url") or "").strip()
            if not url:
                continue
            out.append({
                "title": (r.get("title") or "").strip(),
                "url": url,
                "content": (r.get("content") or "").strip(),
            })
        return out or None
    except Exception:
        return None


def _gemini_retry_after(response, default: float) -> float:
    """从 Retry-After 头解析等待秒数，失败用 default。"""
    h = response.headers.get("Retry-After")
    if not h:
        return default
    h = h.strip()
    if h.isdigit():
        return float(h)
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(h)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return default


async def _call_gemini_search(query: str) -> dict | None:
    """调用 Gemini + Google Search grounding，返回 {answer, sources}。Google 索引，独立于 Tavily/Firecrawl。
    遇 429(限流)/5xx 指数退避重试，避免并发下免费档 15 RPM 被打满直接失败。"""
    import httpx
    import random
    api_key = config.gemini_api_key
    if not api_key:
        return None
    model = config.gemini_model
    endpoint = f"{config.gemini_api_url.rstrip('/')}/v1beta/models/{model}:generateContent"
    params = {"key": api_key}
    body = {
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
    }
    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(endpoint, params=params, json=body)
            if response.status_code == 429 or response.status_code >= 500:
                # 限流/服务端错误:退避重试
                if attempt < max_attempts - 1:
                    wait = min(_gemini_retry_after(response, 8.0 * (2 ** attempt)) + random.uniform(0, 4), 60.0)
                    await asyncio.sleep(wait)
                    continue
                return None
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            answer = "".join(p.get("text", "") for p in parts if "text" in p)
            gm = candidates[0].get("groundingMetadata", {})
            chunks = gm.get("groundingChunks", [])
            sources: list[dict] = []
            seen: set[str] = set()
            for c in chunks:
                web = c.get("web", {})
                uri = (web.get("uri") or "").strip()
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                item: dict = {"url": uri, "provider": "gemini"}
                title = (web.get("title") or "").strip()
                if title:
                    item["title"] = title
                sources.append(item)
            return {"answer": answer, "sources": sources}
        except Exception:
            if attempt < max_attempts - 1:
                await asyncio.sleep(8.0 * (2 ** attempt) + random.uniform(0, 4))
                continue
            return None
    return None


async def _call_firecrawl_scrape(url: str, ctx=None) -> str | None:
    import httpx
    api_url = config.firecrawl_api_url
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{api_url.rstrip('/')}/scrape"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_retries = config.retry_max_attempts
    for attempt in range(max_retries):
        body = {
            "url": url,
            "formats": ["markdown"],
            "timeout": 60000,
            "waitFor": (attempt + 1) * 1500,
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
                markdown = data.get("data", {}).get("markdown", "")
                if markdown and markdown.strip():
                    return markdown
                await log_info(ctx, f"Firecrawl: markdown为空, 重试 {attempt + 1}/{max_retries}", config.debug_enabled)
        except Exception as e:
            await log_info(ctx, f"Firecrawl error: {e}", config.debug_enabled)
            return None
    return None


@mcp.tool(
    name="web_fetch",
    output_schema=None,
    description="""
    Fetches and extracts complete content from a URL, returning it as a structured Markdown document.

    **Key Features:**
        - **Full Content Extraction:** Retrieves and parses all meaningful content (text, images, links, tables, code blocks).
        - **Markdown Conversion:** Converts HTML structure to well-formatted Markdown with preserved hierarchy.
        - **Content Fidelity:** Maintains 100% content fidelity without summarization or modification.

    **Edge Cases & Best Practices:**
        - Ensure URL is complete and accessible (not behind authentication or paywalls).
        - May not capture dynamically loaded content requiring JavaScript execution.
        - Large pages may take longer to process; consider timeout implications.
    """,
    meta={"version": "1.3.0", "author": "multsearch"},
)
async def web_fetch(
    url: Annotated[str, "Valid HTTP/HTTPS web address pointing to the target page. Must be complete and accessible."],
    ctx: Context = None
) -> str:
    await log_info(ctx, f"Begin Fetch: {url}", config.debug_enabled)

    result = await _call_tavily_extract(url)
    if result:
        await log_info(ctx, "Fetch Finished (Tavily)!", config.debug_enabled)
        return result

    await log_info(ctx, "Tavily unavailable or failed, trying Firecrawl...", config.debug_enabled)
    result = await _call_firecrawl_scrape(url, ctx)
    if result:
        await log_info(ctx, "Fetch Finished (Firecrawl)!", config.debug_enabled)
        return result

    await log_info(ctx, "Fetch Failed!", config.debug_enabled)
    if not config.tavily_api_key and not config.firecrawl_api_key:
        return "配置错误: TAVILY_API_KEY 和 FIRECRAWL_API_KEY 均未配置"
    return "提取失败: 所有提取服务均未能获取内容"


async def _call_tavily_map(url: str, instructions: str = None, max_depth: int = 1,
                           max_breadth: int = 20, limit: int = 50, timeout: int = 150) -> str:
    import httpx
    import json
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return "配置错误: TAVILY_API_KEY 未配置，请设置环境变量 TAVILY_API_KEY"
    endpoint = f"{api_url.rstrip('/')}/map"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
    if instructions:
        body["instructions"] = instructions
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            return json.dumps({
                "base_url": data.get("base_url", ""),
                "results": data.get("results", []),
                "response_time": data.get("response_time", 0)
            }, ensure_ascii=False, indent=2)
    except httpx.TimeoutException:
        return f"映射超时: 请求超过{timeout}秒"
    except httpx.HTTPStatusError as e:
        return f"HTTP错误: {e.response.status_code} - {e.response.text[:200]}"
    except Exception as e:
        return f"映射错误: {str(e)}"


@mcp.tool(
    name="web_map",
    description="""
    Maps a website's structure by traversing it like a graph, discovering URLs and generating a comprehensive site map.

    **Key Features:**
        - **Graph Traversal:** Explores website structure starting from root URL.
        - **Depth & Breadth Control:** Configure traversal limits to balance coverage and performance.
        - **Instruction Filtering:** Use natural language to focus crawler on specific content types.

    **Edge Cases & Best Practices:**
        - Start with low max_depth (1-2) for initial exploration, increase if needed.
        - Use instructions to filter for specific content (e.g., "only documentation pages").
        - Large sites may hit timeout limits; adjust timeout and limit parameters accordingly.
    """,
    meta={"version": "1.3.0", "author": "multsearch"},
)
async def web_map(
    url: Annotated[str, "Root URL to begin the mapping (e.g., 'https://docs.example.com')."],
    instructions: Annotated[str, "Natural language instructions for the crawler to filter or focus on specific content."] = "",
    max_depth: Annotated[int, Field(description="Maximum depth of mapping from the base URL.", ge=1, le=5)] = 1,
    max_breadth: Annotated[int, Field(description="Maximum number of links to follow per page.", ge=1, le=500)] = 20,
    limit: Annotated[int, Field(description="Total number of links to process before stopping.", ge=1, le=500)] = 50,
    timeout: Annotated[int, Field(description="Maximum time in seconds for the operation.", ge=10, le=150)] = 150
) -> str:
    result = await _call_tavily_map(url, instructions, max_depth, max_breadth, limit, timeout)
    return result


@mcp.tool(
    name="get_config_info",
    output_schema=None,
    description="""
    Returns current multsearch MCP server configuration and tests API connectivity.

    **Key Features:**
        - **Configuration Check:** Verifies environment variables and current settings.
        - **Connection Test:** Sends request to /models endpoint to validate API access.
        - **Model Discovery:** Lists all available models from the API.

    **Edge Cases & Best Practices:**
        - Use this tool first when debugging connection or configuration issues.
        - API keys are automatically masked for security in the response.
        - Connection test timeout is 10 seconds; network issues may cause delays.
    """,
    meta={"version": "1.3.0", "author": "multsearch"},
)
async def get_config_info() -> str:
    import json
    import httpx

    config_info = config.get_config_info()

    # 添加连接测试
    test_result = {
        "status": "未测试",
        "message": "",
        "response_time_ms": 0
    }

    try:
        api_url = config.api_url
        api_key = config.api_key

        # 构建 /models 端点 URL
        models_url = f"{api_url.rstrip('/')}/models"

        # 发送测试请求
        import time
        start_time = time.time()

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                models_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
            )

            response_time = (time.time() - start_time) * 1000  # 转换为毫秒

            if response.status_code == 200:
                test_result["status"] = "✅ 连接成功"
                test_result["message"] = f"成功获取模型列表 (HTTP {response.status_code})"
                test_result["response_time_ms"] = round(response_time, 2)

                # 尝试解析返回的模型列表
                try:
                    models_data = response.json()
                    if "data" in models_data and isinstance(models_data["data"], list):
                        model_count = len(models_data["data"])
                        test_result["message"] += f"，共 {model_count} 个模型"

                        # 提取所有模型的 ID/名称
                        model_names = []
                        for model in models_data["data"]:
                            if isinstance(model, dict) and "id" in model:
                                model_names.append(model["id"])

                        if model_names:
                            test_result["available_models"] = model_names
                except:
                    pass
            else:
                test_result["status"] = "⚠️ 连接异常"
                test_result["message"] = f"HTTP {response.status_code}: {response.text[:100]}"
                test_result["response_time_ms"] = round(response_time, 2)

    except httpx.TimeoutException:
        test_result["status"] = "❌ 连接超时"
        test_result["message"] = "请求超时（10秒），请检查网络连接或 API URL"
    except httpx.RequestError as e:
        test_result["status"] = "❌ 连接失败"
        test_result["message"] = f"网络错误: {str(e)}"
    except ValueError as e:
        test_result["status"] = "❌ 配置错误"
        test_result["message"] = str(e)
    except Exception as e:
        test_result["status"] = "❌ 测试失败"
        test_result["message"] = f"未知错误: {str(e)}"

    config_info["connection_test"] = test_result

    return json.dumps(config_info, ensure_ascii=False, indent=2)


@mcp.tool(
    name="switch_model",
    output_schema=None,
    description="""
    Switches the default LLM model used for search and fetch operations, persisting the setting.

    **Key Features:**
        - **Model Selection:** Change the AI model for web search synthesis and content fetching.
        - **Persistent Storage:** Model preference saved to ~/.config/multsearch/config.json.
        - **Immediate Effect:** New model used for all subsequent operations.

    **Edge Cases & Best Practices:**
        - Use get_config_info to verify available models before switching.
        - Invalid model IDs may cause API errors in subsequent requests.
        - Model changes persist across sessions until explicitly changed again.
    """,
    meta={"version": "1.3.0", "author": "multsearch"},
)
async def switch_model(
    model: Annotated[str, "Model ID to switch to (e.g., 'glm-5.2')."]
) -> str:
    import json

    try:
        previous_model = config.model
        config.set_model(model)
        current_model = config.model

        result = {
            "status": "✅ 成功",
            "previous_model": previous_model,
            "current_model": current_model,
            "message": f"模型已从 {previous_model} 切换到 {current_model}",
            "config_file": str(config.config_file)
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except ValueError as e:
        result = {
            "status": "❌ 失败",
            "message": f"切换模型失败: {str(e)}"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        result = {
            "status": "❌ 失败",
            "message": f"未知错误: {str(e)}"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    import signal
    import os
    import threading

    # 信号处理（仅主线程）
    if threading.current_thread() is threading.main_thread():
        def handle_shutdown(signum, frame):
            os._exit(0)
        signal.signal(signal.SIGINT, handle_shutdown)
        if sys.platform != 'win32':
            signal.signal(signal.SIGTERM, handle_shutdown)

    # Windows 父进程监控
    if sys.platform == 'win32':
        import time
        import ctypes
        parent_pid = os.getppid()

        def is_parent_alive(pid):
            """Windows 下检查进程是否存活"""
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return True
            exit_code = ctypes.c_ulong()
            result = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return result and exit_code.value == STILL_ACTIVE

        def monitor_parent():
            while True:
                if not is_parent_alive(parent_pid):
                    os._exit(0)
                time.sleep(2)

        threading.Thread(target=monitor_parent, daemon=True).start()

    try:
        mcp.run(transport="stdio", show_banner=False)
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
