"""release_notes.py 公开命令行流程的离线回归测试。

只依赖 Python 3 标准库。每个测试使用独立的临时目录存放 SQLite 文件，
不读取或改写任何用户数据库；通过子进程执行命令，覆盖跨进程场景。
从项目根目录运行：

    python -m unittest discover -s tests
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "release_notes.py"

VERSION = "demo-0.1"
TITLE = " 离线示例 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# README 约定的 show 输出：标题两侧空格保留；重复条目分别出现；
# 多行条目只在首行加 "- " 前缀，内部换行原样保留；整段以换行结尾。
EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title:  离线示例 \n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 覆盖空字符串与仅由空白字符组成的两类无效变更。
BLANK_CHANGES = ["", "  \t\n "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def create_draft(db_path, version, title, changes):
    argv = ["create", version, "--title", title]
    for change in changes:
        argv += ["--change", change]
    return run_cli(db_path, *argv)


def show_draft(db_path, version):
    return run_cli(db_path, "show", version)


class ReleaseNotesFlowTest(unittest.TestCase):
    """create / show 公开流程的正常与失败保护场景。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "notes.sqlite"

    def create_demo(self):
        return create_draft(self.db_path, VERSION, TITLE, CHANGES)

    def assert_show_matches_expected(self, context):
        result = show_draft(self.db_path, VERSION)
        self.assertEqual(result.returncode, 0, f"{context}: 退出码不符: {result!r}")
        self.assertEqual(result.stderr, "", f"{context}: 标准错误应为空: {result!r}")
        self.assertEqual(
            result.stdout, EXPECTED_SHOW, f"{context}: 查看输出与原文不一致"
        )

    def test_create_then_show_across_processes(self):
        """正常样例：创建成功后，两个独立进程的查看结果完全一致。"""
        created = self.create_demo()
        self.assertEqual(created.returncode, 0, f"创建应成功: {created!r}")
        self.assertEqual(
            created.stdout,
            "Created demo-0.1\n",
            f"创建成功的标准输出不符: {created!r}",
        )
        self.assertEqual(created.stderr, "", f"创建成功的标准错误应为空: {created!r}")

        first = show_draft(self.db_path, VERSION)
        second = show_draft(self.db_path, VERSION)
        for label, result in (("第一次查看", first), ("第二次查看", second)):
            self.assertEqual(result.returncode, 0, f"{label}应成功: {result!r}")
            self.assertEqual(result.stderr, "", f"{label}的标准错误应为空: {result!r}")
        self.assertEqual(
            first.stdout,
            second.stdout,
            "两次独立进程的查看结果应完全一致",
        )
        self.assertEqual(
            first.stdout,
            EXPECTED_SHOW,
            "查看输出未按 README 格式精确匹配（标题空格、重复条目、多行前缀）",
        )

    def test_duplicate_create_fails_and_preserves_original(self):
        """同名版本再次创建应失败，且原有内容保持不变。"""
        created = self.create_demo()
        self.assertEqual(created.returncode, 0, f"前置创建应成功: {created!r}")

        duplicate = create_draft(
            self.db_path, VERSION, "重复版本的新标题", ["不应写入的变更"]
        )
        self.assertEqual(duplicate.returncode, 1, f"重复创建应退出 1: {duplicate!r}")
        self.assertEqual(
            duplicate.stdout, "", f"重复创建的标准输出应为空: {duplicate!r}"
        )
        self.assertEqual(
            duplicate.stderr,
            "Version already exists: demo-0.1\n",
            f"重复创建的标准错误不符: {duplicate!r}",
        )

        self.assert_show_matches_expected("重复创建失败后")

    def test_blank_change_rejected_on_existing_db(self):
        """已有数据库上，含空白变更的创建失败且不改变已有内容。"""
        created = self.create_demo()
        self.assertEqual(created.returncode, 0, f"前置创建应成功: {created!r}")

        for blank in BLANK_CHANGES:
            with self.subTest(blank=repr(blank)):
                result = create_draft(
                    self.db_path, "demo-0.2", "有效标题", ["有效条目", blank]
                )
                self.assertEqual(result.returncode, 1, f"应退出 1: {result!r}")
                self.assertEqual(result.stdout, "", f"标准输出应为空: {result!r}")
                self.assertEqual(
                    result.stderr,
                    "Invalid draft\n",
                    f"标准错误不符: {result!r}",
                )

        self.assert_show_matches_expected("空白变更失败后")

        missing = show_draft(self.db_path, "demo-0.2")
        self.assertEqual(missing.returncode, 1, f"demo-0.2 不应存在: {missing!r}")
        self.assertEqual(missing.stdout, "", f"标准输出应为空: {missing!r}")
        self.assertEqual(
            missing.stderr,
            "Version not found: demo-0.2\n",
            f"标准错误不符: {missing!r}",
        )

    def test_blank_change_rejected_on_fresh_db_path(self):
        """数据库尚不存在时，无效创建不得产生数据库文件。"""
        self.assertFalse(self.db_path.exists(), "前置条件：数据库文件不应存在")

        for blank in BLANK_CHANGES:
            with self.subTest(blank=repr(blank)):
                result = create_draft(
                    self.db_path, "demo-0.2", "有效标题", ["有效条目", blank]
                )
                self.assertEqual(result.returncode, 1, f"应退出 1: {result!r}")
                self.assertEqual(result.stdout, "", f"标准输出应为空: {result!r}")
                self.assertEqual(
                    result.stderr,
                    "Invalid draft\n",
                    f"标准错误不符: {result!r}",
                )
                self.assertFalse(
                    self.db_path.exists(),
                    "无效创建不得产生数据库文件",
                )

        missing = show_draft(self.db_path, "demo-0.2")
        self.assertEqual(missing.returncode, 1, f"demo-0.2 不应存在: {missing!r}")
        self.assertEqual(missing.stdout, "", f"标准输出应为空: {missing!r}")
        self.assertEqual(
            missing.stderr,
            "Version not found: demo-0.2\n",
            f"标准错误不符: {missing!r}",
        )
        self.assertFalse(
            self.db_path.exists(), "查看不存在的版本也不得产生数据库文件"
        )


if __name__ == "__main__":
    unittest.main()
