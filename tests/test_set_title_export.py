"""release_notes.py set-title 之后再 export-markdown 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿上以含首尾空格、井号与恰好一个内部换行的
    新标题修订 demo-0.1；修订退出码 0、标准错误为空、标准输出恰为
    "Updated title: demo-0.1" 加换行；随后在新进程导出，结果按 README
    固定格式独立写明——新标题原文（含首尾空格、井号与内部换行）不裁剪、
    不转义，三条变更（两条重复文本与一条多行文本）的数量、原文与顺序
    原样保留，多行条目只在首行带 "- " 前缀；两次独立进程导出逐字相同
    （含末尾换行），且临时目录中不产生发布说明文件。
  - 失败路径一：新标题为空字符串，退出码 1、标准输出为空、标准错误恰为
    "Invalid draft" 加换行；失败后在新进程导出与事先独立写明的原始
    Markdown 完全一致，不出现新标题、不丢失条目。
  - 失败路径二：新标题仅含空格、换行与制表符，同样报 Invalid draft；
    导出仍保持初始标题与三条变更对应的固定 Markdown。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推；比较完整
UTF-8 文本而非片段，不只比较两次实际输出。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，结束后不遗留样例数据库，重复执行
结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 demo-0.1，初始标题首尾各一个空格；三条变更依次为两条
# 重复文本与一条由 " 第一行" 和 "第二行 " 之间含真实换行的多行文本，
# 首行前导空格与末行末尾空格均为原文的一部分。
VERSION = "demo-0.1"
TITLE = " 原始标题 "
CHANGES = ["重复条目", "重复条目", " 第一行\n第二行 "]

# 修订用新标题：" 修订#标题" 与 "第二行 " 之间恰好一个换行；首行前导
# 空格、井号与末行末尾空格均为输入的一部分。
NEW_TITLE = " 修订#标题\n第二行 "

# 修订前的原始导出文本（按 README 固定格式独立写明）：
#   "# " + 版本名原文 + 两个换行；标题原文 + 两个换行；
#   每条变更 "- " + 原文 + 一个换行，多行条目只在首行带前缀。
# 第三条变更自带一个首空格，故 "- " 前缀之后呈现两个空格；其第二行
# "第二行 " 不带前缀，末尾空格之后只接格式规定的一个换行。
EXPECTED_EXPORT_ORIGINAL = (
    "# demo-0.1\n"
    "\n"
    " 原始标题 \n"
    "\n"
    "- 重复条目\n"
    "- 重复条目\n"
    "-  第一行\n"
    "第二行 \n"
)

# 修订标题后的期望导出文本：标题整块替换为新标题原文——首行前导空格与
# 井号原样保留，内部换行把标题分成两行，末行 "第二行 " 的末尾空格之后
# 接格式规定的两个换行；三条变更的数量、原文与顺序逐字不变。
EXPECTED_EXPORT_RETITLED = (
    "# demo-0.1\n"
    "\n"
    " 修订#标题\n"
    "第二行 \n"
    "\n"
    "- 重复条目\n"
    "- 重复条目\n"
    "-  第一行\n"
    "第二行 \n"
)


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、井号、空格与内部换行精确
    可比。
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


def set_title(db_path, version, title):
    return run_cli(db_path, "set-title", version, "--title", title)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class SetTitleExportTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def assert_only_database_file(self):
        """导出只写标准输出：临时目录中除数据库外不得产生发布说明文件。"""
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["notes.sqlite"],
            "导出不得在临时目录中产生发布说明文件",
        )


class TestSetTitleThenExportSuccess(SetTitleExportTestCase):
    def test_set_title_then_export_matches_fixed_format(self):
        self.create_demo_01()

        # 即 python release_notes.py --db notes.sqlite set-title demo-0.1 \
        #   --title " 修订#标题\n第二行 "
        # 与创建使用同一个数据库路径；首尾空格、井号与内部换行是输入的一部分。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0, "set-title 退出码应为 0")
        self.assertEqual(err, "", "set-title 标准错误应为空")
        self.assertEqual(
            out,
            "Updated title: demo-0.1\n",
            "set-title 标准输出应恰为 Updated title: demo-0.1 加末尾换行",
        )

        # 两次相互独立的新进程导出，结果应逐字相同（含末尾换行）。
        first = export_markdown(self.db, VERSION)
        second = export_markdown(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程导出结果应逐字相同")

        code, out, err = first
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_RETITLED,
            "导出文本应精确匹配固定格式：新标题的首尾空格、井号与内部换行"
            "原样保留、不裁剪不转义，两条重复变更分别保留，第三条多行变更"
            "只在首行带前缀，条目数量与顺序不变",
        )

        # 导出只写标准输出，不产生发布说明文件。
        self.assert_only_database_file()


class TestFailedSetTitleLeavesExportUnchanged(SetTitleExportTestCase):
    def test_empty_new_title_fails_and_export_stays_original(self):
        self.create_demo_01()

        # 新标题为空字符串：优先报 Invalid draft，不写数据库。
        code, out, err = set_title(self.db, VERSION, "")
        self.assertEqual(code, 1, "空标题退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            "标准错误应恰为 Invalid draft 加末尾换行",
        )

        # 失败的修订不影响导出结果：在新进程导出，与事先独立写明的原始
        # Markdown 完全一致——初始标题、三条变更的原文、数量与顺序不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后导出退出码应为 0")
        self.assertEqual(err, "", "失败后导出标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_ORIGINAL,
            "空标题的失败修订不得改变导出结果",
        )

        self.assert_only_database_file()

    def test_blank_new_title_fails_and_export_stays_original(self):
        self.create_demo_01()

        # 新标题仅含空格、换行与制表符：同样优先报 Invalid draft。
        code, out, err = set_title(self.db, VERSION, "  \n\t ")
        self.assertEqual(code, 1, "纯空白标题退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            "标准错误应恰为 Invalid draft 加末尾换行",
        )

        # 失败的修订不影响导出结果：在新进程导出，与事先独立写明的原始
        # Markdown 完全一致——初始标题、三条变更的原文、数量与顺序不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后导出退出码应为 0")
        self.assertEqual(err, "", "失败后导出标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_ORIGINAL,
            "纯空白标题的失败修订不得改变导出结果",
        )

        self.assert_only_database_file()


if __name__ == "__main__":
    unittest.main()
