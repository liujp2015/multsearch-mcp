"""端到端验证文本模式：Tavily/Firecrawl 找源 → LLM 总结。不走 uv 缓存，直接跑源码。

需要先设置环境变量（参考 .mcp.json 或 .env.example）：
  MULT_API_URL / MULT_API_KEY / MULT_MODEL / TAVILY_API_KEY / FIRECRAWL_API_KEY
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from multsearch.config import config
from multsearch.providers.llm import LLMProvider
from multsearch.server import _call_tavily_search, _call_firecrawl_search, _build_source_context, _extra_results_to_sources


async def main():
    query = "AKShare stock_zh_a_spot_em limit参数 默认返回多少只"

    print("=== config 检查 ===")
    print("provider:", config.provider)
    print("api_url :", config.api_url)
    print("model   :", config.model)

    if not (config.api_key and config.tavily_api_key and config.firecrawl_api_key):
        print("\n⚠️ 缺少凭证环境变量（MULT_API_KEY / TAVILY_API_KEY / FIRECRAWL_API_KEY），无法跑活链路。")
        return

    print("\n=== 1. Tavily + Firecrawl 并行找源 ===")
    tavily, firecrawl = await asyncio.gather(
        _call_tavily_search(query, 8),
        _call_firecrawl_search(query, 6),
    )
    print(f"Tavily 结果数: {len(tavily) if tavily else 0}")
    print(f"Firecrawl 结果数: {len(firecrawl) if firecrawl else 0}")

    sources = _extra_results_to_sources(tavily, firecrawl)
    print(f"合并后信源数: {len(sources)}")

    context = _build_source_context(tavily, firecrawl)
    print(f"\ncontext 长度: {len(context)} 字符")
    print("context 前 300 字符:\n", context[:300])

    print("\n=== 2. LLM summarize ===")
    provider = LLMProvider(config.api_url, config.api_key, config.model)
    answer = await provider.summarize(query, context)
    print("答案长度:", len(answer))
    print("=== 答案 ===")
    print(answer)


asyncio.run(main())
