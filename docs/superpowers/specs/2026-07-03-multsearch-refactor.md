# multsearch 重构设计 + 实现计划

> 日期:2026-07-03 · 从 muyu-search-mcp v0.1.3 重构为 multsearch-mcp v0.2.0
> baseline commit: `2e0f65e`(重构前完整快照,任何删除均可从该 commit 恢复)

## 1. 目标

把 muyu-search-mcp 重构 + 改名为 **multsearch-mcp**,清掉休眠/遗留代码,只保留双路搜索的活跃内核,然后上传 GitHub。

**双路搜索内核(保留)**:
- Path A `web_search`:Tavily + Firecrawl 找源 → LLM(火山方舟 Ark glm-5.2)summarize,带 `[编号]` 内联引用
- Path B `gemini_search`:Gemini 2.5 Flash + Google Search grounding(独立 Google 索引,429 退避重试)
- 辅助:`web_fetch` / `web_map` / `get_sources` / `get_config_info` / `switch_model`

## 2. 改名映射

| 维度 | 旧 | 新 |
|---|---|---|
| pyproject name | muyu-search-mcp | multsearch-mcp |
| 包目录 | src/muyu_search/ | src/multsearch/ |
| 入口脚本 | muyu-search / muyu-search-setup | multsearch / multsearch-setup |
| FastMCP app 名 | "muyu-search" | "multsearch" |
| Provider 类/文件 | GrokSearchProvider / providers/grok.py | LLMProvider / providers/llm.py |
| config 目录 | ~/.config/muyu-search/ | ~/.config/multsearch/ |
| env 前缀 | MUYU_* (+ GROK_* 遗留别名) | MULT_*(clean break,不留别名) |
| .mcp.json args | `... muyu-search` | `... multsearch` |
| 版本 | 0.1.3 | 0.2.0 |

env 重命名明细(均 MUYU_→MULT_):PROVIDER / API_URL / API_KEY / MODEL / MAX_TOKENS / DEBUG / RETRY_MAX_ATTEMPTS / RETRY_MULTIPLIER / RETRY_MAX_WAIT / LOG_LEVEL / LOG_DIR / TAVILY_ENABLED / GEMINI_API_KEY。**保留**标准名:`TAVILY_API_KEY` / `FIRECRAWL_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY`(第三方标准 env,不改)。

## 3. 删除清单(休眠/遗留)

### server.py
- `web_search` 的 `:online` 在线分支(行 ~298-326)+ `is_text_only` 判断(恒为文本模式)+ 内联 `_safe_grok()` + `grok_provider.search()` 调用
- `web_search` 的 `plan_session_id` 参数 + planning gate + `budget`/`blocked_by_gate` 返回字段
- `web_search` 的 `platform` 参数(文本模式下无作用,Tavily/Firecrawl 不支持平台过滤)→ 移除
- 6 个 plan_* 工具:plan_intent / plan_complexity / plan_sub_query / plan_search_term / plan_tool_mapping / plan_execution
- `toggle_builtin_tools` 工具(改 .claude/settings.json,与搜索 MCP 主线无关)
- 顶部 import:`planning_engine` / `result_cache` / `_split_csv`(来自 planning)
- `config.provider_extra_headers()` 调用(行 226,provider 类构造不再收该参数)

### providers/grok.py → providers/llm.py
- `search()`(在线模式专用)+ `describe_url()` + `rank_sources()`
- 模块级 `get_local_time_info()` + `_needs_time_context()`(只被 search 用)
- import 的 `search_prompt` / `url_describe_prompt` / `rank_sources_prompt`
- 保留:`summarize()` / `fetch()` / `_parse_streaming_response()` / `_execute_stream_with_retry()`(含 tenacity 重试 + Retry-After 解析)/ `_base_headers()`

### utils.py
- `search_prompt` / `url_describe_prompt` / `rank_sources_prompt`(随在线模式删)
- `format_extra_sources()` / `format_search_results()`(定义了但全程无人调用,死代码)
- 保留:`summarize_prompt` / `fetch_prompt` / `extract_unique_urls()`

### config.py
- `_apply_model_suffix()`(:online 自动后缀,OpenRouter 专用)
- `openrouter_referer` / `openrouter_title` / `provider_extra_headers()`(OpenRouter 归因头)
- `_PROVIDER_DEFAULTS` 的 openrouter / xai 条目(provider 只留 custom)
- 所有 `GROK_*` / `OPENROUTER_API_KEY` env 别名 + `MUYU_*` → 改 `MULT_*`(不留旧别名)
- `provider` property 的 openrouter/xai 分支(legacy_url 推断)→ 简化为恒 custom

