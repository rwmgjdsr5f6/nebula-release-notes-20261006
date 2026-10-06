"""release_notes.py export-markdown 公开命令行行为的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功导出：退出码 0、标准错误为空，标准输出恰为固定 Markdown 格式——
    "# " 加版本名原文和两个换行，标题原文和两个换行，再按 show 的展示
    顺序为每条变更原文加 "- " 前缀并各接一个换行；重复条目分别出现，
    多行条目只在首行加前缀，首尾空格原样保留，末尾只有格式规定的换行；
  - 中文标点、Markdown 字符、首尾空格、版本名与标题内部的换行均原样
    输出，不转义、不裁剪（期望文本按格式规则独立写明，不从实际输出推导）；
  - 导出为只读操作：不新增数据库之外的任何文件（含发布说明文件），
    导出前后 show 的完整结果逐字一致，两次独立进程导出逐字相同；
  - 空版本名、仅含空白的版本名一律退出码 1、空标准输出、标准错误恰为
    "Invalid draft" 加换行；即使数据库文件不存在也优先返回此结果，且不
    产生数据库或发布说明文件；
  - 有效名称但数据库文件不存在、或库内没有对应版本时，退出码 1、空
    标准输出、标准错误恰为 "Version not found: <版本名>" 加换行；仅
    改变已存版本名大小写同样按版本不存在处理；失败不产生文件、不改变
    已有草稿。

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

# 成功路径固定样例：版本 demo-0.1，标题首尾各一个空格，变更依次为两条
# 重复文本与一条包含内部换行的文本。全部为虚构软件样例。
VERSION = "demo-0.1"
TITLE = " 预览说明 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# show 期望文本（独立按 show 格式写明）：Version / Title / "- " 前缀，
# Title 后的标题自带首空格，故冒号后呈现两个空格；多行变更内部换行原样。
EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title:  预览说明 \n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# export-markdown 期望文本（按 README 固定格式逐段独立写明，而非由程序
# 输出推导）：
#   "# " + 版本名原文 + 两个换行；
#   标题原文 + 两个换行；
#   每条变更 "- " + 原文 + 一个换行，多行条目内部换行不带前缀。
# 末尾只保留最后一条变更自带的那一个换行。
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

# 小型原样性样例：版本名与标题都含内部换行；标题与变更首尾带空格；
# 覆盖中文标点（，：、（））与 Markdown 字符（# * ` [ ] ( ) _ | >）。
SMALL_VERSION = "示例-0.2\n第二行版本名"
SMALL_TITLE = " 预览，标题：#标记 *重点* `代码`\n第二行 "
SMALL_CHANGES = [" 变更（一）：[链接](url)、A_B | C > D\n第二行  "]

# 独立写明的期望 show 文本。
EXPECTED_SMALL_SHOW = (
    "Version: 示例-0.2\n"
    "第二行版本名\n"
    "Title:  预览，标题：#标记 *重点* `代码`\n"
    "第二行 \n"
    "-  变更（一）：[链接](url)、A_B | C > D\n"
    "第二行  \n"
)

# 独立写明的期望导出文本：版本名中的换行直接接在 "# 示例-0.2" 之后；
# 变更自带一个首空格，故 "- " 前缀之后呈现两个空格；末尾两个空格是
# 变更末行原文的一部分，其后只接格式规定的一个换行。
EXPECTED_SMALL_EXPORT = (
    "# 示例-0.2\n"
    "第二行版本名\n"
    "\n"
    " 预览，标题：#标记 *重点* `代码`\n"
    "第二行 \n"
    "\n"
    "-  变更（一）：[链接](url)、A_B | C > D\n"
    "第二行  \n"
)

# 空名称与若干仅含空白（空格、制表符、换行）的名称。
BLANK_NAMES = ["", "   ", " \n\t "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、标点与首尾空格精确可比。
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
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按成功路径固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestExportMarkdownSuccess(ExportMarkdownTestCase):
    def test_export_success_matches_fixed_format_exactly(self):
        self.create_demo_01()

        # 即 python release_notes.py --db notes.sqlite export-markdown demo-0.1
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT,
            "导出文本应精确匹配固定格式：标题首尾空格、两条重复变更顺序、"
            "多行条目仅首行带前缀，且末尾只有格式规定的换行",
        )

    def test_export_is_read_only_and_identical_across_processes(self):
        self.create_demo_01()

        # 导出前：记录目录内文件集合与 show 的完整结果。
        files_before = set(os.listdir(self._tmpdir.name))
        show_before = show(self.db, VERSION)
        self.assertEqual(show_before[0], 0)
        self.assertEqual(show_before[1], EXPECTED_SHOW)
        self.assertEqual(show_before[2], "")

        # 两次相互独立的进程导出，结果应逐字相同。
        first = export_markdown(self.db, VERSION)
        second = export_markdown(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程导出结果应逐字相同")
        self.assertEqual(first[0], 0)
        self.assertEqual(first[1], EXPECTED_EXPORT)
        self.assertEqual(first[2], "")

        # 导出后：show 的完整结果保持一致，目录内不新增任何文件，
        # 特别是不产生发布说明文件。
        show_after = show(self.db, VERSION)
        self.assertEqual(
            show_after,
            show_before,
            "导出前后 show 的完整结果应逐字一致",
        )
        files_after = set(os.listdir(self._tmpdir.name))
        self.assertEqual(
            files_after,
            files_before,
            "导出不得新增数据库附属文件或发布说明文件",
        )

    def test_verbatim_small_sample_punctuation_markdown_spaces_newlines(self):
        code, out, err = create(
            self.db, SMALL_VERSION, SMALL_TITLE, SMALL_CHANGES
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

        # 先以 show 验收草稿原样保存。
        code, out, err = show(self.db, SMALL_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            EXPECTED_SMALL_SHOW,
            "版本名/标题内部换行、首尾空格、中文标点与 Markdown 字符应原样保存",
        )

        # 再按独立写明的期望文本验收导出，不转义、不裁剪。
        code, out, err = export_markdown(self.db, SMALL_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            EXPECTED_SMALL_EXPORT,
            "中文标点、Markdown 字符、首尾空格、版本名与标题内部的换行"
            "均应原样输出",
        )


class TestExportMarkdownBlankName(ExportMarkdownTestCase):
    def assert_blank_name_rejected(self, db_path, name, case_label):
        code, out, err = export_markdown(db_path, name)
        self.assertEqual(code, 1, f"[{case_label}] 退出码应为 1")
        self.assertEqual(out, "", f"[{case_label}] 标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", f"[{case_label}] 标准错误应恰为 Invalid draft"
        )

    def test_blank_name_rejected_even_when_database_missing(self):
        # 数据库文件不存在时，空白名称仍优先报 Invalid draft，
        # 且不产生数据库或发布说明文件。
        for index, name in enumerate(BLANK_NAMES):
            with self.subTest(name=name):
                label = f"blank-{index}"
                db_path = os.path.join(self._tmpdir.name, f"{label}.sqlite")
                self.assert_blank_name_rejected(db_path, name, label)
                self.assertFalse(
                    os.path.exists(db_path),
                    f"[{label}] 不得在尚不存在的路径产生数据库文件",
                )
                self.assertEqual(
                    os.listdir(self._tmpdir.name),
                    [],
                    f"[{label}] 失败后目录内应无任何文件（含发布说明文件）",
                )

    def test_blank_name_rejected_on_existing_database_without_changes(self):
        self.create_demo_01()
        files_before = set(os.listdir(self._tmpdir.name))

        for index, name in enumerate(BLANK_NAMES):
            with self.subTest(name=name):
                label = f"blank-{index}"
                self.assert_blank_name_rejected(self.db, name, label)

                # 已有草稿不被失败操作改变。
                code, out, err = show(self.db, VERSION)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(
                    out, EXPECTED_SHOW, f"[{label}] 失败后已有草稿不应改变"
                )

        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            files_before,
            "失败导出不得新增任何文件",
        )


class TestExportMarkdownNotFound(ExportMarkdownTestCase):
    def test_valid_name_rejected_when_database_missing_without_creating_file(self):
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        code, out, err = export_markdown(db_path, "demo-0.9")
        self.assertEqual(code, 1, "数据库不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: demo-0.9\n",
            "标准错误应恰为 Version not found 加换行",
        )
        self.assertFalse(
            os.path.exists(db_path), "不得在尚不存在的路径产生数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            [],
            "失败后目录内应无任何文件（含发布说明文件）",
        )

    def test_valid_name_missing_in_existing_database(self):
        self.create_demo_01()
        files_before = set(os.listdir(self._tmpdir.name))

        code, out, err = export_markdown(self.db, "demo-0.9")
        self.assertEqual(code, 1, "版本不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: demo-0.9\n", "标准错误不符")

        # 不补建草稿，已有草稿不变，也不产生新文件。
        code, out, err = show(self.db, "demo-0.9")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "Version not found: demo-0.9\n")
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)
        self.assertEqual(set(os.listdir(self._tmpdir.name)), files_before)

    def test_lookup_is_case_sensitive(self):
        self.create_demo_01()

        # 仅改变已存版本名的大小写，按版本不存在处理。
        changed_case = VERSION.upper()
        code, out, err = export_markdown(self.db, changed_case)
        self.assertEqual(code, 1, "大小写不匹配时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {changed_case}\n",
            "标准错误应使用实际传入的版本名",
        )

        # 原始草稿不受影响，仍可按原名成功导出。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_EXPORT)


if __name__ == "__main__":
    unittest.main()
