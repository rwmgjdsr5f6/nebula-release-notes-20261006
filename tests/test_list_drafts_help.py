#!/usr/bin/env python3
"""release_notes.py list-drafts 帮助输出的离线回归测试。

只验证 list-drafts 子命令的帮助文案这一公开行为，全部通过
release_notes.py 的公开命令行在独立子进程中验收：不直接调用 argparse
的帮助格式化函数，不新增查询选项，不调整任何数据格式，也不改动
create / set-title / add-change / set-change / remove-change /
move-change / rename-version / export-markdown 等既有入口的输出与
错误处理。

覆盖范围：
  - 两种帮助形式：
      python release_notes.py --db notes.sqlite list-drafts --help
      python release_notes.py --db notes.sqlite list-drafts -h
    二者均退出码 0、标准错误为空，标准输出可按 UTF-8 严格解码且以
    换行结束，显示的帮助内容逐字节相同；
  - 公共语义标记：帮助显示 list-drafts 的用法与 --prefix 选项，并保留
    当前关于“只按版本名原文前缀匹配、区分大小写、不裁剪空白、省略
    选项则列出全部草稿”的说明；% 与 _ 作为普通字符出现，百分号只
    显示一个（源码中的 %% 经格式化后是单个 %），帮助文案中不得出现
    双百分号或 %(...) 之类的格式化占位符；
  - 缺值前缀加帮助：list-drafts --prefix --help 时，即使 --prefix 缺值，
    也显示与上面完全相同的帮助并退出 0、标准错误为空，不输出
    Invalid draft、目录 JSON 或异常堆栈；
  - 数据库尚不存在：临时目录始终为空，帮助调用不创建 notes.sqlite 或
    任何其他文件；
  - 已有草稿：固定为版本 v1、标题 预览版，变更依次为两条“重复文本”和
    一条“第一行\\n第二行”；帮助调用前后数据库文件字节完全一致，随后
    show 与 list-drafts 的输出与调用前逐字节相同。

断言只以这些公开语义和必要标记为准，不固定 argparse 的整段缩进、
折行宽度或选项分组标题（语义比对前会折叠空白，以容忍折行差异）。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 notes.sqlite
路径，自行准备并清理样例，不访问公网或用户数据库，可重复执行。
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 三种帮助调用形式：长选项、短选项，以及缺值的 --prefix 后接 --help。
LONG_HELP = ["--help"]
SHORT_HELP = ["-h"]
PREFIX_MISSING_VALUE_THEN_HELP = ["--prefix", "--help"]

# 已有草稿状态下的固定样例。
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
EXPECTED_LIST = '[{"version": "v1", "title": "预览版"}]\n'


def run_list_drafts(db_path, *sub_args):
    """在独立子进程中执行 `... list-drafts <sub_args>`，返回原始字节三元组。

    输出按字节捕获，由断言处用 UTF-8 严格解码：非法字节会直接导致
    断言失败，中文、标点与换行精确可比，不依赖运行环境的区域设置。
    """
    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", db_path, "list-drafts", *sub_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode, result.stdout, result.stderr


def run_create(db_path, version, title, changes):
    cmd = [
        sys.executable,
        SCRIPT,
        "--db",
        db_path,
        "create",
        version,
        "--title",
        title,
    ]
    for change in changes:
        cmd += ["--change", change]
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def run_show(db_path, version):
    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", db_path, "show", version],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode, result.stdout, result.stderr


def run_list_all(db_path):
    """不带 --prefix 的 list-drafts，用于核对帮助调用前后目录不变。"""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", db_path, "list-drafts"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode, result.stdout, result.stderr


class ListDraftsHelpTestCase(unittest.TestCase):
    """每个测试使用独立临时目录中的 notes.sqlite，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="release-notes-help-"
        )
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def help_text_for(self, *sub_args):
        """执行一种帮助调用并核对通用输出契约，返回解码后的帮助文本。"""
        code, out_bytes, err_bytes = run_list_drafts(self.db, *sub_args)
        return self.assert_clean_help(code, out_bytes, err_bytes)

    def assert_clean_help(self, code, out_bytes, err_bytes):
        """帮助调用的公共契约：退出 0、stderr 为空、stdout 严格 UTF-8 且
        以换行结束。返回解码后的文本供进一步语义断言。"""
        self.assertEqual(code, 0, "帮助调用退出码应为 0")
        self.assertEqual(err_bytes, b"", "帮助调用标准错误应为空")

        # 默认 errors='strict'：任何非法 UTF-8 字节都会在这里抛错。
        text = out_bytes.decode("utf-8")
        self.assertNotEqual(text, "", "帮助文本不应为空")
        self.assertTrue(
            text.endswith("\n"), "帮助文本应以换行结束"
        )
        return text

    def assert_public_help_semantics(self, text):
        """按公开语义与必要标记核对帮助内容。

        不固定 argparse 的缩进、折行宽度或分组标题：子命令名与选项名
        直接在原文查找；中文说明在折叠全部空白后比对，以容忍不同列宽
        造成的折行（中文文案本身不含空白，折叠后逐字符可比）。
        """
        # 必要标记：帮助展示的是 list-drafts 的用法，且包含 --prefix 选项。
        self.assertIn("list-drafts", text, "帮助应显示 list-drafts 子命令")
        self.assertIn("--prefix", text, "帮助应说明 --prefix 选项")

        collapsed = re.sub(r"\s+", "", text)
        semantic_fragments = [
            # 仅按版本名原文前缀匹配。
            "仅返回版本名原文以该文本开头的草稿",
            # 区分大小写。
            "区分大小写",
            # 不裁剪空白。
            "不裁剪空白",
            # % 与 _ 是普通字符。
            "%与_为普通字符",
            # 省略选项则列出全部草稿。
            "省略则列出全部草稿",
        ]
        for fragment in semantic_fragments:
            self.assertIn(
                fragment,
                collapsed,
                f"帮助应保留说明：{fragment}",
            )

        # 百分号在用户文案中只能出现一次：源码里的 %% 经 argparse
        # 百分号格式化后应呈现为单个 %，不得泄漏双百分号或 %(...) 占位符；
        # 下划线作为普通字符至少出现一次。
        self.assertEqual(
            text.count("%"), 1, "帮助中百分号应只显示为单个 %"
        )
        self.assertNotIn("%%", text, "帮助中不得出现双百分号 %%")
        self.assertNotIn(
            "%(", text, "帮助文案中不得残留 %(...) 格式化占位符"
        )
        self.assertIn("_", text, "帮助中应出现作为普通字符的下划线 _")

        # 帮助路径不得混入业务输出或错误处理产物。
        self.assertNotIn("Invalid draft", text, "帮助中不得输出 Invalid draft")
        self.assertNotIn("Traceback", text, "帮助中不得输出异常堆栈")
        self.assertNotIn("[]", text, "帮助中不得输出空目录 JSON")
        self.assertNotIn("[{", text, "帮助中不得输出目录 JSON")


