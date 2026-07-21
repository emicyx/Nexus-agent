"""Playwright 浏览器自动化工具集

通用浏览器工具（9 个）：
  1. NavigateTool       - 导航到 URL
  2. ClickElementTool   - 点击元素
  3. InputTextTool      - 输入文本
  4. GetElementTextTool - 获取元素文本
  5. ScreenshotTool     - 页面截图
  6. WaitForElementTool - 等待元素状态
  7. SelectOptionTool   - 下拉选择
  8. PressKeyTool       - 键盘操作
  9. GetPageInfoTool    - 获取页面信息
"""
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

# ── 全局配置 ──────────────────────────────────────────────────────────────────

SCREENSHOTS_DIR = "./screenshots"
DEFAULT_TIMEOUT = 30_000       # 全局默认超时 30s（毫秒）
ACTION_DELAY = 0.5             # 操作间隔（秒），避免过快
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


# ── BrowserManager ─────────────────────────────────────────────────────────────

class BrowserManager:
    """
    全局 Playwright 浏览器实例管理器。

    - headless 可配置（默认 True，调试时可设为 False）
    - viewport 固定为 1920x1080
    - 全局默认超时 30s
    - 操作间隔 action_delay（默认 0.5s）
    """
    _playwright = None
    _browser = None
    _page = None
    _headless = True
    _action_delay = ACTION_DELAY

    @classmethod
    def configure(cls, headless: bool = True, action_delay: float = ACTION_DELAY):
        """在首次使用前配置浏览器参数。必须在 get_page() 之前调用。"""
        cls._headless = headless
        cls._action_delay = action_delay

    @classmethod
    def get_page(cls) -> Page:
        if cls._page is None:
            cls._playwright = sync_playwright().start()
            cls._browser = cls._playwright.chromium.launch(headless=cls._headless)
            cls._page = cls._browser.new_page(viewport=DEFAULT_VIEWPORT)
            cls._page.set_default_timeout(DEFAULT_TIMEOUT)
        return cls._page

    @classmethod
    def delay(cls):
        """操作间隔，避免操作过快导致页面未响应。"""
        time.sleep(cls._action_delay)

    @classmethod
    def close(cls):
        if cls._browser:
            cls._browser.close()
            cls._playwright.stop()
            cls._page = None
            cls._browser = None
            cls._playwright = None


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _ensure_screenshots_dir() -> Path:
    """确保截图目录存在，返回路径。"""
    dir_path = Path(SCREENSHOTS_DIR)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def _take_error_screenshot(description: str = "error") -> str:
    """出错时自动截图，返回截图文件路径。"""
    try:
        page = BrowserManager.get_page()
        dir_path = _ensure_screenshots_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"error_{description}_{timestamp}.png"
        filepath = dir_path / filename
        page.screenshot(path=str(filepath))
        return str(filepath)
    except Exception:
        return "截图失败（浏览器可能未启动）"


def _safe_execute(operation: Callable[[], str], description: str, auto_screenshot: bool = True) -> str:
    """
    统一安全执行包装器：
    - 捕获异常并返回友好错误信息
    - 异常时自动截图（如果 auto_screenshot=True）
    - 操作后自动延时
    """
    try:
        result = operation()
        BrowserManager.delay()
        return result
    except PlaywrightTimeout as e:
        error_msg = f"操作超时 [{description}]: {e}"
        if auto_screenshot:
            screenshot_path = _take_error_screenshot(description.replace(" ", "_"))
            error_msg += f"\n已自动截图保存至: {screenshot_path}"
        return error_msg
    except Exception as e:
        error_msg = f"操作失败 [{description}]: {type(e).__name__}: {e}"
        if auto_screenshot:
            screenshot_path = _take_error_screenshot(description.replace(" ", "_"))
            error_msg += f"\n已自动截图保存至: {screenshot_path}"
        return error_msg


# ── 工具 1: 打开网页 ────────────────────────────────────────────────────────

class NavigateToolInput(BaseModel):
    url: str = Field(description="需要打开的网页 URL")
    wait_until: str = Field(
        default="networkidle",
        description="等待页面加载到什么状态：'load'、'domcontentloaded'、'networkidle'（推荐SPA）、'commit'"
    )
    timeout: int = Field(
        default=30000,
        description="导航超时时间（毫秒），默认 30000"
    )

class NavigateTool(BaseTool):
    name: str = "Navigate Tool"
    description: str = (
        "打开浏览器并导航到指定的 URL。"
        "支持配置等待策略（wait_until）和超时时间（timeout）。"
        "触发时机：当需要打开网页、访问特定 URL 时使用。"
        "适用边界：仅用于页面导航，不处理页面内容交互。"
    )
    args_schema: type[BaseModel] = NavigateToolInput

    def _run(self, url: str, wait_until: str = "networkidle", timeout: int = 30000) -> str:
        def operation():
            page = BrowserManager.get_page()
            page.goto(url, wait_until=wait_until, timeout=timeout)
            title = page.title()
            return f"成功导航至: {url}，页面标题: {title}"
        return _safe_execute(operation, f"导航到 {url}")


