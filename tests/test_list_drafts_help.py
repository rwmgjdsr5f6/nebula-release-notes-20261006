"""release_notes.py list-drafts 子命令帮助输出的离线回归测试。

只验证 list-drafts 的帮助展示这一行为，全部通过 release_notes.py 的公开
命令行在独立子进程中验收（不直接调用 argparse 的帮助格式化函数），期望
均来自公开语义与固定输入，不访问用户数据库，不依赖内部表结构。

覆盖范围：
  - 两种帮助形式：list-drafts --help 与 list-drafts -h 均退出码 0、
    标准错误为空，标准输出可按 UTF-8 严格解码且以换行结束，两种形式
    显示的帮助内容完全一致；
  - 帮助内容：显示 list-drafts 的用法与 --prefix 选项，并保留公开说明
    （只按版本名前缀匹配、区分大小写、不裁剪空白、省略选项列出全部草稿）；
    % 与 _ 作为普通字符出现在说明中，百分号只显示一个，不把双百分号或
    格式化占位符当成用户文案输出。断言只针对这些公开语义与必要标记，
    不固定 argparse 的整段缩进、折行宽度或选项分组标题；
  - 缺值前缀后求助：list-drafts --prefix --help（--prefix 缺值）同样
    显示帮助并退出码 0、标准错误为空，不输出 Invalid draft、目录 JSON
    或异常堆栈；
  - 数据库不存在：帮助调用不创建数据库或任何其他文件，临时目录保持为空；
  - 已有草稿：帮助调用前后数据库文件字节完全一致，随后 show 与
    list-drafts 的输出与调用前完全相同。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
可与既有测试一起被 unittest 发现入口收集并重复执行。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 严格解码：非法字节会直接抛错，中文、空格
    与换行精确可比，不依赖运行环境的区域设置。
    """
    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", db_path, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8"),
        result.stderr.decode("utf-8"),
    )


def list_drafts_help(db_path, *extra_args):
    """以 list-drafts 子命令调用帮助，extra_args 为 --help / -h 等形式。"""
    return run_cli(db_path, "list-drafts", *extra_args)


def show(db_path, version):
    return run_cli(db_path, "show", version)


def list_drafts(db_path, *extra_args):
    return run_cli(db_path, "list-drafts", *extra_args)


class ListDraftsHelpTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="release-notes-list-drafts-help-"
        )
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def assert_help_contract(self, result, form):
        """核对帮助调用的公共契约，返回标准输出文本。

        退出码 0、标准错误为空；标准输出非空、以换行结束（解码已在
        run_cli 中按 UTF-8 严格完成，非法字节会在此之前抛错）。
        """
        code, out, err = result
        self.assertEqual(code, 0, f"{form} 退出码应为 0")
        self.assertEqual(err, "", f"{form} 标准错误应为空")
        self.assertTrue(out, f"{form} 标准输出不应为空")
        self.assertTrue(
            out.endswith("\n"), f"{form} 标准输出应以换行结束"
        )
        return out

    def assert_help_public_markers(self, out, form):
        """核对帮助内容的公开语义标记，不固定缩进、折行或分组标题。

        argparse 可能按终端宽度折行，中文说明可能在任意字符间断行，
        因此先移除全部空白字符再匹配中文标记；英文必要标记（子命令名、
        选项名）不含空白，直接在原文中检查。
        """
        collapsed = re.sub(r"\s+", "", out)

        # 用法与选项：帮助应显示 list-drafts 的用法与 --prefix 选项。
        self.assertIn("list-drafts", out, f"{form} 应显示 list-drafts 用法")
        self.assertIn("--prefix", out, f"{form} 应显示 --prefix 选项")

        # 公开说明：只按版本名前缀匹配、区分大小写、不裁剪空白、
        # 省略选项则列出全部草稿。
        for marker in [
            "可选版本名前缀",
            "版本名原文以该文本开头",
            "区分大小写",
            "不裁剪空白",
            "省略则列出全部草稿",
        ]:
            self.assertIn(
                marker, collapsed, f"{form} 的帮助说明应保留 {marker!r}"
            )

        # % 与 _ 作为普通字符出现在说明中：百分号只显示一个，不出现
        # 双百分号或 %s / %(...) 之类的格式化占位符。
        self.assertIn("_", out, f"{form} 的说明中应保留下划线")
        self.assertIn("%与_", collapsed, f"{form} 的说明中 % 与 _ 应并列出现")
        self.assertEqual(
            out.count("%"), 1, f"{form} 的说明中百分号应只显示一个"
        )
        self.assertNotIn("%%", out, f"{form} 不应输出双百分号")
        self.assertNotIn("%s", out, f"{form} 不应输出格式化占位符 %s")
        self.assertNotIn("%(", out, f"{form} 不应输出格式化占位符 %(...)")


