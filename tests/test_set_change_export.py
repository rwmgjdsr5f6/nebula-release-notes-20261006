"""release_notes.py “替换单条变更后导出 Markdown”流程的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功流程：在固定样例 demo-0.1 上以带前导零的序号 02 替换第二条变更，
    set-change 退出码 0、标准错误为空、标准输出恰为
    "Updated change: demo-0.1 #2" 加末尾换行；随后 export-markdown
    退出码 0、标准错误为空，完整标准输出与按 README 固定格式独立写明的
    预期逐字一致——保留原标题首尾空格、第一条重复文本、第三条多行文本
    与条目顺序，仅第二条出现首尾带空格、含中文标点与 Markdown 字符的
    两行新内容；多行条目只在首行带 "- " 前缀（前缀后一个空格），正文
    不裁剪、不转义；两次独立进程导出逐字相同（末尾换行也参与比较）；
  - 失败流程一：序号 04 超过三条变更的数量，退出码 1、标准输出为空、
    标准错误恰为 "Change not found: demo-0.1 #4" 加末尾换行，失败后
    导出与事先独立写明的原始 Markdown 完全一致；
  - 失败流程二：序号 2 提交仅含空白的新文本，退出码 1、标准输出为空、
    标准错误恰为 "Invalid draft" 加末尾换行，失败后导出仍与原始
    Markdown 完全一致。

预期文本全部按 README 规定的固定格式独立写明，绝不从程序实际输出反推；
比较的是完整 UTF-8 文本而非片段。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，不产生发布说明文件，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 demo-0.1；标题“预览说明”首尾各一个空格；三条变更依次为
# 两条重复文本与一条在“第一行”“第二行”之间含真实换行的多行文本。
VERSION = "demo-0.1"
TITLE = " 预览说明 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# 成功替换样例：新文本首尾各一个空格，两行内容之间只有一个换行，
# 含中文逗号、句号与 Markdown 字符 #。
REPLACE_INDEX = "02"
REPLACEMENT = " 修订，#预览\n保留标点。 "

# 越界失败样例：序号 04（超过三条），提交的是有效（非空白）新文本。
BEYOND_INDEX = "04"
BEYOND_TEXT = "不应写入的第四条"

# 空白文本失败样例：序号 2 合法，但新文本仅由空白组成。
BLANK_TEXT_INDEX = "2"
BLANK_TEXT = "   "

# 替换前的原始导出文本：严格按 README 固定格式独立写明——
#   "# " + 版本名原文 + 两个换行；
#   标题原文 + 两个换行；
#   每条变更 "- " + 原文 + 一个换行，多行条目内部换行不带前缀。
# 标题自带首空格，故标题行呈现一个前导空格；末尾换行是最后一条变更
# 格式规定的那一个换行。
EXPECTED_ORIGINAL_EXPORT = (
    "# demo-0.1\n"
    "\n"
    " 预览说明 \n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 替换第二条后的导出文本：同样按固定格式独立写明，而非由程序输出推导。
# 仅第二条变化：新文本自带一个首空格，接在 "- " 前缀后呈现两个空格；
# 第二行 "保留标点。 " 不带前缀，末尾空格是正文的一部分，其后只接
# 格式规定的一个换行。标题首尾空格、第一条重复文本、第三条多行文本
# 与条目顺序均保持原样。
EXPECTED_REPLACED_EXPORT = (
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


def create_demo_draft(db_path):
    """通过已有 create 命令创建固定样例（重复 --change 按顺序保存）。"""
    args = ["create", VERSION, "--title", TITLE]
    for change in CHANGES:
        args += ["--change", change]
    return run_cli(db_path, *args)


def set_change(db_path, index, change):
    return run_cli(
        db_path, "set-change", VERSION, "--index", index, "--change", change
    )


def export_markdown(db_path):
    return run_cli(db_path, "export-markdown", VERSION)


class SetChangeExportTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录与数据库，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_fixed_sample(self):
        """创建固定样例并断言 create 命令本身的公开输出符合 README。"""
        code, out, err = create_demo_draft(self.db)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestSetChangeThenExportSuccess(SetChangeExportTestCase):
    def test_replace_then_export_matches_independently_written_markdown(self):
        self.create_fixed_sample()

        # 以带前导零的序号 02 替换第二条；新文本首尾各一个空格、两行
        # 之间只有一个换行。
        code, out, err = set_change(self.db, REPLACE_INDEX, REPLACEMENT)
        self.assertEqual(code, 0, "set-change 替换应退出 0")
        self.assertEqual(err, "", "成功替换时标准错误应为空")
        self.assertEqual(
            out,
            "Updated change: demo-0.1 #2\n",
            "标准输出应恰为 Updated change 消息（序号不保留前导零）加末尾换行",
        )

        # 替换后的导出：与独立写明的完整预期逐字比较（含末尾换行），
        # 而非仅检查新文本片段是否出现。
        code, out, err = export_markdown(self.db)
        self.assertEqual(code, 0, "export-markdown 应退出 0")
        self.assertEqual(err, "", "导出时标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_REPLACED_EXPORT,
            "导出应保留标题空格、第一条重复文本、第三条多行文本与顺序，"
            "仅第二条为新内容；多行条目只在首行带 '- ' 前缀，正文不裁剪转义",
        )

    def test_two_independent_export_processes_are_verbatim_identical(self):
        self.create_fixed_sample()

        code, out, err = set_change(self.db, REPLACE_INDEX, REPLACEMENT)
        self.assertEqual((code, out, err), (0, "Updated change: demo-0.1 #2\n", ""))

        # 两次相互独立的进程导出，完整 UTF-8 文本逐字相同，末尾换行
        # 也参与比较；且都等于独立写明的预期。
        first_code, first_out, first_err = export_markdown(self.db)
        second_code, second_out, second_err = export_markdown(self.db)
        self.assertEqual((first_code, first_err), (0, ""))
        self.assertEqual((second_code, second_err), (0, ""))
        self.assertEqual(
            first_out,
            second_out,
            "两次独立进程导出应逐字相同（含末尾换行）",
        )
        self.assertEqual(first_out, EXPECTED_REPLACED_EXPORT)

        # 整个流程只产生数据库文件，不留下任何发布说明样例文件。
        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            {os.path.basename(self.db)},
            "测试结束后目录内应只剩临时数据库，不得留下导出样例文件",
        )


class TestFailedSetChangeLeavesExportIntact(SetChangeExportTestCase):
    def test_index_beyond_count_fails_and_export_keeps_original(self):
        self.create_fixed_sample()

        # 序号 04 超过三条变更：有效新文本也必须被拒绝。
        code, out, err = set_change(self.db, BEYOND_INDEX, BEYOND_TEXT)
        self.assertEqual(code, 1, "序号越界应退出 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #4\n",
            "标准错误应恰为 Change not found 消息（序号不保留前导零）加末尾换行",
        )

        # 失败后导出与事先独立写明的原始 Markdown 完全一致。
        code, out, err = export_markdown(self.db)
        self.assertEqual(code, 0, "失败不应影响后续导出")
        self.assertEqual(err, "", "导出时标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_ORIGINAL_EXPORT,
            "越界失败后导出必须与替换前的原始 Markdown 完全一致",
        )

    def test_blank_change_fails_and_export_keeps_original(self):
        self.create_fixed_sample()

        # 序号 2 合法，但新文本仅含空白：按 Invalid draft 拒绝。
        code, out, err = set_change(self.db, BLANK_TEXT_INDEX, BLANK_TEXT)
        self.assertEqual(code, 1, "空白新文本应退出 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            "标准错误应恰为 Invalid draft 加末尾换行",
        )

        # 失败后导出仍与事先独立写明的原始 Markdown 完全一致。
        code, out, err = export_markdown(self.db)
        self.assertEqual(code, 0, "失败不应影响后续导出")
        self.assertEqual(err, "", "导出时标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_ORIGINAL_EXPORT,
            "空白文本失败后导出必须与替换前的原始 Markdown 完全一致",
        )

        # 失败流程只产生数据库文件，不留下任何发布说明样例文件。
        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            {os.path.basename(self.db)},
            "测试结束后目录内应只剩临时数据库，不得留下导出样例文件",
        )


if __name__ == "__main__":
    unittest.main()