# ── 工具 2: 点击元素 ────────────────────────────────────────────────────────

class ClickElementToolInput(BaseModel):
    selector: str = Field(description="要点击的元素的 CSS 选择器或 Playwright 定位器 (如 'button.submit', 'text=登录')")
    timeout: int = Field(default=10000, description="等待元素可交互的超时时间（毫秒），默认 10000")
    force: bool = Field(default=False, description="是否强制点击（忽略元素被遮挡等检查），默认 False")
    wait_for: str = Field(default="", description="点击后等待的条件：'networkidle'、'load'、空字符串（不等待）")

class ClickElementTool(BaseTool):
    name: str = "Click Element Tool"
    description: str = (
        "点击网页上的指定元素。"
        "支持等待元素可交互（timeout）、强制点击（force）、点击后等待页面状态变化（wait_for）。"
        "触发时机：当需要点击按钮、链接或其他可交互元素时使用。"
        "适用边界：仅处理点击操作，不处理文本输入。"
    )
    args_schema: type[BaseModel] = ClickElementToolInput

    def _run(self, selector: str, timeout: int = 10000, force: bool = False, wait_for: str = "") -> str:
        def operation():
            page = BrowserManager.get_page()
            page.click(selector, timeout=timeout, force=force)
            if wait_for:
                page.wait_for_load_state(wait_for, timeout=DEFAULT_TIMEOUT)
            return f"成功点击元素: {selector}"
        return _safe_execute(operation, f"点击 {selector}")


# ── 工具 3: 输入文本 ────────────────────────────────────────────────────────

class InputTextToolInput(BaseModel):
    selector: str = Field(description="输入框的 CSS 选择器")
    text: str = Field(description="要输入的文本内容")
    timeout: int = Field(default=10000, description="等待输入框可交互的超时时间（毫秒）")
    clear_first: bool = Field(default=True, description="是否先清空输入框再输入，默认 True")
    press_enter: bool = Field(default=False, description="输入完成后是否按回车键，默认 False")

class InputTextTool(BaseTool):
    name: str = "Input Text Tool"
    description: str = (
        "在网页的指定输入框中填写文本。"
        "支持先清空输入框（clear_first）、输入后按回车（press_enter）。"
        "触发时机：当需要在输入框、文本域中填写内容时使用。"
        "适用边界：仅处理文本输入，不处理下拉选择和点击操作。"
    )
    args_schema: type[BaseModel] = InputTextToolInput

    def _run(self, selector: str, text: str, timeout: int = 10000,
             clear_first: bool = True, press_enter: bool = False) -> str:
        def operation():
            page = BrowserManager.get_page()
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            if clear_first:
                page.fill(selector, "")
            page.fill(selector, text)
            if press_enter:
                page.press(selector, "Enter")
            return f"成功在 '{selector}' 中输入文本: '{text}'"
        return _safe_execute(operation, f"输入文本到 {selector}")


# ── 工具 4: 读取元素文本 ────────────────────────────────────────────────────

class GetElementTextToolInput(BaseModel):
    selector: str = Field(description="要读取文本的元素 CSS 选择器")
    timeout: int = Field(default=10000, description="等待元素出现的超时时间（毫秒）")
    attribute: str = Field(
        default="",
        description="可选：读取元素属性而非文本。如 'value'、'title'、'href'、'class' 等。为空时读取文本内容。"
    )

class GetElementTextTool(BaseTool):
    name: str = "Get Element Text Tool"
    description: str = (
        "获取网页上指定元素的文本内容或属性值，常用于测试断言。"
        "通过 attribute 参数可读取元素属性（如 value、title、href 等）。"
        "触发时机：当需要提取页面文本内容、验证元素属性时使用。"
        "适用边界：仅读取单个元素的文本或属性，不处理批量提取。"
    )
    args_schema: type[BaseModel] = GetElementTextToolInput

    def _run(self, selector: str, timeout: int = 10000, attribute: str = "") -> str:
        def operation():
            page = BrowserManager.get_page()
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            if attribute:
                value = page.get_attribute(selector, attribute)
                return f"元素 '{selector}' 的属性 '{attribute}' 值为: '{value}'"
            else:
                text = page.inner_text(selector)
                return f"元素 '{selector}' 的文本内容为: '{text}'"
        return _safe_execute(operation, f"获取元素文本 {selector}", auto_screenshot=False)


# ── 工具 5: 截图工具 ──────────────────────────────────────────────────────────

class ScreenshotToolInput(BaseModel):
    filename: str = Field(
        default="",
        description="截图文件名（可选），默认自动生成时间戳文件名。如 'login_page.png'"
    )

