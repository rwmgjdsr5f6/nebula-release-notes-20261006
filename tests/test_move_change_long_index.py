"""release_notes.py move-change 长数字序号的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（demo-long，标题"预览版"，三条变更依次为
    新增预览、" 改善提示\\n保留换行 "（含一个实际换行与首尾空格）、
    新增预览）上以五千个字符 0 后接 3 作为 --from、--to 为 1 把第三条
    移动到第一条；退出码 0、标准错误为空、标准输出恰为
    "Moved change: demo-long #3 -> #1" 加换行；随后新进程 show 与
    export-markdown 依次为两条新增预览再接完整两行文本，标题、条目
    数量、首尾空格与内部换行均保留，两个入口各自保持原有格式。
  - 失败路径：N 为五千个字符 9 组成的数字串。版本存在时，来源为
    000+N、目标为 1，或来源为 1、目标为 000+N，均报
    "Change not found: demo-long #N"（序号去前导零）；来源为 000+N、
    目标为 04（目标在界内但来源越界）时仍报告来源的 N。来源为 N、
    目标为 0 时优先报 Invalid draft，同一输入在数据库路径不存在时
    也如此，且不创建文件。所有失败退出码 1、标准输出为空、标准错误
    只含指定消息及末尾换行，不出现异常堆栈；对已有草稿，失败前后的
    show 与 export-markdown 输出逐字相同。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推、不调用
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

# 固定样例：版本 demo-long，标题"预览版"，三条变更依次为两条重复文本
# 夹一条含首尾空格与内部换行的两行文本（引号不属于内容）。
VERSION = "demo-long"
TITLE = "预览版"
MULTILINE_CHANGE = " 改善提示\n保留换行 "
CHANGES = ["新增预览", MULTILINE_CHANGE, "新增预览"]

# 长数字序号：五千个字符 0 后接 3 作为来源（去前导零后定位第三条）；
# N 为五千个字符 9 组成的数字串，远超条目总数，用于越界路径。
LONG_FROM_THREE = "0" * 5000 + "3"
N = "9" * 5000
PADDED_N = "000" + N

# 移动前的期望 show 输出（按 README 固定格式独立写明）：多行条目只在
# 首行前加 "- "，内部换行与首尾空格原样保留。
EXPECTED_SHOW_BEFORE = (
    "Version: demo-long\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
    "- 新增预览\n"
)

# 移动前的期望导出文本（按 README 固定格式独立写明）。
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

# 把第三条移动到第一条后的期望 show 输出：两条新增预览在前，完整两行
# 文本在后，标题、条目数量、首尾空格与内部换行均保留。
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


def move_change(db_path, version, *move_args):
    return run_cli(db_path, "move-change", version, *move_args)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class MoveChangeLongIndexTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_fixtures(self):
        """按固定样例创建 demo-long，并核对创建结果与移动前的两个入口。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-long 退出码应为 0")
        self.assertEqual(out, "Created demo-long\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "移动前 show 退出码应为 0")
        self.assertEqual(err, "", "移动前 show 标准错误应为空")
        self.assertEqual(
            out, EXPECTED_SHOW_BEFORE, "移动前 show 输出应逐字符合固定格式"
        )

        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "移动前 export-markdown 退出码应为 0")
        self.assertEqual(err, "", "移动前 export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_BEFORE,
            "移动前 export-markdown 输出应逐字符合固定格式",
        )


class TestMoveChangeLongIndexSuccess(MoveChangeLongIndexTestCase):
    def test_move_with_long_zero_padded_from(self):
        self.create_fixtures()

        # 即 python release_notes.py --db notes.sqlite move-change demo-long \
        #   --from 000…0003（五千个 0 后接 3） --to 1
        # 前导零不影响定位，输出序号不保留前导零。
        code, out, err = move_change(
            self.db, VERSION, "--from", LONG_FROM_THREE, "--to", "1"
        )
        self.assertEqual(code, 0, "move-change 退出码应为 0")
        self.assertEqual(err, "", "move-change 标准错误应为空")
        self.assertEqual(
            out,
            "Moved change: demo-long #3 -> #1\n",
            "move-change 标准输出应恰为 Moved change: demo-long #3 -> #1"
            " 加末尾换行",
        )

        # 新进程查看：顺序为两条新增预览再接完整两行文本。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "移动后 show 退出码应为 0")
        self.assertEqual(err, "", "移动后 show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_MOVE,
            "移动后应依次为新增预览、新增预览、完整两行文本：标题、条目"
            "数量、首尾空格与内部换行均保留",
        )

        # 新进程导出：同一新顺序，按 export-markdown 原有固定格式。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "移动后 export-markdown 退出码应为 0")
        self.assertEqual(err, "", "移动后 export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_MOVE,
            "移动后导出文本应精确匹配固定格式并按新顺序输出",
        )


