"""release_notes.py move-change 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（demo-0.1，标题"预览版"，三条变更依次为
    新增预览、改善提示、新增预览，另有一个独立版本）上以带前导零的序号
    --from 03 --to 1 把第三条移动到第一条；退出码 0、标准错误为空、标准
    输出恰为 "Moved change: demo-0.1 #3 -> #1" 加换行；随后 show 依次
    为新增预览、新增预览、改善提示，标题与另一版本保持原样，新进程查看
    结果相同，export-markdown 按固定格式反映新顺序；移动后 set-change、
    remove-change 按新序号定位，add-change 仍追加到末尾。来源与目标
    相同也成功且顺序不变；向后移动（1 -> 3）目标即最终位置，不减一。
  - 失败路径：未提供或重复提供 --from / --to、版本名为空 / 仅空白、
    序号不符合要求（0、非数字、负数、小数）均报 Invalid draft 且不创建
    数据库；数据库文件不存在或版本不存在报 Version not found；版本存在
    后先检查来源再检查目标，越界报 Change not found（序号去前导零），
    两者都越界时报告来源。所有失败退出码 1、标准输出为空、错误末尾带
    换行，已有数据不变。

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

# 固定样例：版本 demo-0.1，标题"预览版"，三条变更依次为两条重复文本
# 夹一条独立文本；另有一个独立版本 other-1.0 用于验证互不影响。
VERSION = "demo-0.1"
TITLE = "预览版"
CHANGES = ["新增预览", "改善提示", "新增预览"]

OTHER_VERSION = "other-1.0"
OTHER_TITLE = "正式版"
OTHER_CHANGES = ["首个变更"]

# 把第三条移动到第一条后的期望 show 输出（按 README 固定格式独立写明）：
# 所选条目恰好位于目标序号 1，其余条目相对顺序不变。
EXPECTED_SHOW_AFTER_MOVE = (
    "Version: demo-0.1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 改善提示\n"
)

# 另一版本的期望 show 输出：不受 demo-0.1 移动影响。
EXPECTED_SHOW_OTHER = (
    "Version: other-1.0\n"
    "Title: 正式版\n"
    "- 首个变更\n"
)

# 移动后的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_AFTER_MOVE = (
    "# demo-0.1\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 改善提示\n"
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


class MoveChangeTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_fixtures(self):
        """按固定样例创建 demo-0.1 与独立的 other-1.0。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")
        code, out, err = create(self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES)
        self.assertEqual(code, 0, "create other-1.0 退出码应为 0")
        self.assertEqual(err, "", "create other-1.0 标准错误应为空")


class TestMoveChangeSuccess(MoveChangeTestCase):
    def test_move_third_to_first_then_show_export_and_followups(self):
        self.create_fixtures()

        # 即 python release_notes.py --db notes.sqlite move-change demo-0.1 \
        #   --from 03 --to 1
        # 前导零不影响定位，输出序号不保留前导零。
        code, out, err = move_change(
            self.db, VERSION, "--from", "03", "--to", "1"
        )
        self.assertEqual(code, 0, "move-change 退出码应为 0")
        self.assertEqual(err, "", "move-change 标准错误应为空")
        self.assertEqual(
            out,
            "Moved change: demo-0.1 #3 -> #1\n",
            "move-change 标准输出应恰为 Moved change: demo-0.1 #3 -> #1"
            " 加末尾换行",
        )

        # 两个相互独立的进程查看，结果应逐字相同（含末尾换行）。
        first = show(self.db, VERSION)
        second = show(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程查看结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_MOVE,
            "移动后应依次为新增预览、新增预览、改善提示：所选条目恰好位于"
            "目标序号，其余条目相对顺序不变，重复记录分别保留",
        )

        # 标题与另一版本保持原样。
        code, out, err = show(self.db, OTHER_VERSION)
        self.assertEqual(code, 0, "show other-1.0 退出码应为 0")
        self.assertEqual(err, "", "show other-1.0 标准错误应为空")
        self.assertEqual(out, EXPECTED_SHOW_OTHER, "另一版本不应受影响")

        # 导出按固定格式反映新顺序，且不产生任何文件。
        before = set(os.listdir(self._tmpdir.name))
        code, out, err = export_markdown(self.db, VERSION)
        after = set(os.listdir(self._tmpdir.name))
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_MOVE,
            "导出文本应精确匹配固定格式并按移动后的顺序输出",
        )
        self.assertEqual(before, after, "export-markdown 不应产生任何文件")

        # 移动后 set-change 按新序号定位第二条。
        code, out, err = run_cli(
            self.db, "set-change", VERSION, "--index", "2",
            "--change", "新增预览（修订）",
        )
        self.assertEqual(code, 0, "移动后 set-change 退出码应为 0")
        self.assertEqual(out, "Updated change: demo-0.1 #2\n")
        self.assertEqual(err, "")

        # add-change 仍追加到末尾。
        code, out, err = run_cli(
            self.db, "add-change", VERSION, "--change", "补充说明",
        )
        self.assertEqual(code, 0, "移动后 add-change 退出码应为 0")
        self.assertEqual(out, "Added change: demo-0.1\n")
        self.assertEqual(err, "")

        # remove-change 按新序号定位第三条（原第一条改善提示）。
        code, out, err = run_cli(
            self.db, "remove-change", VERSION, "--index", "3",
        )
        self.assertEqual(code, 0, "移动后 remove-change 退出码应为 0")
        self.assertEqual(out, "Removed change: demo-0.1 #3\n")
        self.assertEqual(err, "")

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "最终 show 退出码应为 0")
        self.assertEqual(err, "", "最终 show 标准错误应为空")
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览（修订）\n"
            "- 补充说明\n",
            "set-change、remove-change 应按移动后的新序号定位，"
            "add-change 应追加到末尾",
        )

    def test_move_same_position_keeps_order(self):
        self.create_fixtures()

        code, out, err = move_change(
            self.db, VERSION, "--from", "2", "--to", "02"
        )
        self.assertEqual(code, 0, "来源与目标相同退出码应为 0")
        self.assertEqual(err, "", "来源与目标相同标准错误应为空")
        self.assertEqual(out, "Moved change: demo-0.1 #2 -> #2\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 改善提示\n"
            "- 新增预览\n",
            "来源与目标相同时内容与顺序应保持不变",
        )

    def test_move_forward_targets_final_position(self):
        self.create_fixtures()

        # 目标表示最终位置：把第一条移动到第三条，不因来源在前而减一。
        code, out, err = move_change(
            self.db, VERSION, "--from", "1", "--to", "3"
        )
        self.assertEqual(code, 0, "move-change 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(out, "Moved change: demo-0.1 #1 -> #3\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 改善提示\n"
            "- 新增预览\n"
            "- 新增预览\n",
            "向后移动时所选条目应恰好落在目标序号，其余条目相对顺序不变",
        )


