"""release_notes.py export-markdown 公开命令行的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功导出：退出码、标准输出/错误，固定 Markdown 格式逐字匹配
    （"# " + 版本名 + 两个换行；标题原文 + 两个换行；每条变更按输入
    顺序加 "- " 前缀及一个空格并各接一个换行，重复条目都出现，多行
    条目只在首行加前缀，末尾只有格式规定的换行）；
  - 原样输出：中文标点、Markdown 字符、首尾空格及版本名和标题内部
    的换行不转义、不裁剪，期望文本按格式规则独立写明；
  - 导出为只读操作：不新增数据库或发布说明文件，导出前后 show 的
    完整结果一致，两次独立进程导出逐字相同；
  - 无效版本名（空字符串、仅含空白）：退出码 1，标准错误恰为
    "Invalid draft" 加换行，即使数据库不存在也优先返回此结果，
    标准输出为空且不产生任何文件；
  - 有效名称但数据库文件或版本不存在（含仅改变大小写）：退出码 1，
    标准错误恰为 "Version not found: <版本名>" 加换行，标准输出
    为空且不产生任何文件。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

VERSION = "demo-0.1"
TITLE = " 预览说明 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# 固定导出格式，按规则独立写明：
#   "# " + 版本名原文 + 两个换行；
#   标题原文（含首尾空格）+ 两个换行；
#   每条变更按输入顺序加 "- " 前缀及一个空格，并各接一个换行；
#   两条重复变更都出现；多行条目只在首行加前缀，内部换行原样保留；
#   末尾只有最后一条变更格式规定的一个换行。
EXPECTED_EXPORT = (
    "# demo-0.1\n"
    "\n"
    " 预览说明 \n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 同一草稿的 show 输出，用于核对导出前后草稿内容不变。
EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title:  预览说明 \n"
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


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class ExportMarkdownTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-export-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按成功路径样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def tmpdir_entries(self):
        """临时目录当前内容（排序后），用于核对是否新增了任何文件。"""
        return sorted(os.listdir(self._tmpdir.name))


class TestExportMarkdownSuccess(ExportMarkdownTestCase):
    def test_export_output_exact_and_stable_across_processes(self):
        self.create_demo_01()

        # 创建进程结束后，两次独立进程导出结果应逐字相同且精确匹配格式。
        first = export_markdown(self.db, VERSION)
        second = export_markdown(self.db, VERSION)

        self.assertEqual(first, second, "两次独立进程导出的结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT,
            "导出应符合固定格式：版本名与标题原文后各两个换行，"
            "每条变更按输入顺序加 '- ' 前缀，重复条目都出现，"
            "多行条目只在首行加前缀，末尾只有格式规定的换行",
        )

    def test_export_is_read_only_and_show_is_unchanged(self):
        self.create_demo_01()

        # 导出前的 show 结果与目录内容作为基准。
        show_before = show(self.db, VERSION)
        self.assertEqual(show_before, (0, EXPECTED_SHOW, ""))
        entries_before = self.tmpdir_entries()
        self.assertEqual(entries_before, ["notes.sqlite"])

        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_EXPORT, ""))

        # 成功导出不新增数据库或发布说明文件，导出后 show 结果逐字不变。
        self.assertEqual(
            self.tmpdir_entries(),
            entries_before,
            "导出不得在临时目录新增数据库或发布说明文件",
        )
        show_after = show(self.db, VERSION)
        self.assertEqual(
            show_after,
            show_before,
            "导出前后 show 的完整结果应保持一致",
        )


class TestExportMarkdownVerbatim(ExportMarkdownTestCase):
    """中文标点、Markdown 字符、首尾空格及名称/标题内部换行原样输出。"""

    # 版本名与标题内部均含换行；标题与变更含中文标点、Markdown 字符
    # 与首尾空格。所有内容均为虚构软件的虚构文本。
    RAW_VERSION = "demo\n0.2 预览版"
    RAW_TITLE = "  标题：*星号* `代码` #井号\n第二行、结束。  "
    RAW_CHANGES = [
        "  **加粗**、`代码`、[链接](https://example.invalid)  ",
        "首行，含标点：\n次行——结束。",
    ]

    # 期望文本按固定格式规则独立写明，不从实际输出推导：
    # 版本名与标题原文（含内部换行与首尾空格）不转义、不裁剪。
    RAW_EXPECTED_EXPORT = (
        "# demo\n"
        "0.2 预览版\n"
        "\n"
        "  标题：*星号* `代码` #井号\n"
        "第二行、结束。  \n"
        "\n"
        "-   **加粗**、`代码`、[链接](https://example.invalid)  \n"
        "- 首行，含标点：\n"
        "次行——结束。\n"
    )

    def test_special_text_is_exported_verbatim(self):
        code, out, err = create(
            self.db, self.RAW_VERSION, self.RAW_TITLE, self.RAW_CHANGES
        )
        self.assertEqual((code, err), (0, ""))

        code, out, err = export_markdown(self.db, self.RAW_VERSION)
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            self.RAW_EXPECTED_EXPORT,
            "中文标点、Markdown 字符、首尾空格及版本名和标题内部的换行"
            "应原样输出，不转义或裁剪",
        )


class TestExportMarkdownInvalidVersion(ExportMarkdownTestCase):
    """空版本名与仅含空白的版本名一律报 Invalid draft。"""

    INVALID_VERSIONS = {
        "empty": "",
        "whitespace_only": "  \n\t ",
    }

    def assert_invalid_draft(self, db_path, version, case_label):
        code, out, err = export_markdown(db_path, version)
        self.assertEqual(code, 1, f"[{case_label}] 退出码应为 1")
        self.assertEqual(out, "", f"[{case_label}] 标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", f"[{case_label}] 标准错误不符"
        )

    def test_invalid_version_on_missing_database_creates_no_file(self):
        # 数据库不存在时也优先返回 Invalid draft，且不产生任何文件。
        for label, version in self.INVALID_VERSIONS.items():
            with self.subTest(case=label, database="missing"):
                db_path = os.path.join(
                    self._tmpdir.name, f"export-invalid-{label}.sqlite"
                )
                self.assert_invalid_draft(db_path, version, label)
                self.assertFalse(
                    os.path.exists(db_path),
                    f"[{label}] 不得在尚不存在的路径产生数据库文件",
                )

    def test_invalid_version_on_existing_database_leaves_it_intact(self):
        self.create_demo_01()
        entries_before = self.tmpdir_entries()

        for label, version in self.INVALID_VERSIONS.items():
            with self.subTest(case=label, database="existing"):
                self.assert_invalid_draft(self.db, version, label)

        # 失败导出不新增文件，已有草稿内容逐字不变。
        self.assertEqual(self.tmpdir_entries(), entries_before)
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW, ""))


class TestExportMarkdownNotFound(ExportMarkdownTestCase):
    """有效名称对应的文件或版本不存在时报 Version not found。"""

    def assert_version_not_found(self, db_path, version, case_label):
        code, out, err = export_markdown(db_path, version)
        self.assertEqual(code, 1, f"[{case_label}] 退出码应为 1")
        self.assertEqual(out, "", f"[{case_label}] 标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {version}\n",
            f"[{case_label}] 标准错误不符",
        )

    def test_missing_version_on_existing_database(self):
        self.create_demo_01()
        entries_before = self.tmpdir_entries()

        # 库内没有对应版本；仅改变已存版本名的大小写同样视为不存在。
        for version in ("demo-0.9", VERSION.upper()):
            with self.subTest(version=version):
                self.assert_version_not_found(self.db, version, version)

        # 失败导出不新增文件，已有草稿内容逐字不变。
        self.assertEqual(self.tmpdir_entries(), entries_before)
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW, ""))

    def test_missing_database_file_creates_no_file(self):
        fresh_db = os.path.join(self._tmpdir.name, "fresh.sqlite")
        self.assert_version_not_found(fresh_db, VERSION, "missing-file")
        self.assertFalse(
            os.path.exists(fresh_db),
            "数据库文件不存在时不得创建该文件",
        )
        self.assertEqual(
            self.tmpdir_entries(),
            [],
            "失败导出不得在临时目录产生任何文件",
        )


if __name__ == "__main__":
    unittest.main()
