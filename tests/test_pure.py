"""纯函数单测：信源解析 / 合并 / 信源上下文组装。不依赖网络。"""
import sys
import os
from pathlib import Path

# 让测试可直接从源码跑（无需安装包）
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multsearch.utils import extract_unique_urls
from multsearch.sources import merge_sources, split_answer_and_sources
from multsearch.server import _build_source_context, _extra_results_to_sources


def test_extract_unique_urls_dedup_and_order():
    out = extract_unique_urls("see https://a.com and https://a.com again and http://b.com/x")
    assert out == ["https://a.com", "http://b.com/x"]


def test_merge_sources_dedup_by_url():
    a = [{"url": "https://a.com", "title": "A"}]
    b = [{"url": "https://a.com"}, {"url": "https://b.com"}]
    merged = merge_sources(a, b)
    assert len(merged) == 2
    assert merged[0]["url"] == "https://a.com"
    assert merged[0].get("title") == "A"
    assert merged[1]["url"] == "https://b.com"


def test_merge_sources_skips_empty_url():
    a = [{"url": "", "title": "x"}, {"title": "no-url"}]
    b = [{"url": "https://c.com"}]
    merged = merge_sources(a, b)
    assert merged == [{"url": "https://c.com"}]


def test_split_heading_sources_cn():
    text = "答案正文\n\n## 信源\n- [A](https://a.com)\n- [B](https://b.com)"
    answer, sources = split_answer_and_sources(text)
    assert answer == "答案正文"
    assert len(sources) == 2
    assert sources[0] == {"title": "A", "url": "https://a.com"}
    assert sources[1] == {"title": "B", "url": "https://b.com"}


def test_split_tail_link_block():
    text = "答案在这里\n\nhttps://a.com\nhttps://b.com"
    answer, sources = split_answer_and_sources(text)
    assert answer == "答案在这里"
    urls = [s["url"] for s in sources]
    assert urls == ["https://a.com", "https://b.com"]


def test_split_no_sources_returns_all_as_answer():
    text = "纯答案没有信源"
    answer, sources = split_answer_and_sources(text)
    assert answer == "纯答案没有信源"
    assert sources == []


def test_build_source_context_numbered():
    firecrawl = [{"title": "F1", "url": "https://f.com", "description": "d1"}]
    tavily = [{"title": "T1", "url": "https://t.com", "content": "c1"}]
    ctx = _build_source_context(tavily, firecrawl)
    # firecrawl 先于 tavily
    assert "[1] F1" in ctx
    assert "URL: https://f.com" in ctx
    assert "内容: d1" in ctx
    assert "[2] T1" in ctx
    assert "URL: https://t.com" in ctx
    assert "内容: c1" in ctx


def test_build_source_context_dedup_url():
    # 同一 url 在两边都出现，只算一次
    firecrawl = [{"title": "F", "url": "https://dup.com", "description": "d"}]
    tavily = [{"title": "T", "url": "https://dup.com", "content": "c"}]
    ctx = _build_source_context(tavily, firecrawl)
    assert ctx.count("https://dup.com") == 1
    assert "[2]" not in ctx  # 只有 1 条


def test_extra_results_to_sources_provider_tag():
    firecrawl = [{"title": "F1", "url": "https://f.com", "description": "d1"}]
    tavily = [{"title": "T1", "url": "https://t.com", "content": "c1"}]
    sources = _extra_results_to_sources(tavily, firecrawl)
    assert len(sources) == 2
    assert sources[0]["provider"] == "firecrawl"
    assert sources[0]["description"] == "d1"
    assert sources[1]["provider"] == "tavily"
    assert sources[1]["description"] == "c1"


def test_extra_results_to_sources_none_inputs():
    assert _extra_results_to_sources(None, None) == []
    assert _extra_results_to_sources([], []) == []
