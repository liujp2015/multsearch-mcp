"""multsearch-mcp runtime configuration.

Env-var prefix: MULT_*  (e.g. MULT_API_KEY, MULT_MODEL).

Provider mode: custom — 用户自备 MULT_API_URL & MULT_MODEL（任意 OpenAI 兼容
chat/completions 端点；默认火山方舟 Ark glm-5.2）。模型纯文本无联网能力，
由 Tavily/Firecrawl 找源、模型 summarize。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default


def _bool(*names: str, default: bool = False) -> bool:
    v = _env(*names)
    if v is None:
        return default
    return v.lower() in ("true", "1", "yes", "on")


class Config:
    _instance = None

    _SETUP_HINT = (
        "请运行引导脚本完成配置：\n"
        "  uvx --from 'multsearch-mcp[setup]' multsearch-setup\n"
        "或手动设置环境变量 MULT_API_KEY（必需）/ MULT_API_URL / MULT_MODEL。"
    )

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config_file = None
            cls._instance._cached_model = None
        return cls._instance

    # ── persistent config file ─────────────────────────────────────────

    @property
    def config_file(self) -> Path:
        if self._config_file is None:
            config_dir = Path.home() / ".config" / "multsearch"
            try:
                config_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                config_dir = Path.cwd() / ".multsearch"
                config_dir.mkdir(parents=True, exist_ok=True)
            self._config_file = config_dir / "config.json"
        return self._config_file

    def _load_config_file(self) -> dict:
        if not self.config_file.exists():
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_config_file(self, config_data: dict) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            raise ValueError(f"无法保存配置文件: {str(e)}")

    # ── provider selection ─────────────────────────────────────────────

    @property
    def provider(self) -> str:
        v = (_env("MULT_PROVIDER") or self._load_config_file().get("provider") or "").strip().lower()
        return v or "custom"

    @property
    def api_url(self) -> str:
        url = _env("MULT_API_URL") or self._load_config_file().get("api_url")
        if not url:
            raise ValueError(f"API URL 未配置。\n{self._SETUP_HINT}")
        return url

    @property
    def api_key(self) -> str:
        key = _env("MULT_API_KEY") or self._load_config_file().get("api_key")
        if not key:
            raise ValueError(f"API Key 未配置。\n{self._SETUP_HINT}")
        return key

    def set_provider_config(self, provider: str, api_url: str, api_key: str, model: str) -> None:
        """持久化 provider/url/key/model 到 config.json（env 变量优先级仍高于此文件）。"""
        config_data = self._load_config_file()
        config_data["provider"] = provider
        config_data["api_url"] = api_url
        config_data["api_key"] = api_key
        config_data["model"] = model
        self._save_config_file(config_data)
        self._cached_model = model

    # ── debug / retry knobs ────────────────────────────────────────────

    @property
    def debug_enabled(self) -> bool:
        return _bool("MULT_DEBUG")

    @property
    def retry_max_attempts(self) -> int:
        return int(_env("MULT_RETRY_MAX_ATTEMPTS", default="3"))

    @property
    def retry_multiplier(self) -> float:
        return float(_env("MULT_RETRY_MULTIPLIER", default="1"))

    @property
    def retry_max_wait(self) -> int:
        return int(_env("MULT_RETRY_MAX_WAIT", default="10"))

    @property
    def max_tokens(self) -> Optional[int]:
        """LLM 请求的 max_tokens 上限。不设置时由 provider 默认值决定。"""
        v = _env("MULT_MAX_TOKENS")
        if v is None or v.strip() == "":
            return None
        try:
            return int(v)
        except ValueError:
            return None

    # ── Tavily / Firecrawl ─────────────────────────────────────────────

    @property
    def tavily_enabled(self) -> bool:
        return _bool("MULT_TAVILY_ENABLED", "TAVILY_ENABLED", default=True)

    @property
    def tavily_api_url(self) -> str:
        return _env("TAVILY_API_URL", default="https://api.tavily.com") or "https://api.tavily.com"

    @property
    def tavily_api_key(self) -> Optional[str]:
        return _env("TAVILY_API_KEY")

    @property
    def firecrawl_api_url(self) -> str:
        return _env("FIRECRAWL_API_URL", default="https://api.firecrawl.dev/v2") or "https://api.firecrawl.dev/v2"

    @property
    def firecrawl_api_key(self) -> Optional[str]:
        return _env("FIRECRAWL_API_KEY")

    # ── SearXNG (自建元搜索引擎, 聚合 google/bing/duckduckgo) ──────────

    @property
    def searxng_enabled(self) -> bool:
        return _bool("MULT_SEARXNG_ENABLED", "SEARXNG_ENABLED", default=True)

    @property
    def searxng_api_url(self) -> str:
        """SearXNG 实例地址。优先级: env > config.json > 默认实例。"""
        url = (
            _env("SEARXNG_URL", "MULT_SEARXNG_URL")
            or self._load_config_file().get("searxng_url")
        )
        return (url or "http://45.197.145.62:8081").rstrip("/")

    @property
    def searxng_engines(self) -> str:
        """逗号分隔的引擎列表。默认多引擎聚合,因单 google 易被限流返回空。"""
        return (
            _env("SEARXNG_ENGINES", "MULT_SEARXNG_ENGINES", default="google,bing,duckduckgo")
            or "google,bing,duckduckgo"
        )

    # ── Gemini (Google Search grounding) ────────────────────────────────

    @property
    def gemini_api_key(self) -> Optional[str]:
        return _env("GEMINI_API_KEY", "GOOGLE_API_KEY", "MULT_GEMINI_API_KEY") or self._load_config_file().get("gemini_api_key")

    @property
    def gemini_api_url(self) -> str:
        return _env("GEMINI_API_URL", default="https://generativelanguage.googleapis.com") or "https://generativelanguage.googleapis.com"

    @property
    def gemini_model(self) -> str:
        return _env("GEMINI_MODEL", default="gemini-2.5-flash") or "gemini-2.5-flash"

    # ── logging ────────────────────────────────────────────────────────

    @property
    def log_level(self) -> str:
        return (_env("MULT_LOG_LEVEL", default="INFO") or "INFO").upper()

    @property
    def log_dir(self) -> Path:
        log_dir_str = _env("MULT_LOG_DIR", default="logs") or "logs"
        log_dir = Path(log_dir_str)
        if log_dir.is_absolute():
            return log_dir

        home_log_dir = Path.home() / ".config" / "multsearch" / log_dir_str
        try:
            home_log_dir.mkdir(parents=True, exist_ok=True)
            return home_log_dir
        except OSError:
            pass

        cwd_log_dir = Path.cwd() / log_dir_str
        try:
            cwd_log_dir.mkdir(parents=True, exist_ok=True)
            return cwd_log_dir
        except OSError:
            pass

        tmp_log_dir = Path("/tmp") / "multsearch" / log_dir_str
        tmp_log_dir.mkdir(parents=True, exist_ok=True)
        return tmp_log_dir

    # ── model ──────────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        if self._cached_model is not None:
            return self._cached_model
        model = (
            _env("MULT_MODEL")
            or self._load_config_file().get("model")
            or "glm-5.2"
        )
        self._cached_model = model
        return self._cached_model

    def set_model(self, model: str) -> None:
        config_data = self._load_config_file()
        config_data["model"] = model
        self._save_config_file(config_data)
        self._cached_model = model

    # ── reporting ──────────────────────────────────────────────────────

    @staticmethod
    def _mask_api_key(key: Optional[str]) -> str:
        if not key:
            return "未配置"
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

    def get_config_info(self) -> dict:
        try:
            api_url = self.api_url
            api_key_masked = self._mask_api_key(self.api_key)
            config_status = "✅ 配置完整"
        except ValueError as e:
            api_url = "未配置"
            api_key_masked = "未配置"
            config_status = f"❌ 配置错误: {str(e)}"

        return {
            "MULT_PROVIDER": self.provider,
            "MULT_API_URL": api_url,
            "MULT_API_KEY": api_key_masked,
            "MULT_MODEL": self.model,
            "MULT_DEBUG": self.debug_enabled,
            "MULT_LOG_LEVEL": self.log_level,
            "MULT_LOG_DIR": str(self.log_dir),
            "TAVILY_API_URL": self.tavily_api_url,
            "TAVILY_ENABLED": self.tavily_enabled,
            "TAVILY_API_KEY": self._mask_api_key(self.tavily_api_key),
            "FIRECRAWL_API_URL": self.firecrawl_api_url,
            "FIRECRAWL_API_KEY": self._mask_api_key(self.firecrawl_api_key),
            "SEARXNG_URL": self.searxng_api_url,
            "SEARXNG_ENABLED": self.searxng_enabled,
            "SEARXNG_ENGINES": self.searxng_engines,
            "config_status": config_status,
        }


config = Config()
