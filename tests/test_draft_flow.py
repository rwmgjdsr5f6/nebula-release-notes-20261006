"""release_notes.py 公开命令行流程的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - create 成功：退出码、标准输出/错误，草稿内容原样持久化；
  - show 跨进程重复查看结果一致，输出与 README 格式精确匹配；
  - 重复版本创建失败且不破坏已有内容；
  - 空白变更创建失败：已有数据库不被修改、不存在的路径不产生文件；
  - 查看不存在的版本：退出码与标准错误。
  - set-title 成功：标题原样更新且幂等，版本名与变更的原文、数量、顺序不变；
  - set-title 无效输入优先报 Invalid draft 且不建文件；版本不存在报
    Version not found；其他版本与已有条目不被失败操作改变。
  - add-change 成功：新条目排在所有旧条目之后，重复文本分别保留；
    中文、标点、首尾空格与内部换行按一条变更原样保存；
  - add-change 无效输入（未提供 / 重复提供 --change、空白名称或文本）
    优先报 Invalid draft 且不建文件；版本不存在报 Version not found；
    失败操作不改变标题、旧条目与其他版本。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

VERSION = "demo-0.1"
TITLE = " 离线示例 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# README 所述 show 输出格式：Version / Title / 每条变更 "- " 前缀，
# 多行条目内部换行原样保留，整段输出以换行结尾。
EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title:  离线示例 \n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文与空格精确可比。
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


def create(db_path, version, title, changes):
    args = ["create", version, "--title", title]
    for change in changes:
        args += ["--change", change]
    return run_cli(db_path, *args)


def show(db_path, version):
    return run_cli(db_path, "show", version)


def set_title(db_path, version, title):
    return run_cli(db_path, "set-title", version, "--title", title)


def add_change(db_path, version, change):
    return run_cli(db_path, "add-change", version, "--change", change)


class DraftFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按正常样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestCreateAndShow(DraftFlowTestCase):
    def test_create_success_output(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Created demo-0.1\n")
        self.assertEqual(err, "")

    def test_show_output_exact_and_stable_across_processes(self):
        self.create_demo_01()

        # 创建进程结束后，两次独立进程查看结果应完全一致且精确匹配格式。
        first = show(self.db, VERSION)
        second = show(self.db, VERSION)

        self.assertEqual(first, second, "两次独立进程 show 的结果应完全一致")
        code, out, err = first
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW,
            "show 输出应精确保留标题两侧空格、重复条目顺序及多行条目内部换行",
        )


class TestDuplicateCreate(DraftFlowTestCase):
    def test_duplicate_version_fails_and_preserves_original(self):
        self.create_demo_01()

        # 使用有效的新标题和变更再次创建同名版本。
        code, out, err = create(self.db, VERSION, "另一个标题", ["另一条变更"])
        self.assertEqual(code, 1, "重复创建退出码应为 1")
        self.assertEqual(out, "", "重复创建标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: demo-0.1\n",
            "重复创建标准错误不符",
        )

        # 再次查看仍为原始内容。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW, "重复创建后原草稿内容不应改变")


class TestInvalidCreate(DraftFlowTestCase):
    INVALID_CHANGES = {
        "empty_string": "",
        "whitespace_only": "  \n\t ",
    }

    def assert_invalid_create(self, db_path, changes, case_label):
        code, out, err = create(db_path, "demo-0.2", "有效标题", changes)
        self.assertEqual(code, 1, f"[{case_label}] 无效创建退出码应为 1")
        self.assertEqual(out, "", f"[{case_label}] 无效创建标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", f"[{case_label}] 无效创建标准错误不符"
        )

    def test_invalid_change_rejected_on_existing_database(self):
        self.create_demo_01()

        for label, invalid in self.INVALID_CHANGES.items():
            with self.subTest(case=label, database="existing"):
                # 先传有效条目，再传无效条目。
                self.assert_invalid_create(
                    self.db, ["有效条目", invalid], label
                )

        # 已有数据库中的 demo-0.1 不应被任何失败创建改变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW, "无效创建不应改变已有草稿")

        # demo-0.2 不应留下任何内容。
        code, out, err = show(self.db, "demo-0.2")
        self.assertEqual(code, 1, "查看 demo-0.2 退出码应为 1")
        self.assertEqual(out, "", "查看 demo-0.2 标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: demo-0.2\n",
            "查看 demo-0.2 标准错误不符",
        )

    def test_invalid_change_rejected_on_fresh_path_without_creating_file(self):
        for label, invalid in self.INVALID_CHANGES.items():
            with self.subTest(case=label, database="fresh"):
                fresh_db = os.path.join(self._tmpdir.name, f"fresh-{label}.sqlite")
                self.assert_invalid_create(
                    fresh_db, ["有效条目", invalid], label
                )
                self.assertFalse(
                    os.path.exists(fresh_db),
                    f"[{label}] 无效创建不得在尚不存在的路径产生数据库文件",
                )

                # 该路径上查看 demo-0.2 应为版本不存在。
                code, out, err = show(fresh_db, "demo-0.2")
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, "Version not found: demo-0.2\n")


class TestSetTitle(DraftFlowTestCase):
    NEW_TITLE = " 修订，标题：\n第二行 "

    def expected_show_with(self, title):
        """与 EXPECTED_SHOW 同构，仅替换标题行。"""
        first_line, second_line = CHANGES[2].splitlines()
        return (
            f"Version: {VERSION}\n"
            f"Title: {title}\n"
            f"- {CHANGES[0]}\n"
            f"- {CHANGES[1]}\n"
            f"- {first_line}\n"
            f"{second_line}\n"
        )

    def test_set_title_success_output_and_persisted_content(self):
        self.create_demo_01()

        code, out, err = set_title(self.db, VERSION, self.NEW_TITLE)
        self.assertEqual(code, 0, "set-title 退出码应为 0")
        self.assertEqual(out, "Updated title: demo-0.1\n", "set-title 标准输出不符")
        self.assertEqual(err, "", "set-title 标准错误应为空")

        # 新进程查看：只有标题改变，版本名与两条变更原文、数量、顺序不变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            self.expected_show_with(self.NEW_TITLE),
            "show 应精确保留新标题的首尾空格、标点与内部换行，变更保持原样",
        )

    def test_same_title_is_idempotent(self):
        self.create_demo_01()

        first = set_title(self.db, VERSION, self.NEW_TITLE)
        second = set_title(self.db, VERSION, self.NEW_TITLE)
        self.assertEqual(first, (0, "Updated title: demo-0.1\n", ""))
        self.assertEqual(second, (0, "Updated title: demo-0.1\n", ""))

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.expected_show_with(self.NEW_TITLE))

    def test_set_title_is_case_sensitive_and_leaves_other_versions_intact(self):
        self.create_demo_01()

        # 大小写不同的版本名视为不存在，且不得改动原草稿。
        code, out, err = set_title(self.db, VERSION.upper(), "不应写入")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, f"Version not found: {VERSION.upper()}\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW, "大小写不匹配的修订不应改变原草稿")

    def test_invalid_input_reports_invalid_draft_even_when_version_missing(self):
        # 库文件尚不存在时，无效输入仍优先报 Invalid draft，且不产生文件。
        cases = {
            "missing_title": ["set-title", "missing-1"],
            "empty_title": ["set-title", VERSION, "--title", ""],
            "whitespace_title": ["set-title", VERSION, "--title", "  \n\t "],
            "empty_version": ["set-title", "", "--title", "有效标题"],
            "whitespace_version": ["set-title", "   ", "--title", "有效标题"],
        }
        for label, cli_args in cases.items():
            with self.subTest(case=label):
                db_path = os.path.join(self._tmpdir.name, f"invalid-{label}.sqlite")
                code, out, err = run_cli(db_path, *cli_args)
                self.assertEqual(code, 1, f"[{label}] 退出码应为 1")
                self.assertEqual(out, "", f"[{label}] 标准输出应为空")
                self.assertEqual(
                    err, "Invalid draft\n", f"[{label}] 标准错误不符"
                )
                self.assertFalse(
                    os.path.exists(db_path),
                    f"[{label}] 不得在尚不存在的路径产生数据库文件",
                )

    def test_invalid_input_does_not_modify_existing_database(self):
        self.create_demo_01()

        for title in (None, "", "  \n\t "):
            with self.subTest(title=title):
                if title is None:
                    code, out, err = run_cli(self.db, "set-title", VERSION)
                else:
                    code, out, err = set_title(self.db, VERSION, title)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, "Invalid draft\n")

        # 所有失败后原草稿（含原标题）保持不变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)

    def test_valid_input_version_missing_on_existing_database(self):
        self.create_demo_01()

        code, out, err = set_title(self.db, "demo-0.9", "新标题")
        self.assertEqual(code, 1, "版本不存在退出码应为 1")
        self.assertEqual(out, "")
        self.assertEqual(err, "Version not found: demo-0.9\n")

        # 原草稿不变，且不补建缺失版本。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)
        code, out, err = show(self.db, "demo-0.9")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "Version not found: demo-0.9\n")


class TestAddChange(DraftFlowTestCase):
    NEW_CHANGE = "修正提示"

    def expected_show_after_changes(self, *extra):
        """与 EXPECTED_SHOW 同构，在原有变更之后按顺序拼接追加条目。"""
        first_line, second_line = CHANGES[2].splitlines()
        parts = [
            f"Version: {VERSION}\n",
            f"Title: {TITLE}\n",
            f"- {CHANGES[0]}\n",
            f"- {CHANGES[1]}\n",
            f"- {first_line}\n",
            f"{second_line}\n",
        ]
        for content in extra:
            parts.append(f"- {content}\n")
        return "".join(parts)

    def test_add_change_success_output_and_persisted_order(self):
        self.create_demo_01()

        code, out, err = add_change(self.db, VERSION, self.NEW_CHANGE)
        self.assertEqual(code, 0, "add-change 退出码应为 0")
        self.assertEqual(out, "Added change: demo-0.1\n", "add-change 标准输出不符")
        self.assertEqual(err, "", "add-change 标准错误应为空")

        # 新进程查看：标题与旧条目原文、数量、顺序不变，新条目排在最后。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            self.expected_show_after_changes(self.NEW_CHANGE),
            "追加条目应位于所有旧条目之后，其余内容保持原样",
        )

    def test_duplicate_text_is_kept_as_separate_entry(self):
        self.create_demo_01()

        first = add_change(self.db, VERSION, self.NEW_CHANGE)
        second = add_change(self.db, VERSION, self.NEW_CHANGE)
        self.assertEqual(first, (0, "Added change: demo-0.1\n", ""))
        self.assertEqual(second, (0, "Added change: demo-0.1\n", ""))

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            self.expected_show_after_changes(self.NEW_CHANGE, self.NEW_CHANGE),
            "重复文本应分别保留为两条，依次排在最后",
        )

    def test_appended_text_keeps_spaces_punctuation_and_inner_newlines(self):
        self.create_demo_01()
        text = "  追加，首行：\n第二行  "

        code, out, err = add_change(self.db, VERSION, text)
        self.assertEqual((code, out, err), (0, "Added change: demo-0.1\n", ""))

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        # 多行文本只算一条：只在首行前加 "- "，内部换行与首尾空格原样保留。
        self.assertEqual(
            out,
            self.expected_show_after_changes(text),
            "追加文本的中文、标点、首尾空格与内部换行应原样保留",
        )

    def test_other_versions_and_failed_target_remain_intact(self):
        self.create_demo_01()

        # 另一个版本的草稿与条目不受追加影响。
        code, out, err = create(self.db, "other-2.0", "其他标题", ["其他变更"])
        self.assertEqual(code, 0)

        code, out, err = add_change(self.db, VERSION, "只属于 demo-0.1")
        self.assertEqual(code, 0)

        code, out, err = show(self.db, "other-2.0")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: other-2.0\nTitle: 其他标题\n- 其他变更\n",
            "追加不得影响其他版本",
        )

    def test_invalid_input_reports_invalid_draft_even_when_version_missing(self):
        # 库文件尚不存在时，无效输入仍优先报 Invalid draft，且不产生文件。
        cases = {
            "missing_change": ["add-change", VERSION],
            "repeated_change": ["add-change", VERSION, "--change", "a", "--change", "b"],
            "empty_change": ["add-change", VERSION, "--change", ""],
            "whitespace_change": ["add-change", VERSION, "--change", "  \n\t "],
            "empty_version": ["add-change", "", "--change", "有效文本"],
            "whitespace_version": ["add-change", "  ", "--change", "有效文本"],
        }
        for label, cli_args in cases.items():
            with self.subTest(case=label):
                db_path = os.path.join(self._tmpdir.name, f"invalid-{label}.sqlite")
                code, out, err = run_cli(db_path, *cli_args)
                self.assertEqual(code, 1, f"[{label}] 退出码应为 1")
                self.assertEqual(out, "", f"[{label}] 标准输出应为空")
                self.assertEqual(err, "Invalid draft\n", f"[{label}] 标准错误不符")
                self.assertFalse(
                    os.path.exists(db_path),
                    f"[{label}] 不得在尚不存在的路径产生数据库文件",
                )

    def test_invalid_input_does_not_modify_existing_database(self):
        self.create_demo_01()

        bad_invocations = [
            ["add-change", VERSION],
            ["add-change", VERSION, "--change", "a", "--change", "b"],
            ["add-change", VERSION, "--change", ""],
            ["add-change", VERSION, "--change", "  \n\t "],
            ["add-change", "missing-version", "--change", ""],
        ]
        for cli_args in bad_invocations:
            with self.subTest(args=cli_args):
                code, out, err = run_cli(self.db, *cli_args)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, "Invalid draft\n")

        # 所有失败后原草稿（标题与条目）保持不变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)

    def test_valid_input_version_or_file_missing(self):
        self.create_demo_01()

        # 库内没有对应版本：不补建草稿，原内容不变；大小写不匹配同样视为不存在。
        for missing_version in ("demo-0.9", VERSION.upper()):
            with self.subTest(version=missing_version):
                code, out, err = add_change(self.db, missing_version, "不应写入")
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, f"Version not found: {missing_version}\n")

                code, out, err = show(self.db, missing_version)
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(err, f"Version not found: {missing_version}\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)

        # 数据库文件不存在：报错且不创建文件。
        fresh_db = os.path.join(self._tmpdir.name, "fresh.sqlite")
        code, out, err = add_change(fresh_db, VERSION, "不应写入")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, f"Version not found: {VERSION}\n")
        self.assertFalse(os.path.exists(fresh_db), "不得在尚不存在的路径产生数据库文件")


if __name__ == "__main__":
    unittest.main()
