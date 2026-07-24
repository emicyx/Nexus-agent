"""URL 抓取工具：获取网页 HTML 并转为 markdown，自动保存到磁盘。

双层抓取策略：
  path A: requests + html2text（快路径，适合静态 HTML）
  path B: Playwright 浏览器渲染（降级路径，处理 SPA / 反爬）

降级触发条件：
  - requests 抓取失败（HTTP 错误/超时/空响应）
  - markdown 内容过短（< _MIN_CONTENT_CHARS，疑似 SPA 空壳）
  - markdown 命中反爬关键词（疑似登录墙/验证码页）

抓取成功后自动将 markdown 保存到 /app/data/outputs/raw/{slug}.md，
只返回简短摘要（标题+字数+文件路径），不再返回全文。
这样避免 LLM 通过 function calling 传递大段文本（qwen-turbo 对大参数
支持有限），文件 I/O 由工具代码直接完成，确定性高。

CrewAI akickoff() 在主 asyncio 事件循环中调用 _run，
而 Playwright sync API 不能在已运行的 asyncio loop 里用，
故 path B 必须丢到独立线程池执行（线程内无 asyncio loop 冲突）。
"""
import concurrent.futures
import logging
import re
import threading
from pathlib import Path
from typing import Any

import requests
from crewai.tools import BaseTool
from html2text import HTML2Text
from pydantic import BaseModel, Field

from app.config import settings
from app.tools._file_utils import resolve_output_path

logger = logging.getLogger("fetch_url")

# 全局 html2text 配置（单例避免重复构造）
_h2t = HTML2Text()
_h2t.ignore_links = False
_h2t.ignore_images = False
_h2t.body_width = 0  # 不换行截断，保留原始段落结构
_h2t.unicode_snob = True  # 保留中文等 Unicode 字符

# 文件名 slug 最大长度
_SLUG_MAX_LEN = 60

# 静态抓取的最小有效内容字数（少于此值视为疑似 SPA 空壳）
_MIN_CONTENT_CHARS = 500

# 反爬关键词：命中任一即视为疑似反爬页（登录墙/验证码/JS 必须页）
_ANTI_BOT_KEYWORDS = (
    "请登录", "登录后查看", "扫码登录", "登录知乎",
    "人机验证", "验证码", "滑动验证", "安全验证",
    "access denied", "verify you are human", "enable javascript",
    "robot", "captcha",
)

# SPA hash 路由常见正文容器选择器（docsify/vuepress/gitbook 等）
_SPA_CONTAINER_SELECTORS = ("#main", "#app", "article", ".markdown-section", ".content")

# Playwright 专用线程池：单线程串行执行，避免多 crew 并发争用同一个 browser。
# 关键：必须在独立线程内运行 sync_playwright，否则 "It looks like you are using
# Playwright Sync API inside the asyncio loop" 报错（CrewAI akickoff 在主 asyncio
# loop 中调用工具 _run）。
_pw_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="fetch-pw",
)
_PW_TIMEOUT_SECONDS = 90  # Playwright 整体超时（含导航+等待+渲染）

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class FetchUrlInput(BaseModel):
    url: str = Field(
        ...,
        description="要抓取的网页 URL，必须包含 http:// 或 https://",
    )
    selector: str = Field(
        "",
        description=(
            "可选：仅抽取匹配 CSS 选择器的元素内容（如 'main', 'article', '#content'）。"
            "为空时抓取整个 body。"
        ),
    )


