"""release_notes.py move-change 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（demo-0.1，标题"预览版"，三条变更依次为
    新增预览、改善提示、新增预览，另有一个独立版本）上以带前导零的
    --from 03 --to 1 把第三条移动到首位；退出码 0、标准错误为空、标准
    输出恰为 "Moved change: demo-0.1 #3 -> #1" 加换行；随后 show 得到
    新增预览、新增预览、改善提示，标题与另一版本保持原样，新进程查看
    结果相同，export-markdown 按固定格式反映新顺序且不产生文件；移动后
    set-change / remove-change 按新序号定位、add-change 仍追加到末尾。
  - 顺序语义：来源与目标相同也成功且内容顺序不变；前移与后移时所选条目
    恰好位于目标序号（目标为最终位置，不因来源在前而减一），其余条目
    相对顺序不变；删除造成位置不连续后移动仍按展示顺序计数。
  - 原文保留：多行文本只占一个序号，首尾空格与内部换行随条目一起移动，
    重复记录分别保留，条目数量不变，其他版本不受影响。
  - 失败路径：未提供或重复提供 --from / --to、版本名为空 / 仅空白、
    任一序号不符合要求（0、非数字、负数、小数）均报 Invalid draft 且不
    创建数据库；数据库文件不存在或版本不存在报 Version not found；
    序号越界报 Change not found（序号去前导零），先检查来源再检查目标，
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

# 固定样例：版本 demo-0.1，标题"预览版"，三条变更依次为首尾重复文本
# 夹住一条独立文本；另有一个独立版本 other-1.0 用于验证互不影响。
VERSION = "demo-0.1"
TITLE = "预览版"
CHANGES = ["新增预览", "改善提示", "新增预览"]

OTHER_VERSION = "other-1.0"
OTHER_TITLE = "正式版"
OTHER_CHANGES = ["首个变更", "第二个变更"]

# 第三条移动到首位后的期望 show 输出（按 README 固定格式独立写明）：
# 两条重复文本相邻，原第二条移到末尾，其余条目相对顺序不变。
EXPECTED_SHOW_AFTER_MOVE = (
    "Version: demo-0.1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 改善提示\n"
)

# 另一版本的期望 show 输出：不受 demo-0.1 移动影响，自身顺序也不变。
EXPECTED_SHOW_OTHER = (
    "Version: other-1.0\n"
    "Title: 正式版\n"
    "- 首个变更\n"
    "- 第二个变更\n"
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
        code, out, err = move_change(self.db, VERSION, "--from", "03", "--to", "1")
        self.assertEqual(code, 0, "move-change 退出码应为 0")
        self.assertEqual(err, "", "move-change 标准错误应为空")
        self.assertEqual(
            out,
            "Moved change: demo-0.1 #3 -> #1\n",
            "move-change 标准输出应恰为固定文案加末尾换行",
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
            "移动后第三条应位于首位：两条重复文本相邻，原第二条相对后移",
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
            "导出文本应精确匹配固定格式：新顺序的三条变更",
        )
        self.assertEqual(before, after, "export-markdown 不应产生任何文件")

        # 移动后 set-change / remove-change 按新序号定位，add-change
        # 仍追加到末尾：先把新第二条（原第三条的重复文本）替换，再删除
        # 新第三条（改善提示），最后追加。
        code, out, err = run_cli(
            self.db, "set-change", VERSION, "--index", "2",
            "--change", "新增预览（修订）",
        )
        self.assertEqual(code, 0, "移动后 set-change 退出码应为 0")
        self.assertEqual(out, "Updated change: demo-0.1 #2\n")
        self.assertEqual(err, "")

        code, out, err = run_cli(
            self.db, "remove-change", VERSION, "--index", "3",
        )
        self.assertEqual(code, 0, "移动后 remove-change 退出码应为 0")
        self.assertEqual(out, "Removed change: demo-0.1 #3\n")
        self.assertEqual(err, "")

        code, out, err = run_cli(
            self.db, "add-change", VERSION, "--change", "补充说明",
        )
        self.assertEqual(code, 0, "移动后 add-change 退出码应为 0")
        self.assertEqual(out, "Added change: demo-0.1\n")
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
            "替换与删除应按移动后的新序号定位，追加应落在末尾",
        )

        # 另一版本在上述全部操作后仍保持原样。
        code, out, err = show(self.db, OTHER_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW_OTHER, "另一版本始终不应受影响")


class TestMoveChangeOrdering(MoveChangeTestCase):
    """用 A/B/C/D 四条记录验证最终位置语义与相对顺序。"""

    def show_items(self):
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        return [line[2:] for line in out.splitlines()[2:]]

    def test_same_source_and_target_is_noop_success(self):
        create(self.db, VERSION, TITLE, ["A", "B", "C", "D"])
        code, out, err = move_change(
            self.db, VERSION, "--from", "03", "--to", "003"
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, "Moved change: demo-0.1 #3 -> #3\n")
        self.assertEqual(
            self.show_items(), ["A", "B", "C", "D"], "来源与目标相同时顺序不变"
        )

    def test_move_forward_target_is_final_position(self):
        create(self.db, VERSION, TITLE, ["A", "B", "C", "D"])
        # 1 -> 3：目标表示最终位置，不因来源在前而减一，结果 B C A D。
        code, out, err = move_change(
            self.db, VERSION, "--from", "1", "--to", "3"
        )
        self.assertEqual((code, out, err), (0, "Moved change: demo-0.1 #1 -> #3\n", ""))
        self.assertEqual(
            self.show_items(), ["B", "C", "A", "D"],
            "前移后所选条目应恰好位于目标序号，其余条目相对顺序不变",
        )

    def test_move_backward(self):
        create(self.db, VERSION, TITLE, ["A", "B", "C", "D"])
        # 4 -> 2：后移时目标同样是最终位置，结果 A D B C。
        code, _, err = move_change(self.db, VERSION, "--from", "4", "--to", "2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            self.show_items(), ["A", "D", "B", "C"],
            "后移后所选条目应恰好位于目标序号，其余条目相对顺序不变",
        )

    def test_move_first_to_end_and_last_to_start(self):
        create(self.db, VERSION, TITLE, ["A", "B", "C"])
        code, _, err = move_change(self.db, VERSION, "--from", "1", "--to", "3")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.show_items(), ["B", "C", "A"], "首条移到末尾")
        code, _, err = move_change(self.db, VERSION, "--from", "3", "--to", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.show_items(), ["A", "B", "C"], "末条移到首位")

    def test_move_after_remove_uses_display_order(self):
        # 删除中间条目后内部位置不连续，移动仍按展示顺序计数，且追加
        # 仍落在末尾。
        create(self.db, VERSION, TITLE, ["A", "B", "C"])
        code, _, err = run_cli(self.db, "remove-change", VERSION, "--index", "2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.show_items(), ["A", "C"])
        code, _, err = move_change(self.db, VERSION, "--from", "1", "--to", "2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.show_items(), ["C", "A"], "删除造成位置不连续后仍可移动")
        code, _, err = run_cli(self.db, "add-change", VERSION, "--change", "Z")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.show_items(), ["C", "A", "Z"], "追加仍落在末尾")

    def test_move_single_entry_draft(self):
        create(self.db, VERSION, TITLE, ["唯一变更"])
        code, out, err = move_change(self.db, VERSION, "--from", "1", "--to", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "Moved change: demo-0.1 #1 -> #1\n")
        self.assertEqual(self.show_items(), ["唯一变更"])

    def test_repeated_moves_and_duplicate_contents_preserved(self):
        # 重复文本分别保留；多次移动后条目数量不变。
        create(self.db, VERSION, TITLE, ["X", "X", "Y", "X"])
        for source, target in (("1", "4"), ("2", "1"), ("4", "2")):
            code, _, err = move_change(
                self.db, VERSION, "--from", source, "--to", target
            )
            self.assertEqual((code, err), (0, ""))
        items = self.show_items()
        self.assertEqual(len(items), 4, "移动不改变条目数量")
        self.assertEqual(sorted(items), ["X", "X", "X", "Y"], "重复记录全部保留")


class TestMoveChangeVerbatimContent(MoveChangeTestCase):
    def test_multiline_and_surrounding_spaces_move_intact(self):
        multiline = "第一行\n第二行\n  缩进第三行"
        spaced = "  首尾空格  "
        create(self.db, VERSION, TITLE, [multiline, spaced, "第三条"])

        # 多行条目从第 1 位移到第 3 位，只占一个序号；原文逐字保留。
        code, out, err = move_change(self.db, VERSION, "--from", "1", "--to", "3")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "Moved change: demo-0.1 #1 -> #3\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            f"- {spaced}\n"
            "- 第三条\n"
            f"- {multiline}\n",
            "多行文本与首尾空格应随条目原样移动，内部换行不拆成多条",
        )

        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n"
            f"- {spaced}\n"
            "- 第三条\n"
            f"- {multiline}\n",
            "导出应同样保留多行与首尾空格",
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
        self.assert_invalid("--from", "1", "--to", "1", version="")
        self.assert_invalid("--from", "1", "--to", "1", version="  \n\t ")

    def test_bad_index_values(self):
        bad_values = ("0", "00", "abc", "-1", "1.5", " 1", "1 ", "+1", "1a")
        for bad in bad_values:
            with self.subTest(side="from", index=bad):
                self.assert_invalid("--from", bad, "--to", "1")
            with self.subTest(side="to", index=bad):
                self.assert_invalid("--from", "1", "--to", bad)


class TestMoveChangeNotFound(MoveChangeTestCase):
    def test_missing_database_file(self):
        code, out, err = move_change(self.db, VERSION, "--from", "1", "--to", "2")
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

    def test_missing_version_is_case_sensitive(self):
        self.create_fixtures()
        code, out, err = move_change(
            self.db, "Demo-0.1", "--from", "1", "--to", "2"
        )
        self.assertEqual(code, 1, "版本不存在（含仅大小写不同）退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: Demo-0.1\n")

    def test_source_checked_before_target_when_both_out_of_range(self):
        self.create_fixtures()
        code, out, err = move_change(
            self.db, VERSION, "--from", "05", "--to", "06"
        )
        self.assertEqual(code, 1, "序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #5\n",
            "两者都越界时应先报告来源（序号不保留前导零）",
        )

    def test_target_out_of_range_reported_after_valid_source(self):
        self.create_fixtures()
        code, out, err = move_change(
            self.db, VERSION, "--from", "2", "--to", "04"
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #4\n",
            "来源有效而目标越界时应报告目标",
        )

    def test_huge_out_of_range_index(self):
        self.create_fixtures()
        code, out, err = move_change(
            self.db, VERSION,
            "--from", "99999999999999999999999", "--to", "1",
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(
            err, "Change not found: demo-0.1 #99999999999999999999999\n"
        )

    def test_failed_move_leaves_data_unchanged(self):
        self.create_fixtures()
        # 先做一次成功移动打乱初始顺序，再触发越界失败。
        code, _, err = move_change(self.db, VERSION, "--from", "1", "--to", "3")
        self.assertEqual((code, err), (0, ""))
        code, out, err = move_change(
            self.db, VERSION, "--from", "1", "--to", "9"
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "Change not found: demo-0.1 #9\n")
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n"
            "- 改善提示\n- 新增预览\n- 新增预览\n",
            "越界失败不得改变移动后的已有顺序",
        )


if __name__ == "__main__":
    unittest.main()