class TestHelpFormsWithoutDatabase(ListDraftsHelpTestCase):
    """数据库尚不存在：--help 与 -h 内容一致，且不创建任何文件。"""

    def test_help_and_short_form_identical_and_create_nothing(self):
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        long_out = self.assert_help_contract(
            list_drafts_help(self.db, "--help"), "--help"
        )
        short_out = self.assert_help_contract(
            list_drafts_help(self.db, "-h"), "-h"
        )
        self.assertEqual(
            long_out, short_out, "--help 与 -h 显示的帮助内容应相同"
        )
        self.assert_help_public_markers(long_out, "--help")

        # 帮助调用不创建数据库或任何其他文件，临时目录保持为空。
        self.assertFalse(
            os.path.exists(self.db), "帮助调用不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            [],
            "帮助调用不得新增任何文件",
        )


class TestHelpAfterPrefixWithoutValue(ListDraftsHelpTestCase):
    """--prefix 缺值后接 --help：仍显示同样帮助，不按非法前缀处理。"""

    def test_prefix_without_value_then_help_shows_same_help(self):
        canonical = self.assert_help_contract(
            list_drafts_help(self.db, "--help"), "--help"
        )

        code, out, err = run_cli(
            self.db, "list-drafts", "--prefix", "--help"
        )
        self.assertEqual(code, 0, "--prefix --help 退出码应为 0")
        self.assertEqual(err, "", "--prefix --help 标准错误应为空")
        self.assertEqual(
            out,
            canonical,
            "--prefix 缺值后接 --help 应显示与 --help 相同的帮助",
        )

        # 不输出 Invalid draft、目录 JSON 或异常堆栈。
        combined = out + err
        self.assertNotIn(
            "Invalid draft", combined, "帮助调用不得输出 Invalid draft"
        )
        self.assertNotIn(
            "Traceback", combined, "帮助调用不得输出异常堆栈"
        )
        self.assertNotIn(
            '"version"', out, "帮助调用不得输出目录 JSON"
        )
        self.assertNotIn(
            "[{", out, "帮助调用不得输出目录 JSON 数组"
        )

        # 数据库不存在的场景下同样不创建任何文件。
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            [],
            "帮助调用不得新增任何文件",
        )


class TestHelpWithExistingDrafts(ListDraftsHelpTestCase):
    """已有草稿：帮助调用前后数据库字节一致，show 与目录输出不变。"""

    VERSION = "v1"
    TITLE = "预览版"
    CHANGES = ["重复文本", "重复文本", "第一行\n第二行"]
    EXPECTED_SHOW = (
        "Version: v1\n"
        "Title: 预览版\n"
        "- 重复文本\n"
        "- 重复文本\n"
        "- 第一行\n"
        "第二行\n"
    )
    EXPECTED_LIST = (
        json.dumps(
            [{"version": "v1", "title": "预览版"}], ensure_ascii=False
        )
        + "\n"
    )

    def test_help_leaves_database_and_outputs_unchanged(self):
        # 固定样例：版本 v1、标题 预览版，变更依次为两条 重复文本 和一条
        # 由 第一行、一个换行、第二行 组成的多行文本。
        code, out, err = run_cli(
            self.db,
            "create",
            self.VERSION,
            "--title",
            self.TITLE,
            "--change",
            "重复文本",
            "--change",
            "重复文本",
            "--change",
            "第一行\n第二行",
        )
        self.assertEqual((code, out, err), (0, "Created v1\n", ""))

        # 调用前基准：数据库文件字节、show 与 list-drafts 输出。
        with open(self.db, "rb") as db_file:
            bytes_before = db_file.read()
        before_show = show(self.db, self.VERSION)
        self.assertEqual(
            before_show, (0, self.EXPECTED_SHOW, ""), "调用前 show 基准不符"
        )
        before_list = list_drafts(self.db)
        self.assertEqual(
            before_list, (0, self.EXPECTED_LIST, ""), "调用前目录基准不符"
        )

        # 三种帮助形式各调用一次，均符合帮助契约且内容一致。
        canonical = self.assert_help_contract(
            list_drafts_help(self.db, "--help"), "--help"
        )
        self.assert_help_public_markers(canonical, "--help")
        for form, extra_args in [
            ("-h", ["-h"]),
            ("--prefix --help", ["--prefix", "--help"]),
        ]:
            out = self.assert_help_contract(
                list_drafts_help(self.db, *extra_args), form
            )
            self.assertEqual(
                out, canonical, f"{form} 显示的帮助内容应与 --help 相同"
            )

        # 帮助调用后：数据库文件字节完全一致，show 与 list-drafts 的输出
        # 与调用前完全相同，目录中不新增任何文件。
        with open(self.db, "rb") as db_file:
            bytes_after = db_file.read()
        self.assertEqual(
            bytes_after, bytes_before, "帮助调用后数据库字节应保持一致"
        )
        self.assertEqual(
            show(self.db, self.VERSION),
            before_show,
            "帮助调用后 show 输出应与调用前完全相同",
        )
        self.assertEqual(
            list_drafts(self.db),
            before_list,
            "帮助调用后 list-drafts 输出应与调用前完全相同",
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            ["notes.sqlite"],
            "帮助调用不得新增任何文件",
        )


if __name__ == "__main__":
    unittest.main()