class FetchUrlTool(BaseTool):
    """抓取指定 URL 的网页内容并转为 markdown。

    双层抓取：requests 快路径失败/疑似 SPA 或反爬时，自动降级 Playwright 浏览器渲染。
    """

    name: str = "fetch_url"
    description: str = (
        "抓取指定 URL 的网页内容并转换为 markdown 格式，自动保存到磁盘。"
        "触发时机：当需要阅读某个网页、提取网页正文内容、"
        "为知识库入库准备 markdown 素材时使用。\n"
        "行为：抓取成功后自动将完整 markdown 保存到 outputs/raw/{slug}.md，"
        "只返回简短摘要（标题+字数+文件路径），不返回全文。\n"
        "适用边界：内置双层抓取——先 requests 快速抓静态 HTML，"
        "若失败/内容过短/命中反爬关键词，自动降级到 Playwright 浏览器渲染 JS 后再抓，"
        "可处理 SPA（docsify/vuepress/hash 路由）和大部分反爬站点。"
    )
    args_schema: type[BaseModel] = FetchUrlInput

    def _run(
        self,
        url: str,
        selector: str = "",
        **kwargs: Any,
    ) -> str:
        url = (url or "").strip()
        if not url:
            return "错误：url 不能为空"
        if not (url.startswith("http://") or url.startswith("https://")):
            return f"错误：url 必须以 http:// 或 https:// 开头，收到: {url}"

        # ── path A: requests 快路径 ──
        requests_html: str | None = None
        requests_err: str | None = None
        try:
            resp = requests.get(
                url,
                headers=_DEFAULT_HEADERS,
                timeout=30,
                allow_redirects=True,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            requests_html = resp.text
        except requests.RequestException as e:
            requests_err = f"{type(e).__name__}: {e}"
            logger.warning("fetch_url requests failed: %s -> %s", url, e)

        # 判定 requests 是否需要降级
        if requests_html:
            markdown_a = _h2t.handle(requests_html).strip()
            if (
                len(markdown_a) >= _MIN_CONTENT_CHARS
                and not _looks_like_anti_bot(markdown_a)
            ):
                # 静态抓取成功 → 自动保存到磁盘，返回摘要
                title = _extract_title(requests_html)
                slug = _url_to_slug(url)
                file_path = _save_markdown_to_file(markdown_a, slug)
                logger.info(
                    "fetch_url requests ok → saved: %s (markdown=%d chars)",
                    url, len(markdown_a),
                )
                return _format_summary(url, title, len(markdown_a), file_path, source="requests")
            logger.info(
                "fetch_url requests 返回内容过短或疑似反爬，降级 Playwright: "
                "%s (markdown=%d chars, anti_bot=%s)",
                url, len(markdown_a),
                _looks_like_anti_bot(markdown_a),
            )

        # ── path B: Playwright 降级 ──
        if not settings.FETCH_URL_PLAYWRIGHT_FALLBACK:
            if requests_err:
                return f"抓取失败（降级已禁用）：{requests_err}"
            return (
                f"抓取成功但内容为空或疑似反爬页（降级已禁用）：{url}\n"
                "可在 config 中开启 FETCH_URL_PLAYWRIGHT_FALLBACK=True 启用浏览器渲染降级。"
            )

        pw_result = _fetch_with_playwright(url, selector)
        if pw_result is None:
            # Playwright 也失败 → 返回 path A 的错误
            if requests_err:
                return f"抓取失败（requests + playwright 均失败）：{requests_err}"
            return f"抓取失败：requests 无内容、Playwright 抓取失败：{url}"
        pw_html, pw_title = pw_result
        markdown_b = _h2t.handle(pw_html).strip()
        if not markdown_b:
            return f"Playwright 渲染成功但转换 markdown 为空：{url}"
        # Playwright 成功 → 自动保存到磁盘，返回摘要
        slug = _url_to_slug(url)
        file_path = _save_markdown_to_file(markdown_b, slug)
        logger.info(
            "fetch_url playwright ok → saved: %s (markdown=%d chars)",
            url, len(markdown_b),
        )
        return _format_summary(url, pw_title, len(markdown_b), file_path, source="playwright")


def _looks_like_anti_bot(markdown: str) -> bool:
    """检测 markdown 文本是否命中反爬关键词（登录墙/验证码/JS 必须页）。"""
    if not markdown:
        return True
    lower = markdown.lower()
    for kw in _ANTI_BOT_KEYWORDS:
        if kw in markdown or kw.lower() in lower:
            return True
    return False


def _is_spa_hash_url(url: str) -> bool:
    """判定是否为 hash 路由 SPA URL（如 docsify/vuepress 的 #/path）。"""
    if "#" not in url:
        return False
    fragment = url.split("#", 1)[1]
    return fragment.startswith("/")


def _fetch_with_playwright(url: str, selector: str = "") -> tuple[str, str] | None:
    """用 Playwright 渲染并抓取 HTML。

    返回 (html, title) 或 None（失败）。

    实现要点：必须在独立线程内运行 sync_playwright，因为 CrewAI akickoff
    在主 asyncio loop 中调用 _run，而 Playwright sync API 不能在已运行的
    asyncio loop 内启动。这里把整个抓取逻辑丢到 _pw_executor 线程池，
    并阻塞等待结果。
    """
    future = _pw_executor.submit(_fetch_with_playwright_impl, url, selector)
    try:
        return future.result(timeout=_PW_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        logger.warning("fetch_url playwright thread timeout: %s", url)
        future.cancel()
        return None
    except Exception as e:
        logger.warning("fetch_url playwright thread failed: %s -> %s", url, e)
        return None


def _fetch_with_playwright_impl(url: str, selector: str) -> tuple[str, str] | None:
    """实际 Playwright 抓取逻辑，运行在 _pw_executor 独立线程内。

    每次创建临时 browser/page，避免跨线程争用全局单例。
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as e:
        logger.warning("fetch_url playwright import failed: %s", e)
        return None

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.set_default_timeout(30000)

            # 用 domcontentloaded 快速完成导航；networkidle 对 SPA 过于严格易超时
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # 尽力等待 networkidle（5s 超时即可，不阻塞）
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeout:
                pass  # SPA 长连接，networkidle 等不到也无所谓

            # SPA hash 路由：等待常见正文容器（任一命中即继续）
            if _is_spa_hash_url(url):
                for sel in _SPA_CONTAINER_SELECTORS:
                    try:
                        page.wait_for_selector(sel, timeout=10000)
                        break
                    except PlaywrightTimeout:
                        continue

            # 优先抓指定 selector；否则取 body inner_html
            target = selector if selector.strip() else "body"
            try:
                html = page.inner_html(target)
            except Exception:
                html = page.content()

            try:
                title = page.title()
            except Exception:
                title = "(无标题)"
            return html, title
    except Exception as e:
        logger.warning("fetch_url playwright render failed: %s -> %s", url, e)
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _format_summary(
    url: str, title: str, char_count: int, file_path: str, source: str,
) -> str:
    """返回简短摘要（不包含正文），供 LLM agent 消费。"""
    return (
        f"✅ 已抓取：{title}，{char_count}字，"
        f"文件={file_path}，抓取方式={source}"
    )


def _url_to_slug(url: str) -> str:
    """从 URL 提取文件名友好的 slug。

    例：https://www.runoob.com/ai-agent/langgraph-quick-start.html
    → langgraph-quick-start
    """
    # 去掉协议和域名，取路径部分
    path = re.sub(r"^https?://[^/]+/", "", url)
    # 去掉末尾的 .html/.htm/.php/.asp 等扩展名
    path = re.sub(r"\.(html?|php|asp|jsp)(\?.*)?$", "", path)
    # 去掉 query string 和 fragment
    path = path.split("?")[0].split("#")[0]
    # 取最后一个非空路径段
    parts = [p for p in path.strip("/").split("/") if p]
    slug = parts[-1] if parts else "index"
    # 替换非字母数字为连字符
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]", "-", slug)
    # 合并多余连字符
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    # 截断
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or "page"


def _save_markdown_to_file(markdown: str, slug: str) -> str:
    """保存 markdown 到 outputs/raw/{slug}.md，返回相对路径。"""
    # 清理多余空行（保留输出质量）
    lines = [ln.rstrip() for ln in markdown.splitlines()]
    cleaned: list[str] = []
    blank_streak = 0
    for ln in lines:
        if ln == "":
            blank_streak += 1
            if blank_streak <= 2:
                cleaned.append(ln)
        else:
            blank_streak = 0
            cleaned.append(ln)
    content = "\n".join(cleaned).strip()

    file_path = resolve_output_path(f"{slug}.md", sub_dir="raw")
    file_path.write_text(content, encoding="utf-8")
    # 返回相对于 /app/data/ 的路径（和其他工具保持一致）
    return f"outputs/raw/{slug}.md"


def _extract_title(html: str) -> str:
    """从 HTML 中提取 <title>。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    return "(无标题)"
