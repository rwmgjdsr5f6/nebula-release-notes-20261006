"""release_notes.py move-change 长数字序号的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：固定样例为版本 demo-long、标题"预览版"、三条变更（第一条与
    第三条均为"新增预览"，第二条原文为 " 改善提示\\n保留换行 "，含首尾
    空格与一个内部换行）。--from 为五千个字符 0 后接 3、--to 为 1 时退出
    码 0、标准错误为空、标准输出恰为 "Moved change: demo-long #3 -> #1"
    加换行；新进程执行 show 与 export-markdown 时顺序为两条新增预览再接
    完整两行文本，标题、条目数量、首尾空格与内部换行均保留，两个入口仍
    使用各自原有格式。
  - 失败路径：记 N 为五千个字符 9 组成的数字串。版本存在时，来源为
    000+N、目标为 1，或来源为 1、目标为 000+N，均报
    "Change not found: demo-long #" + N；来源为 000+N、目标为 04 时仍
    报告来源的 N。来源为 N、目标为 0 时优先报 Invalid draft，同一输入在
    数据库路径不存在时也同样报 Invalid draft 且不创建文件。所有失败退出
    码 1、标准输出为空、标准错误只含指定消息及末尾换行、无异常堆栈；对
    已有草稿，失败前后的 show 与 export-markdown 输出逐字相同。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推，也不调用
被测函数生成期望值；比较完整 UTF-8 文本而非片段。

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

# 固定样例：版本 demo-long，标题"预览版"，三条变更依次为两条重复文本夹
# 一条含首尾空格与内部换行的两行文本（"\n" 表示一个实际换行，引号不属于
# 内容）。
VERSION = "demo-long"
TITLE = "预览版"
MULTILINE_CHANGE = " 改善提示\n保留换行 "
CHANGES = ["新增预览", MULTILINE_CHANGE, "新增预览"]

# 长数字序号：五千个字符 0 后接 3（数值为 3，前导零不影响定位）；
# N 为五千个字符 9 组成的数字串（远超条目总数，用于越界路径）。
LONG_FROM_THREE = "0" * 5000 + "3"
LONG_NINE = "9" * 5000
PADDED_LONG_NINE = "000" + LONG_NINE

# 移动第三条到第一条后的期望 show 输出（按 README 固定格式独立写明）：
# 两条新增预览在前，两行文本在后；多行条目只在首行前加 "- "，首尾空格与
# 内部换行原样保留。
EXPECTED_SHOW_AFTER_MOVE = (
    "Version: demo-long\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
)

# 移动后的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_AFTER_MOVE = (
    "# demo-long\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
)

# 移动前（原始顺序）的期望 show 与导出文本，用于核对失败前后逐字相同。
EXPECTED_SHOW_BEFORE = (
    "Version: demo-long\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
    "- 新增预览\n"
)
EXPECTED_EXPORT_BEFORE = (
    "# demo-long\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
    "- 新增预览\n"
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


def show(db_path, version):
    return run_cli(db_path, "show", version)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


def move_change(db_path, version, *move_args):
    return run_cli(db_path, "move-change", version, *move_args)


class LongIndexTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_fixture(self):
        """按固定样例创建 demo-long（标题"预览版"，三条变更）。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-long 退出码应为 0")
        self.assertEqual(out, "Created demo-long\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def assert_failure_keeps_draft(self, expected_err, *move_args):
        """在已有草稿上执行失败的移动并核对可观察行为。

        退出码 1、标准输出为空、标准错误恰为 expected_err、无异常堆栈；
        失败前后的 show 与 export-markdown 输出逐字相同。
        """
        self.create_fixture()

        show_before = show(self.db, VERSION)
        export_before = export_markdown(self.db, VERSION)
        self.assertEqual(show_before, (0, EXPECTED_SHOW_BEFORE, ""))
        self.assertEqual(export_before, (0, EXPECTED_EXPORT_BEFORE, ""))

        code, out, err = move_change(self.db, VERSION, *move_args)
        self.assertEqual(code, 1, "失败退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, expected_err, "标准错误应恰为指定消息加末尾换行"
        )
        self.assertNotIn("Traceback", err, "标准错误不应包含异常堆栈")

        show_after = show(self.db, VERSION)
        export_after = export_markdown(self.db, VERSION)
        self.assertEqual(
            show_after, show_before, "失败前后的 show 输出应逐字相同"
        )
        self.assertEqual(
            export_after,
            export_before,
            "失败前后的 export-markdown 输出应逐字相同",
        )


class TestMoveChangeLongIndexSuccess(LongIndexTestCase):
    def test_move_with_long_leading_zero_index(self):
        self.create_fixture()

        # 即 python release_notes.py --db notes.sqlite move-change demo-long \
        #   --from 000...0003（五千个 0 后接 3） --to 1
        code, out, err = move_change(
            self.db, VERSION, "--from", LONG_FROM_THREE, "--to", "1"
        )
        self.assertEqual(code, 0, "move-change 退出码应为 0")
        self.assertEqual(err, "", "move-change 标准错误应为空")
        self.assertEqual(
            out,
            "Moved change: demo-long #3 -> #1\n",
            "move-change 标准输出应恰为 Moved change: demo-long #3 -> #1"
            " 加末尾换行（序号不保留前导零）",
        )

        # 新进程执行 show：顺序为两条新增预览再接完整两行文本，标题、条目
        # 数量、首尾空格与内部换行均保留。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_MOVE,
            "移动后应依次为新增预览、新增预览、两行文本：首尾空格与内部"
            "换行原样保留，多行条目只占一个序号",
        )

        # 新进程执行 export-markdown：同一新顺序，仍使用导出原有格式。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_MOVE,
            "导出文本应精确匹配固定格式并按移动后的顺序输出",
        )


class TestMoveChangeLongIndexNotFound(LongIndexTestCase):
    def test_from_out_of_range(self):
        # 来源为 000+N、目标为 1：报告来源的 N（去前导零）。
        self.assert_failure_keeps_draft(
            "Change not found: demo-long #" + LONG_NINE + "\n",
            "--from", PADDED_LONG_NINE, "--to", "1",
        )

    def test_to_out_of_range(self):
        # 来源为 1、目标为 000+N：报告目标的 N。
        self.assert_failure_keeps_draft(
            "Change not found: demo-long #" + LONG_NINE + "\n",
            "--from", "1", "--to", PADDED_LONG_NINE,
        )

    def test_both_out_of_range_reports_from(self):
        # 来源为 000+N、目标为 04：两者都越界时仍报告来源的 N。
        self.assert_failure_keeps_draft(
            "Change not found: demo-long #" + LONG_NINE + "\n",
            "--from", PADDED_LONG_NINE, "--to", "04",
        )


class TestMoveChangeLongIndexInvalid(LongIndexTestCase):
    def test_invalid_to_zero_with_long_from_existing_draft(self):
        # 来源为 N、目标为 0：输入校验优先于序号定位，报 Invalid draft。
        self.assert_failure_keeps_draft(
            "Invalid draft\n",
            "--from", LONG_NINE, "--to", "0",
        )

    def test_invalid_to_zero_with_long_from_missing_database(self):
        # 同一输入在数据库路径不存在时同样报 Invalid draft，且不创建文件。
        code, out, err = move_change(
            self.db, VERSION, "--from", LONG_NINE, "--to", "0"
        )
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertNotIn("Traceback", err, "标准错误不应包含异常堆栈")
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )


if __name__ == "__main__":
    unittest.main()