class TestHelpWithoutDatabase(ListDraftsHelpTestCase):
    """数据库尚不存在：帮助调用完全离线、只读，不创建任何文件。"""

    def test_long_and_short_help_are_identical_and_create_nothing(self):
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "初始临时目录应为空"
        )

        long_text = self.help_text_for(*LONG_HELP)
        short_text = self.help_text_for(*SHORT_HELP)

        self.assertEqual(
            short_text, long_text, "--help 与 -h 显示的帮助内容应相同"
        )
        self.assert_public_help_semantics(long_text)

        self.assertFalse(
            os.path.exists(self.db), "帮助调用不得创建 notes.sqlite"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            [],
            "帮助调用后临时目录仍应为空，不得新增任何文件",
        )

    def test_prefix_with_missing_value_then_help_shows_same_help(self):
        # --prefix 缺值本属非法，但其后的 --help 应优先触发帮助：
        # 显示与正常帮助完全相同的内容，退出 0、stderr 为空，不落到
        # cmd_list_drafts 的 Invalid draft 分支，也不访问数据库。
        canonical = self.help_text_for(*LONG_HELP)
        text = self.help_text_for(*PREFIX_MISSING_VALUE_THEN_HELP)

        self.assertEqual(
            text,
            canonical,
            "--prefix --help 应显示与 --help 完全相同的帮助",
        )
        self.assert_public_help_semantics(text)
        self.assertNotIn("Invalid draft", text)
        self.assertNotIn("Traceback", text)

        self.assertFalse(
            os.path.exists(self.db), "帮助调用不得创建 notes.sqlite"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            [],
            "帮助调用后临时目录仍应为空，不得新增任何文件",
        )


class TestHelpWithExistingDraft(ListDraftsHelpTestCase):
    """已有一份固定草稿：帮助调用只读，数据库字节与业务输出保持不变。"""

    def setUp(self):
        super().setUp()

        created = run_create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(
            created.returncode, 0, "准备样例草稿时 create 应成功"
        )
        self.assertEqual(created.stderr, b"", "create 标准错误应为空")

        # 帮助调用前的只读基准：数据库原始字节、show 输出、目录输出。
        self.bytes_before = Path(self.db).read_bytes()
        code, out, err = run_show(self.db, VERSION)
        self.show_before = (code, out, err)
        code, out, err = run_list_all(self.db)
        self.list_before = (code, out, err)

        # 先确认样例与基准本身符合固定要求：两条重复文本与一条多行
        # 变更按顺序保存，目录只含 v1 这一条。
        self.assertEqual(
            self.show_before,
            (0, EXPECTED_SHOW.encode("utf-8"), b""),
            "帮助调用前的 show 基准不符",
        )
        self.assertEqual(
            self.list_before,
            (0, EXPECTED_LIST.encode("utf-8"), b""),
            "帮助调用前的 list-drafts 基准不符",
        )

    def test_three_help_forms_identical_and_leave_everything_unchanged(self):
        long_text = self.help_text_for(*LONG_HELP)
        short_text = self.help_text_for(*SHORT_HELP)
        prefix_text = self.help_text_for(*PREFIX_MISSING_VALUE_THEN_HELP)

        # 三种形式逐字节相同，且都满足公共语义。
        self.assertEqual(short_text, long_text, "-h 应与 --help 内容相同")
        self.assertEqual(
            prefix_text, long_text, "--prefix --help 应与 --help 内容相同"
        )
        for label, text in (
            ("--help", long_text),
            ("-h", short_text),
            ("--prefix --help", prefix_text),
        ):
            with self.subTest(form=label):
                self.assert_public_help_semantics(text)

        # 帮助调用后：数据库文件字节逐字节一致。
        self.assertEqual(
            Path(self.db).read_bytes(),
            self.bytes_before,
            "帮助调用前后数据库字节应完全一致",
        )

        # 随后 show 与 list-drafts 的输出与调用前完全相同。
        code, out, err = run_show(self.db, VERSION)
        self.assertEqual(
            (code, out, err),
            self.show_before,
            "帮助调用后 show 输出应与调用前完全相同",
        )
        code, out, err = run_list_all(self.db)
        self.assertEqual(
            (code, out, err),
            self.list_before,
            "帮助调用后 list-drafts 输出应与调用前完全相同",
        )


if __name__ == "__main__":
    unittest.main()