class TestMoveChangeInvalidInput(MoveChangeTestCase):
    """输入校验优先于版本查找：无效输入一律 Invalid draft，且不创建数据库。"""

    def assert_invalid(self, *move_args, version=VERSION):
        code, out, err = move_change(self.db, version, *move_args)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )

    def test_missing_options(self):
        self.assert_invalid()
        self.assert_invalid("--from", "1")
        self.assert_invalid("--to", "1")

    def test_duplicate_options(self):
        self.assert_invalid("--from", "1", "--from", "2", "--to", "1")
        self.assert_invalid("--from", "1", "--to", "1", "--to", "2")

    def test_blank_version(self):
        self.assert_invalid("--from", "1", "--to", "2", version="")
        self.assert_invalid("--from", "1", "--to", "2", version="  \n\t ")

    def test_bad_index_values(self):
        for bad in ("0", "00", "abc", "-1", "1.5", " 1", "1 ", "+1", "1a"):
            with self.subTest(index=bad):
                self.assert_invalid("--from", bad, "--to", "1")
                self.assert_invalid("--from", "1", "--to", bad)


class TestMoveChangeNotFound(MoveChangeTestCase):
    def test_missing_database_file(self):
        code, out, err = move_change(
            self.db, VERSION, "--from", "1", "--to", "2"
        )
        self.assertEqual(code, 1, "数据库文件不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: demo-0.1\n",
            "标准错误应恰为 Version not found: demo-0.1 加末尾换行",
        )
        self.assertFalse(
            os.path.exists(self.db), "move-change 不得创建数据库文件"
        )

    def test_missing_version(self):
        self.create_fixtures()
        code, out, err = move_change(
            self.db, "Demo-0.1", "--from", "1", "--to", "2"
        )
        self.assertEqual(code, 1, "版本不存在（含仅大小写不同）退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: Demo-0.1\n")

    def assert_change_not_found(self, expected, *move_args):
        self.create_fixtures()
        code, out, err = move_change(self.db, VERSION, *move_args)
        self.assertEqual(code, 1, "序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            expected,
            "标准错误应恰为 Change not found 消息加末尾换行"
            "（序号不保留前导零）",
        )
        # 失败后内容保持不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n- 新增预览\n- 改善提示\n- 新增预览\n",
            "序号越界的失败移动不得改变已有内容与顺序",
        )

    def test_from_out_of_range(self):
        self.assert_change_not_found(
            "Change not found: demo-0.1 #4\n", "--from", "04", "--to", "1"
        )

    def test_to_out_of_range(self):
        self.assert_change_not_found(
            "Change not found: demo-0.1 #4\n", "--from", "1", "--to", "04"
        )

    def test_both_out_of_range_reports_from(self):
        self.assert_change_not_found(
            "Change not found: demo-0.1 #5\n", "--from", "05", "--to", "06"
        )


if __name__ == "__main__":
    unittest.main()