class ScreenshotTool(BaseTool):
    name: str = "Screenshot Tool"
    description: str = (
        "对当前页面进行截图并保存到 ./screenshots/ 目录。"
        "触发时机：当需要记录测试证据、排查页面问题、保存页面状态时使用。"
        "适用边界：仅截取当前可视区域，不处理全页滚动截图。"
    )
    args_schema: type[BaseModel] = ScreenshotToolInput

    def _run(self, filename: str = "") -> str:
        def operation():
            page = BrowserManager.get_page()
            dir_path = _ensure_screenshots_dir()
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{timestamp}.png"
            filepath = dir_path / filename
            page.screenshot(path=str(filepath))
            return f"截图已保存至: {filepath}"
        return _safe_execute(operation, "页面截图", auto_screenshot=False)


# ── 工具 6: 等待元素工具 ──────────────────────────────────────────────────────

class WaitForElementToolInput(BaseModel):
    selector: str = Field(description="要等待的元素的 CSS 选择器")
    state: str = Field(
        default="visible",
        description="等待元素的状态：'visible'（可见）、'hidden'（隐藏）、'attached'（存在于DOM）、'detached'（从DOM移除）"
    )
    timeout: int = Field(default=10000, description="等待超时时间（毫秒），默认 10000")

class WaitForElementTool(BaseTool):
    name: str = "Wait For Element Tool"
    description: str = (
        "等待网页上的指定元素达到特定状态。"
        "触发时机：当需要等待页面加载完成、弹窗消失、异步内容出现时使用。"
        "适用边界：仅等待元素状态变化，不执行任何交互操作。"
    )
    args_schema: type[BaseModel] = WaitForElementToolInput

    def _run(self, selector: str, state: str = "visible", timeout: int = 10000) -> str:
        def operation():
            page = BrowserManager.get_page()
            page.wait_for_selector(selector, state=state, timeout=timeout)
            return f"元素 '{selector}' 已达到状态: {state}"
        return _safe_execute(operation, f"等待元素 {selector} → {state}")


# ── 工具 7: 下拉选择工具 ──────────────────────────────────────────────────────

class SelectOptionToolInput(BaseModel):
    selector: str = Field(description="<select> 元素的 CSS 选择器")
    value: str = Field(description="要选择的选项的值（value 属性）或显示文本")

class SelectOptionTool(BaseTool):
    name: str = "Select Option Tool"
    description: str = (
        "在网页的下拉选择框（<select>）中选择一个选项。"
        "支持通过选项的 value 值或显示文本来选择。"
        "触发时机：当需要操作下拉菜单、选择列表项时使用。"
        "适用边界：仅适用于 <select> 元素，不处理自定义下拉组件。"
    )
    args_schema: type[BaseModel] = SelectOptionToolInput

    def _run(self, selector: str, value: str) -> str:
        def operation():
            page = BrowserManager.get_page()
            try:
                page.select_option(selector, value=value, timeout=5000)
            except PlaywrightTimeout:
                page.select_option(selector, label=value, timeout=5000)
            return f"成功在 '{selector}' 中选择选项: '{value}'"
        return _safe_execute(operation, f"下拉选择 {selector} → {value}")


# ── 工具 8: 键盘操作工具 ──────────────────────────────────────────────────────

class PressKeyToolInput(BaseModel):
    key: str = Field(
        description="要按下的按键名称。如：'Enter'、'Tab'、'Escape'、'Backspace'、'ArrowDown'、'Control+a'、'Control+c'"
    )

class PressKeyTool(BaseTool):
    name: str = "Press Key Tool"
    description: str = (
        "模拟键盘按键操作。"
        "支持单键（如 'Enter'、'Tab'、'Escape'）和组合键（如 'Control+a'、'Control+c'）。"
        "触发时机：当需要表单提交、快捷键操作、全选文本等场景时使用。"
        "适用边界：仅模拟键盘输入，不处理鼠标操作。"
    )
    args_schema: type[BaseModel] = PressKeyToolInput

    def _run(self, key: str) -> str:
        def operation():
            page = BrowserManager.get_page()
            page.keyboard.press(key)
            return f"成功按下按键: {key}"
        return _safe_execute(operation, f"按键 {key}")


# ── 工具 9: 获取页面信息工具 ──────────────────────────────────────────────────

class GetPageInfoToolInput(BaseModel):
    pass  # 无参数

class GetPageInfoTool(BaseTool):
    name: str = "Get Page Info Tool"
    description: str = (
        "获取当前页面的基本信息，包括 URL、标题。"
        "触发时机：当需要确认当前所在页面状态、验证导航是否成功时使用。"
        "适用边界：仅返回 URL 和标题，不返回页面内容。"
    )
    args_schema: type[BaseModel] = GetPageInfoToolInput

    def _run(self) -> str:
        def operation():
            page = BrowserManager.get_page()
            url = page.url
            title = page.title()
            return f"当前页面 URL: {url}\n页面标题: {title}"
        return _safe_execute(operation, "获取页面信息", auto_screenshot=False)
