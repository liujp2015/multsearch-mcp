# multsearch-mcp

**Claude Code 双路联网搜索 MCP**

两条独立搜索路径交叉验证 · 信源显式回传 · 国内可用

[![Python](https://img.shields.io/badge/python-3.10+-3776ab.svg)](https://www.python.org/downloads/) [![MCP](https://img.shields.io/badge/MCP-server-7e57c2.svg)](https://modelcontextprotocol.io/)
[![M8ven Live Monitored](https://m8ven.ai/badge/mcp/liujp2015-multsearch-mcp-y5z1mk)](https://m8ven.ai/mcp/liujp2015-multsearch-mcp-y5z1mk)

---

## 这是什么

一个为 Claude Code 设计的本地 MCP 服务器。装上之后，Claude 回答前会**真的去网上查**，并把信源显式带回来，而不是凭印象瞎答。

核心是**两条独立的搜索路径**，互相交叉验证：

| 路径 | 工具 | 索引来源 | 模型 |
| --- | --- | --- | --- |
| **Path A** | `web_search` | Tavily + Firecrawl + SearXNG 找源 | LLM 基于信源 summarize（默认火山方舟 Ark `glm-5.2`） |
| **Path B** | `gemini_search` | Google Search grounding | Gemini 2.5 Flash |

两条路用的是**完全不同的索引**（Tavily / Firecrawl / SearXNG vs Google），适合对关键事实做双路交叉验证。除此之外还有网页抓取、站点扫描、信源回查、配置诊断等辅助工具。

## 提供的工具

| 工具 | 作用 |
| --- | --- |
| `web_search` | Tavily + Firecrawl + SearXNG 并行找源 → LLM 综合成带 `[编号]` 内联引用的答案（Path A） |
| `gemini_search` | Gemini + Google grounding 直接给 grounded 答案 + Google 引用（Path B） |
| `get_sources` | 用 `web_search` / `gemini_search` 返回的 `session_id` 取回完整信源列表 |
| `web_fetch` | 抓任意网页转结构化 Markdown（Tavily extract → Firecrawl 兜底） |
| `web_map` | 扫描一个站点，列出全部可访问 URL |
| `get_config_info` | 查看当前配置 + 测试 API 连通性 + 列出可用模型 |
| `switch_model` | 切换默认 LLM 模型并持久化到 `~/.config/multsearch/config.json` |

**为什么不直接用 Claude Code 自带的 `WebSearch` / `WebFetch`？**

- 自带工具受后端策略限制，部分地区不可用、命中率不稳定
- 自带工具不会把搜索结果**显式喂给模型**，Claude 经常仍走内部记忆，幻觉率高
- 没有结构化信源回传，难以追溯

本项目把这三件事都修好了，且**主模型走火山方舟 Ark（国内直连、无需代理）**，Tavily / Firecrawl / Gemini 走代理，SearXNG 默认直连。

---

## 工作原理

```
你 → Claude Code → multsearch MCP → ┬─ Tavily + Firecrawl + SearXNG 找源 ─► LLM summarize   (web_search, Path A)
                                    ├─ Gemini + Google grounding                   (gemini_search, Path B)
                                    ├─ Tavily extract / Firecrawl scrape           (web_fetch)
                                    └─ Tavily map                                  (web_map)
```

**`web_search` 文本模式**：Tavily + Firecrawl + SearXNG **并行**抓信源 → 组装成编号信源文本 → 喂给 LLM（glm-5.2）让它**只依据信源**作答，每条事实带 `[n]` 引用。模型本身不联网，所以不会幻觉编造。信源以 Tavily / Firecrawl / SearXNG 的实际结果为准，模型若附了信源列表会被剥离。同 query + model 的响应走 LRU 缓存直接命中。

**`gemini_search`**：走 Gemini 原生 Google Search grounding，返回 grounded 答案 + Google 引用，遇 429 / 5xx 指数退避重试。

---

## 30 秒上手

两种安装方式：**A. 让 Claude Code 自己配**（推荐）/ **B. 交互式向导**。

### A. Claude Code 驱动（非交互模式，推荐）

把这一段直接发给 Claude Code：

> 我本地有 `multsearch-mcp` 源码（路径：`<填绝对路径>`），请把它安装到我当前项目里（scope=project）。
> 你需要先 `cd` 到我当前项目目录，然后通过下面这条命令完成安装。我会告诉你我的 Ark API Key（`ark-...` 开头），Tavily / Firecrawl / Gemini 都跳过（SearXNG 默认就启用，无需配置）：
>
> ```
> uvx --from '<源码路径>[setup]' \
>     multsearch-setup \
>     --non-interactive \
>     --package-spec <源码路径> \
>     --scope project \
>     --provider ark \
>     --api-key <我会粘贴的 Ark Key>
> ```
>
> 装完检查 .mcp.json 是否只在当前目录、`claude mcp list` 是否能看到 multsearch。

### B. 交互式向导（自己跑）

```bash
cd ~/your-project          # ← 关键：决定 .mcp.json 写到哪里
uvx --from '<源码路径>[setup]' multsearch-setup --package-spec <源码路径>
```

向导会一步步问 LLM 服务来源（Ark / 自定义）、API Key、Tavily / Firecrawl / Gemini（均可选）、作用域，全程键盘选项 + 回车，**不需要手写任何配置文件**。完成后自动跑 `claude mcp add-json` 注册到当前项目 `.mcp.json`。重启 Claude Code 后 `/mcp` 能看到 `multsearch`。

> 没装 `uv`？`curl -LsSf https://astral.sh/uv/install.sh | sh` 然后重开终端。

---

## API Key 申请指南

主 LLM Key 必需，其余可选。**零成本组合 = Ark + Tavily + Gemini**，三家都有免费额度，全程不用绑卡。SearXNG 默认免费开启、无需 key，作为 web_search 第三路信源。

### 1. 火山方舟 Ark（主 LLM，必需）

| 项 | 说明 |
| --- | --- |
| 控制台 | https://console.volcengine.com/ark |
| Key 前缀 | `ark-...` |
| 模型 | `glm-5.2`（纯文本，国内直连无需代理） |
| 计费 | 按 token，有免费 / 体验额度 |

Ark 走国内直连，Tavily / Firecrawl / Gemini 走代理。

### 2. Tavily（强烈建议，可选）

| 项 | 说明 |
| --- | --- |
| 注册 | https://app.tavily.com/home |
| 免费额度 | **1,000 次 / 月**，无需绑卡 |
| 用途 | `web_search` 找源 + `web_fetch` / `web_map` |

不配 Tavily：`web_search` 退化为 Firecrawl + SearXNG 两路；`web_fetch` / `web_map` 不可用。

### 3. Firecrawl（可选）

| 项 | 说明 |
| --- | --- |
| 注册 | https://www.firecrawl.dev/signin?view=signup |
| 免费额度 | **1,000 credits / 月**，无需绑卡 |
| 用途 | `web_search` 找源 + `web_fetch` 兜底 |

### 4. Gemini（可选 · 第二路搜索）

| 项 | 说明 |
| --- | --- |
| 取 Key | https://aistudio.google.com/apikey（免费） |
| 用途 | `gemini_search` 独立 Google 索引交叉验证 |
| 注意 | Google API 在中国大陆需走代理（`HTTP_PROXY` / `HTTPS_PROXY`） |

### 5. SearXNG（可选 · 第三路信源）

| 项 | 说明 |
| --- | --- |
| 是什么 | 自建 / 公开的元搜索引擎，聚合 Google / Bing / DuckDuckGo 等多引擎结果 |
| 默认地址 | `http://45.197.145.62:8081`（可用 `SEARXNG_URL` 覆盖，或写进 `~/.config/multsearch/config.json` 的 `searxng_url`） |
| 用途 | `web_search` 第三路信源，与 Tavily / Firecrawl 并行采集 |
| Key | 无需 API Key |
| 引擎 | `SEARXNG_ENGINES` 默认 `google,bing,duckduckgo`（多引擎聚合，单引擎挂掉其他兜底） |
| 代理 | 代码内 `trust_env=False` 直连，不走 `HTTP_PROXY` |
| 注意 | 实例需在 `settings.yml` 开启 `json` 输出格式；实测该实例 google 引擎被限流返回空，故默认多引擎聚合 |

---

## 配置文件形态

向导跑完后，项目根目录的 `.mcp.json` 大致长这样（敏感字段已脱敏）：

```json
{
  "mcpServers": {
    "multsearch": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "<源码路径>", "multsearch"],
      "env": {
        "MULT_PROVIDER": "custom",
        "MULT_API_URL": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "MULT_API_KEY": "ark-****",
        "MULT_MODEL": "glm-5.2",
        "MULT_MAX_TOKENS": "8192",
        "TAVILY_API_KEY": "tvly-****",
        "FIRECRAWL_API_KEY": "fc-****",
        "GEMINI_API_KEY": "****",
        "GEMINI_MODEL": "gemini-2.5-flash",
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "http://127.0.0.1:7890",
        "NO_PROXY": "localhost,127.0.0.1,ark.cn-beijing.volces.com,*.volces.com"
      }
    }
  }
}
```

Claude Code 启动时读这个文件，按 stdio 协议拉起一个 Python 进程（本项目），通过 MCP 协议转发工具调用。`httpx` 默认 `trust_env=True`，会读 `HTTP_PROXY` / `NO_PROXY`，所以 Tavily / Firecrawl / Gemini 走代理、Ark 走直连；SearXNG 单独用 `trust_env=False` 直连。

---

## 作用域怎么选

向导第 6 步选作用域：

| Scope | 配置文件位置 | 谁能看到 | 推荐场景 |
| --- | --- | --- | --- |
| **project** | `<项目>/.mcp.json` | 入 git 后团队共享 | **默认** |
| user | `~/.claude.json` | 本机所有项目 | 一台机器多项目都用 |
| local | `<项目>/.claude/settings.local.json` | 仅本机本项目 | 个人调试 |

新手选 `project`，把 `.mcp.json` 提交进 git 即可。

---

## 进阶配置

### 切换 Provider / 模型

向导第 2 步可选 `Ark` / `自定义`。已装好想改：重新跑向导，或直接编辑 `.mcp.json` 的 `env` 段。运行时也可用 `switch_model` 工具切换模型并持久化到 `~/.config/multsearch/config.json`。

### 完整环境变量参考

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MULT_PROVIDER` | `custom` | `custom` / `ark`（向导预设） |
| `MULT_API_URL` | — | OpenAI 兼容端点（Ark: `https://ark.cn-beijing.volces.com/api/plan/v3`） |
| `MULT_API_KEY` | — | **必需** |
| `MULT_MODEL` | `glm-5.2` | LLM 模型，可用 `switch_model` 切换 |
| `MULT_MAX_TOKENS` | — | 推理模型建议 `8192`（reasoning 要吃 token） |
| `MULT_RETRY_MAX_ATTEMPTS` | `3` | LLM 流式请求的最大重试次数 |
| `MULT_RETRY_MULTIPLIER` | `1` | 指数退避 multiplier |
| `MULT_RETRY_MAX_WAIT` | `10` | 单次重试最大等待秒数 |
| `TAVILY_API_KEY` | — | 可选，`web_search` 找源 + `web_fetch` / `web_map` |
| `TAVILY_API_URL` | `https://api.tavily.com` | Tavily API 入口 |
| `MULT_TAVILY_ENABLED` | `true` | 关闭则跳过 Tavily 找源 |
| `FIRECRAWL_API_KEY` | — | 可选，`web_search` 找源 + `web_fetch` 兜底 |
| `FIRECRAWL_API_URL` | `https://api.firecrawl.dev/v2` | Firecrawl API 入口 |
| `GEMINI_API_KEY` | — | 可选，`gemini_search` 第二路 |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini 模型 |
| `GEMINI_API_URL` | `https://generativelanguage.googleapis.com` | Gemini API 入口 |
| `SEARXNG_URL` | `http://45.197.145.62:8081` | SearXNG 实例地址（第三路信源） |
| `SEARXNG_ENGINES` | `google,bing,duckduckgo` | 逗号分隔的引擎列表 |
| `MULT_SEARXNG_ENABLED` | `true` | 关闭则不采集 SearXNG 信源 |
| `HTTP_PROXY` / `HTTPS_PROXY` | — | 中国大陆访问 Tavily / Firecrawl / Gemini 需代理（SearXNG 直连不受影响） |
| `NO_PROXY` | — | 国内 API（`ark.cn-beijing.volces.com` 等）直连 |
| `MULT_DEBUG` | `false` | 详细日志 |
| `MULT_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `MULT_LOG_DIR` | `~/.config/multsearch/logs` | 日志输出目录 |

配置优先级：环境变量 > `~/.config/multsearch/config.json`。

---

## 验证安装

```bash
claude mcp list
# 应该出现一行：multsearch    connected
```

在 Claude Code 里新开会话：

> 用 multsearch 的 web_search 搜一下「AKShare stock_zh_a_spot_em 的 limit 参数默认返回多少只」，把信源列出来。

连通后会看到 Claude 调用 `web_search`，输出带 `[编号]` 引用的回答。再用 `get_sources` 取回完整信源列表。

第二路：

> 用 multsearch 的 gemini_search 搜同一个问题，对比两路结果。

---

## 开发与测试

```bash
# 安装开发依赖
uv sync --extra dev

# 跑纯函数单测（不依赖网络）
pytest tests/
```

`test_text_mode.py` 是端到端活链路验证脚本：Tavily / Firecrawl 并行找源 → LLM summarize，需要先准备好 `MULT_API_KEY` / `TAVILY_API_KEY` / `FIRECRAWL_API_KEY`。

---

## 常见问题

**Q：`/mcp` 看不到 multsearch？**
A：99% 是 Claude Code 没重启。先 `claude mcp list` 看命令行是否能看到，能看到就完全重启 Claude Code（退出再开，不是新会话）。

**Q：改了源码不生效？**
A：`uv tool run --from <path>` 会缓存构建的 wheel。改源码后**bump `pyproject.toml` 的 `version`**（强制重建 wheel），或清 uv 缓存（`sdists-v9/path` + `archive-v0` 下本包目录），再 `/mcp` 重连。

**Q：`web_search` 返回空 content？**
A：多半是 Tavily / Firecrawl 没连上（代理没开 → HTTP 000）。代码里 `except Exception: return None` 静默兜底了；SearXNG 默认开启会兜底一路信源（走直连），若三路全空再开代理（Clash `127.0.0.1:7890`），用 `get_config_info` 测连通性，或用 `dangerouslyDisableSandbox` 的 curl 直连 `api.tavily.com` 排查。

**Q：`gemini_search` 报失败？**
A：Google API 必须走代理（不在 `NO_PROXY` 里），且 `GEMINI_API_KEY` 要设置。免费档 15 RPM，并发下可能 429，已内置退避重试。

**Q：能用其它 OpenAI 兼容端点吗？**
A：可以。`MULT_PROVIDER=custom` + 自填 `MULT_API_URL` / `MULT_MODEL` 即可（如 DeepSeek、智谱、内网网关等）。

**Q：为什么没有规划 / 任务拆解类工具？**
A：早期版本有一套 6 阶段搜索规划状态机，实际使用中调用方很少走，维护成本高，已移除以聚焦双路搜索内核。`web_search` 现在是直接的「找源 → 总结」流程，带同 query + model 的 LRU 响应缓存。