class TestMoveChangeLongIndexNotFound(MoveChangeLongIndexTestCase):
    """版本存在时，越界长序号报 Change not found，已有内容逐字不变。"""

    def assert_change_not_found(self, *move_args):
        self.create_fixtures()

        # 失败前的两个入口输出，用于失败后逐字比对。
        _, show_before, _ = show(self.db, VERSION)
        _, export_before, _ = export_markdown(self.db, VERSION)

        code, out, err = move_change(self.db, VERSION, *move_args)
        self.assertEqual(code, 1, "序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-long #" + N + "\n",
            "标准错误应恰为 Change not found: demo-long # 加 N 和末尾换行"
            "（序号去前导零），不出现异常堆栈",
        )

        # 失败后 show 与 export-markdown 输出与失败前逐字相同。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 show 退出码应为 0")
        self.assertEqual(err, "", "失败后 show 标准错误应为空")
        self.assertEqual(out, show_before, "失败后 show 输出应与失败前逐字相同")
        self.assertEqual(
            out, EXPECTED_SHOW_BEFORE, "失败移动不得改变已有内容与顺序"
        )

        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 export-markdown 退出码应为 0")
        self.assertEqual(err, "", "失败后 export-markdown 标准错误应为空")
        self.assertEqual(
            out, export_before, "失败后 export-markdown 输出应与失败前逐字相同"
        )
        self.assertEqual(
            out, EXPECTED_EXPORT_BEFORE, "失败移动不得改变已有内容与顺序"
        )

    def test_from_out_of_range(self):
        # 来源为 000+N、目标为 1：报告来源的 N（去前导零）。
        self.assert_change_not_found("--from", PADDED_N, "--to", "1")

    def test_to_out_of_range(self):
        # 来源为 1、目标为 000+N：报告目标的 N（去前导零）。
        self.assert_change_not_found("--from", "1", "--to", PADDED_N)

    def test_from_out_of_range_with_in_range_to(self):
        # 来源为 000+N、目标为 04（目标在界内）：仍报告来源的 N。
        self.assert_change_not_found("--from", PADDED_N, "--to", "04")


class TestMoveChangeLongIndexInvalidDraft(MoveChangeLongIndexTestCase):
    """目标序号为 0 时输入校验优先：报 Invalid draft，不创建数据库文件。"""

    def assert_invalid_draft(self):
        code, out, err = move_change(self.db, VERSION, "--from", N, "--to", "0")
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            "标准错误应恰为 Invalid draft 加末尾换行，不出现异常堆栈",
        )

    def test_invalid_input_with_existing_draft(self):
        self.create_fixtures()
        _, show_before, _ = show(self.db, VERSION)
        _, export_before, _ = export_markdown(self.db, VERSION)

        self.assert_invalid_draft()

        # 已有草稿在失败前后逐字相同。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 show 退出码应为 0")
        self.assertEqual(err, "", "失败后 show 标准错误应为空")
        self.assertEqual(out, show_before, "失败后 show 输出应与失败前逐字相同")
        self.assertEqual(out, EXPECTED_SHOW_BEFORE)

        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 export-markdown 退出码应为 0")
        self.assertEqual(err, "", "失败后 export-markdown 标准错误应为空")
        self.assertEqual(
            out, export_before, "失败后 export-markdown 输出应与失败前逐字相同"
        )
        self.assertEqual(out, EXPECTED_EXPORT_BEFORE)

    def test_invalid_input_with_missing_database(self):
        # 数据库路径不存在时同一输入同样优先报 Invalid draft，且不创建文件。
        self.assertFalse(os.path.exists(self.db), "前置条件：数据库文件不存在")
        self.assert_invalid_draft()
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )


if __name__ == "__main__":
    unittest.main()