### planning.py
- 整个文件删除(586 行)。web_search 的响应去重缓存(result_cache)迁到 server.py 内联实现。

### test_text_mode.py
- 同步改:`muyu_search.*` → `multsearch.*`,`GrokSearchProvider` → `LLMProvider`,去掉 `provider_extra_headers()` 实参,env `MUYU_*` → `MULT_*`。

## 4. 保留清单(不动)

- sources.py(SourcesCache / merge_sources / split_answer_and_sources 及内部 _split_*)—— 信源解析,纯函数,全部保留
- providers/base.py(BaseSearchProvider ABC / SearchResult)
- logger.py
- server.py 的 Tavily/Firecrawl/Gemini 调用函数(_call_tavily_search/extract/map、_call_firecrawl_search/scrape、_call_gemini_search、_gemini_retry_after)
- server.py 的 _build_source_context / _extra_results_to_sources / _fetch_available_models / _get_available_models_cached(model 参数软校验,Ark /models 不通时静默跳过,无害保留)
- gemini_search / web_fetch / web_map / get_sources / get_config_info / switch_model 工具

## 5. 向后兼容性

- **clean break**:env 不留 MUYU_/GROK_ 别名。.mcp.json 由我同步更新到 MULT_*,用户无感。
- config.json(~/.config/multsearch/config.json)从旧路径迁移;env 优先级仍高于文件,故 .mcp.json env 全在时不依赖文件。
- web_search 签名变化:去掉 `platform` / `plan_session_id` 参数。调用方(Claude Code)按工具 schema 调,无硬编码依赖,安全。
- uv 缓存:version 0.1.3→0.2.0 强制重建 wheel(见 memory uv_tool_run_cache),无需手动清缓存。

## 6. 实现步骤

1. **包重命名**:`git mv src/muyu_search src/multsearch`;改 `__init__.py`;server.py 顶部 try/except 绝对 import `muyu_search.*`→`multsearch.*`;FastMCP 名;config.py 配置路径;setup_wizard 品牌字串;providers/__init__.py。
2. **Provider 改名**:`git mv providers/grok.py providers/llm.py`;class GrokSearchProvider→LLMProvider;get_provider_name "Grok"→"LLM";删 search/describe_url/rank_sources + 两个 time helper + 无用 prompt import。
3. **删 :online 遗留**:server.py web_search 只留文本模式分支;删 platform/plan_session_id 参数 + gate/budget;config.py 删 _apply_model_suffix/openrouter_*/provider_extra_headers/GROK_* 别名、MUYU_→MULT_、provider 简化;utils.py 删 3 个 prompt + 2 个死函数。
4. **删 plan_*/planning.py**:server.py 删 6 个 plan_* 工具 + planning import;web_search 的 result_cache 调用换成 server.py 内联 `_RESULT_CACHE`(OrderedDict LRU);删 planning.py。
5. **删 toggle_builtin_tools**。
6. **配置收尾**:pyproject(name/desc/scripts/version 0.2.0);.mcp.json(args + env MUYU_→MULT_);迁移 ~/.config/muyu-search/config.json → ~/.config/multsearch/;README.md 品牌更新;test_text_mode.py 同步。
7. **验证**:py_compile 全部;为纯函数加单测(split_answer_and_sources/merge_sources/_build_source_context/_extra_results_to_sources/extract_unique_urls)跑 pytest;`python -c` 导入并列工具名;代理开时 curl 直连 Ark summarize + Gemini 验活链路。
8. **提交**:git commit 重构成果。
9. **【需用户】** `/mcp` 重连实测 web_search + gemini_search;`gh auth login` 后 gh repo create + push。

## 7. 风险

- 删 planning.py 丢 web_search 响应缓存 → 用内联 LRU 缓存补回(行为等价)。
- Ark `/api/plan/v3/models` 可能 404 → 现有 try/except 已兜底返回 [],model 校验静默跳过,无影响。
- 网络验证依赖 Clash 代理开启 → 活链路 curl 用 dangerouslyDisableSandbox;MCP 工具级实测留给用户 /mcp 重连后做。
