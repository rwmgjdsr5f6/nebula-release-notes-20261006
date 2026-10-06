"""release_notes.py set-change 之后再 export-markdown 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿上以带前导零的序号 02 替换第二条变更，
    新文本含首尾空格、中文标点与恰好一个内部换行；替换退出码 0、
    标准错误为空、标准输出恰为 "Updated change: demo-0.1 #2" 加换行；
    随后导出结果按 README 固定格式独立写明——原标题首尾空格、第一条
    重复文本、第三条多行文本与条目顺序原样保留，仅第二条出现新内容；
    多行条目只在首行带 "- " 前缀，正文不裁剪、不转义；两次独立进程
    导出逐字相同（含末尾换行）。
  - 失败路径一：序号 04 越界，退出码 1、标准输出为空、标准错误恰为
    "Change not found: demo-0.1 #4" 加换行；失败后导出与事先独立写明
    的原始 Markdown 完全一致。
  - 失败路径二：序号 2 提交仅含空白的新文本，退出码 1、标准输出为空、
    标准错误恰为 "Invalid draft" 加换行；导出仍保持原样。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推；比较完整
UTF-8 文本而非片段。

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

# 固定样例：版本 demo-0.1，标题首尾各一个空格，三条变更依次为两条重复
# 文本与一条 "第一行" 和 "第二行" 之间含真实换行的多行文本。
VERSION = "demo-0.1"
TITLE = " 预览说明 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# 替换用新文本：首尾各一个空格，"修订，#预览" 与 "保留标点。" 两行之间
# 恰好一个换行。
NEW_CHANGE = " 修订，#预览\n保留标点。 "

# 替换前的原始导出文本（按 README 固定格式独立写明）：
#   "# " + 版本名原文 + 两个换行；标题原文 + 两个换行；
#   每条变更 "- " + 原文 + 一个换行，多行条目只在首行带前缀。
EXPECTED_EXPORT_ORIGINAL = (
    "# demo-0.1\n"
    "\n"
    " 预览说明 \n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 替换第二条后的期望导出文本：仅第二条变为新内容——新文本自带一个首
# 空格，故 "- " 前缀之后呈现两个空格；第二行 "保留标点。 " 不带前缀，
# 末尾空格是原文的一部分，其后只接格式规定的一个换行。其余条目、标题
# 首尾空格与条目顺序逐字不变。
EXPECTED_EXPORT_REPLACED = (
    "# demo-0.1\n"
    "\n"
    " 预览说明 \n"
    "\n"
    "- 新增预览\n"
    "-  修订，#预览\n"
    "保留标点。 \n"
    "- 第一行\n"
    "第二行\n"
)


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


def set_change(db_path, version, index, change):
    return run_cli(
        db_path, "set-change", version, "--index", str(index),
        "--change", change,
    )


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class SetChangeExportTestCase(unittest.TestCase):
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


class TestSetChangeThenExportSuccess(SetChangeExportTestCase):
    def test_replace_second_entry_then_export_matches_fixed_format(self):
        self.create_demo_01()

        # 即 python release_notes.py --db notes.sqlite set-change demo-0.1 \
        #   --index 02 --change " 修订，#预览\n保留标点。 "
        # 前导零不影响定位，输出序号不保留前导零。
        code, out, err = set_change(self.db, VERSION, "02", NEW_CHANGE)
        self.assertEqual(code, 0, "set-change 退出码应为 0")
        self.assertEqual(err, "", "set-change 标准错误应为空")
        self.assertEqual(
            out,
            "Updated change: demo-0.1 #2\n",
            "set-change 标准输出应恰为 Updated change: demo-0.1 #2 加末尾换行",
        )

        # 两次相互独立的进程导出，结果应逐字相同（含末尾换行）。
        first = export_markdown(self.db, VERSION)
        second = export_markdown(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程导出结果应逐字相同")

        code, out, err = first
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_REPLACED,
            "导出文本应精确匹配固定格式：仅第二条被替换，新文本的首尾空格、"
            "标点与内部换行原样保留，多行条目只在首行带前缀，标题空格、"
            "重复条目与第三条多行文本及条目顺序不变",
        )


class TestFailedSetChangeLeavesExportUnchanged(SetChangeExportTestCase):
    def test_out_of_range_index_fails_and_export_stays_original(self):
        self.create_demo_01()

        # 序号 04 超出三条变更的范围：前导零不影响定位，错误消息不保留前导零。
        code, out, err = set_change(self.db, VERSION, "04", "不应写入")
        self.assertEqual(code, 1, "序号越界时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #4\n",
            "标准错误应恰为 Change not found: demo-0.1 #4 加末尾换行",
        )

        # 失败的替换不影响导出结果：与事先独立写明的原始 Markdown 完全一致。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后导出退出码应为 0")
        self.assertEqual(err, "", "失败后导出标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_ORIGINAL,
            "序号越界的失败替换不得改变导出结果",
        )

    def test_blank_new_text_fails_and_export_stays_original(self):
        self.create_demo_01()

        # 序号 2 本身有效，但新文本仅含空白：优先报 Invalid draft。
        code, out, err = set_change(self.db, VERSION, "2", "  \n\t ")
        self.assertEqual(code, 1, "空白新文本退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            "标准错误应恰为 Invalid draft 加末尾换行",
        )

        # 失败的替换不影响导出结果：与事先独立写明的原始 Markdown 完全一致。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后导出退出码应为 0")
        self.assertEqual(err, "", "失败后导出标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_ORIGINAL,
            "空白新文本的失败替换不得改变导出结果",
        )


if __name__ == "__main__":
    unittest.main()
