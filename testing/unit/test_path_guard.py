"""path_guard 回归测试（2026-08-25 线上事故：委派链路径幻觉）。

事故：manager 把工具真实返回的 outputs/raw/en-us.md 重构为
overwatch-champions-series-en-us.md，下游按假路径读取失败后误判
"抓取失败"无限重试。本文件锁定三道修复：
1. validate_claimed_output_paths：委派结果声称路径磁盘校验 + 目录清单纠错
2. sanitize_step_text：决策事件剥离裸 tool-call JSON / 折叠重复行
3. fetch_url._resolve_slug：save_as 命名契约（防穿越清洗）
4. view_file 404 附目录清单（_file_utils.dir_listing_hint）
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.config import settings
from app.crews.path_guard import sanitize_step_text, validate_claimed_output_paths


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把沙箱根切到临时目录，并造一个 raw 目录（模拟线上 outputs/raw/）。"""
    raw = tmp_path / "outputs" / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(settings, "SANDBOX_DATA_DIR", str(tmp_path))
    return tmp_path


def _make_file(path: Path, content: str = "x" * 1024, age_min: int = 0) -> None:
    path.write_text(content, encoding="utf-8")
    stamp = time.time() - age_min * 60
    os.utime(path, (stamp, stamp))


class TestValidateClaimedPaths:
    def test_no_claims_unchanged(self, sandbox):
        result = "抓取失败：HTTP 403，无文件产出"
        assert validate_claimed_output_paths(result) == result

    def test_existing_claim_unchanged(self, sandbox):
        _make_file(sandbox / "outputs" / "raw" / "en-us.md")
        result = "✅ 已抓取，文件=outputs/raw/en-us.md"
        assert validate_claimed_output_paths(result) == result

    def test_fabricated_path_gets_dir_listing(self, sandbox):
        """核心回归：声称的假路径 → 追加目录清单，真实文件名可见。"""
        _make_file(sandbox / "outputs" / "raw" / "en-us.md", content="y" * 15000, age_min=2)
        _make_file(sandbox / "outputs" / "raw" / "older.md", age_min=600)
        result = (
            "✅ 已抓取：Overwatch Esports，1284字，"
            "文件=outputs/raw/overwatch-champions-series-en-us.md"
        )
        out = validate_claimed_output_paths(result, coworker="内容编排主管")
        # 原文保留 + 纠错附注出现，且真实文件在清单中、最新文件排最前
        assert result in out
        assert "[系统路径校验]" in out
        assert "en-us.md" in out
        listing = out.split("最新在前）：\n", 1)[1]
        assert listing.index("en-us.md") < listing.index("older.md")

    def test_parent_missing_no_annotation(self, sandbox):
        """目录不存在的声称路径不抢戏（保持原报错语义）。"""
        result = "文件保存于 outputs/web_ingest/never-dir/x.md"
        assert validate_claimed_output_paths(result) == result

    def test_traversal_claim_ignored(self, sandbox):
        """越界路径（.. 逃逸）不触发校验，由沙箱防线处理。"""
        _make_file(sandbox / "outputs" / "raw" / "a.md")
        result = "见 outputs/../../etc/passwd.md 与 outputs/raw/a.md"
        assert validate_claimed_output_paths(result) == result

    def test_guard_failure_degrades_to_original(self, sandbox, monkeypatch):
        """守卫内部异常必须降级放行原文，绝不阻断委派。"""
        monkeypatch.setattr(
            "app.tools._file_utils._data_base", lambda: 1 / 0  # type: ignore[misc]
        )
        result = "文件=outputs/raw/en-us.md"
        assert validate_claimed_output_paths(result) == result


class TestSanitizeStepText:
    def test_normal_text_unchanged(self):
        text = "分析网页结构\n提取选手名单\n准备委派撰写员"
        assert sanitize_step_text(text) == text

    def test_dedup_repeated_lines(self):
        """决策卡同段文字 ×2 → 折叠为一次。"""
        text = "结论：需要重试\n结论：需要重试\n下一步：委派阅读员"
        assert sanitize_step_text(text) == "结论：需要重试\n下一步：委派阅读员"

    def test_strip_bare_toolcall_json(self):
        """裸 tool-call JSON 残留（线上决策卡出现 ×3）→ 剥离。"""
        text = (
            '{"name": "fetch_url", "arguments": {"selector": "main", '
            '"url": "https://x.com"}}\n'
            "决策：先核对文件路径\n"
            '{"name": "fetch_url", "arguments": {"url": "https://y.com"}},'
        )
        out = sanitize_step_text(text)
        assert "fetch_url" not in out
        assert "决策：先核对文件路径" in out

    def test_strip_emoji_prefixed_toolcall_variant(self):
        """2026-08-25 第三轮 trace 变体：'🕗 {...} </tool_call>'。"""
        text = (
            '🕗 {"name": "delegate_work_to_coworker", "arguments": '
            '{"coworker": "内容撰写员", "task": "汇报失败"}} </tool_call>\n'
            "结论：流程暂停待用户输入"
        )
        out = sanitize_step_text(text)
        assert "delegate_work_to_coworker" not in out
        assert "</tool_call>" not in out
        assert "结论：流程暂停待用户输入" in out

    def test_all_json_falls_back_to_original(self):
        text = '{"name": "t", "arguments": {"a": 1}}'
        assert sanitize_step_text(text) == text

    def test_empty_safe(self):
        assert sanitize_step_text("") == ""


class TestFetchUrlSaveAs:
    def test_save_as_wins(self):
        from app.tools.fetch_url_tool import _resolve_slug

        assert _resolve_slug("https://esports.overwatch.com/en-us", "owcs-home") == "owcs-home"

    def test_save_as_extension_and_traversal_stripped(self):
        from app.tools.fetch_url_tool import _resolve_slug

        assert _resolve_slug("https://x.com/a", "evil/../../name.md") == "name"
        assert _resolve_slug("https://x.com/a", "带扩展名.md") == "带扩展名"

    def test_default_unchanged(self):
        """未指定 save_as 时行为与旧版完全一致（URL 派生）。"""
        from app.tools.fetch_url_tool import _resolve_slug, _url_to_slug

        assert _resolve_slug("https://esports.overwatch.com/en-us") == _url_to_slug(
            "https://esports.overwatch.com/en-us"
        )


class TestViewFileDirHint:
    def test_not_found_lists_real_files(self, sandbox):
        """view_file 404 → 附目录清单与真实文件名（第二道防线）。"""
        from app.tools._file_utils import dir_listing_hint, resolve_read_path

        _make_file(sandbox / "outputs" / "raw" / "en-us.md", content="z" * 15000, age_min=1)
        missing = resolve_read_path("outputs/raw/overwatch-champions-series-en-us.md")
        # resolve_read_path 不校验存在性，直接拿 resolved 路径测 hint
        assert not missing.exists()
        hint = dir_listing_hint(missing)
        assert hint is not None
        assert "en-us.md" in hint
        assert "逐字复制" in hint
