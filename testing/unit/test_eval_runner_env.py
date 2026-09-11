"""eval harness 单测：runner 环境层的沙箱清理与快照（#8/#9，纯文件系统，无网络）。"""
from testing.eval.runner import EnvAdapter


def _env(tmp_path):
    return EnvAdapter("http://localhost:9", None, tmp_path / "outputs")


def test_clean_sandbox_removes_contents_keeps_dir(tmp_path):
    env = _env(tmp_path)
    sb = tmp_path / "outputs"
    (sb / "raw").mkdir(parents=True)
    (sb / "raw" / "a.md").write_text("x", encoding="utf-8")
    (sb / "escape.md").write_text("y", encoding="utf-8")
    assert env.clean_sandbox() == 2
    assert sb.is_dir() and list(sb.iterdir()) == []
    assert env.clean_sandbox() == 0  # 幂等


def test_clean_sandbox_missing_dir_is_noop(tmp_path):
    assert _env(tmp_path).clean_sandbox() == 0


def test_sandbox_snapshot_includes_parent_files_excluding_sandbox(tmp_path):
    """#9：快照带父目录文件清单（不含 outputs/ 自身），供 ../ 路径断言核越界目标。"""
    env = _env(tmp_path)
    sb = tmp_path / "outputs"
    (sb / "raw").mkdir(parents=True)
    (sb / "raw" / "page.md").write_text("in-sandbox", encoding="utf-8")
    (tmp_path / "escape.md").write_text("escaped!", encoding="utf-8")
    (tmp_path / "store.db").write_text("db", encoding="utf-8")

    snap = env.sandbox_snapshot()
    assert [f["path"] for f in snap["files"]] == ["raw/page.md"]
    parent_paths = {f["path"] for f in snap["parent_files"]}
    # 父目录文件在列，沙箱自身的内容绝不泄入（否则 outputs/ 内合法文件会被当越界产物）
    assert parent_paths == {"escape.md", "store.db"}
    hit = next(f for f in snap["parent_files"] if f["path"] == "escape.md")
    assert hit["size"] == len("escaped!")
