"""release_notes.py “删除后再移动”衔接场景的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（demo-0.2，标题"预览版"，四条变更依次为
    新增预览、临时记录、一条两行文本、新增预览；两行文本首行为
    " 改善提示"、次行为 "保留换行 "，两行之间恰有一个换行，首尾空格
    属于原文）上先执行 remove-change demo-0.2 --index 2，再执行
    move-change demo-0.2 --from 02 --to 03。两次退出码均为 0、标准错误
    为空、标准输出恰为 "Removed change: demo-0.2 #2" 与
    "Moved change: demo-0.2 #2 -> #3" 各加一个末尾换行；移动后顺序为
    两条“新增预览”再接完整两行文本，重复条目都保留，标题与版本名不变；
    show 与 export-markdown 按各自固定格式完整呈现该顺序，换行与空格
    不裁剪，多行条目只有首行带条目前缀，新进程读取得到相同结果。
  - 失败路径：从相同的删除后状态（新增预览、完整两行文本、新增预览）
    出发，分别请求 move-change --from 04 --to 1 与 --from 1 --to 04；
    两者均退出 1、标准输出为空、标准错误恰为
    "Change not found: demo-0.2 #4" 加一个换行；失败前后的 show 与
    export-markdown 输出逐字一致，仍为删除后的三条记录。

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

# 固定样例：版本 demo-0.2，标题"预览版"，四条变更依次为两条重复文本、
# 一条独立文本与一条两行文本。两行文本首行 " 改善提示" 带一个前导空格，
# 次行 "保留换行 " 带一个末尾空格，两行之间恰有一个换行，首尾空格属于原文。
VERSION = "demo-0.2"
TITLE = "预览版"
MULTILINE = " 改善提示\n保留换行 "
CHANGES = ["新增预览", "临时记录", MULTILINE, "新增预览"]

# 删除第二条（临时记录）后的期望 show 输出（按 README 固定格式独立写明）：
# 剩余三条按原相对顺序排列，多行条目只有首行带 "- " 前缀，内部换行与
# 首尾空格原样保留。
EXPECTED_SHOW_AFTER_REMOVE = (
    "Version: demo-0.2\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
    "- 新增预览\n"
)

# 删除后的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_AFTER_REMOVE = (
    "# demo-0.2\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
    "- 新增预览\n"
)

# 删除后再把第二条（两行文本）移动到第三条后的期望 show 输出：两条
# “新增预览”在前，完整两行文本在最后，重复条目都保留。
EXPECTED_SHOW_AFTER_MOVE = (
    "Version: demo-0.2\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "-  改善提示\n"
    "保留换行 \n"
)

# 移动后的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_AFTER_MOVE = (
    "# demo-0.2\n"
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


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class RemoveThenMoveTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_fixture(self):
        """按固定样例创建 demo-0.2（标题"预览版"，四条变更）。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.2 退出码应为 0")
        self.assertEqual(out, "Created demo-0.2\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def remove_second_change(self):
        """删除第二条变更（临时记录），到达各用例共用的删除后状态。"""
        # 即 python release_notes.py --db notes.sqlite remove-change demo-0.2 \
        #   --index 2
        code, out, err = run_cli(
            self.db, "remove-change", VERSION, "--index", "2"
        )
        self.assertEqual(code, 0, "remove-change 退出码应为 0")
        self.assertEqual(err, "", "remove-change 标准错误应为空")
        self.assertEqual(
            out,
            "Removed change: demo-0.2 #2\n",
            "remove-change 标准输出应恰为 Removed change: demo-0.2 #2"
            " 加末尾换行",
        )

    def prepare_removed_state(self):
        """创建样例并删除第二条，得到三条记录的删除后状态。"""
        self.create_fixture()
        self.remove_second_change()

    def assert_removed_state(self):
        """确认当前状态逐字等于删除后的三条记录（show 与导出）。"""
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_REMOVE,
            "删除后应依次为新增预览、完整两行文本、新增预览",
        )
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_REMOVE,
            "删除后导出文本应精确匹配固定格式",
        )


class TestRemoveThenMoveSuccess(RemoveThenMoveTestCase):
    def test_remove_second_then_move_second_to_third(self):
        self.prepare_removed_state()

        # 即 python release_notes.py --db notes.sqlite move-change demo-0.2 \
        #   --from 02 --to 03
        # 序号按剩余记录的当前展示顺序定位，前导零不影响定位，输出序号不
        # 保留前导零。
        code, out, err = run_cli(
            self.db, "move-change", VERSION, "--from", "02", "--to", "03"
        )
        self.assertEqual(code, 0, "move-change 退出码应为 0")
        self.assertEqual(err, "", "move-change 标准错误应为空")
        self.assertEqual(
            out,
            "Moved change: demo-0.2 #2 -> #3\n",
            "move-change 标准输出应恰为 Moved change: demo-0.2 #2 -> #3"
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
            "移动后应依次为两条新增预览再接完整两行文本：重复条目都保留，"
            "标题与版本名不变，多行条目只有首行带条目前缀，换行与空格不裁剪",
        )

        # 导出按固定格式反映新顺序，且不产生任何文件。
        before = set(os.listdir(self._tmpdir.name))
        code, out, err = export_markdown(self.db, VERSION)
        after = set(os.listdir(self._tmpdir.name))
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_MOVE,
            "导出文本应精确匹配固定格式并按移动后的顺序完整呈现两行文本",
        )
        self.assertEqual(before, after, "export-markdown 不应产生任何文件")


class TestRemoveThenMoveOutOfRange(RemoveThenMoveTestCase):
    """删除后越界移动：退出 1、输出为空、状态逐字不变。"""

    def assert_move_out_of_range(self, *move_args):
        # 每次从相同的删除后状态开始（独立临时库，互不影响）。
        self.prepare_removed_state()
        self.assert_removed_state()

        code, out, err = run_cli(self.db, "move-change", VERSION, *move_args)
        self.assertEqual(code, 1, "序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.2 #4\n",
            "标准错误应恰为 Change not found: demo-0.2 #4 加末尾换行"
            "（序号不保留前导零）",
        )

        # 失败后的 show 与 export-markdown 与失败前逐字一致。
        self.assert_removed_state()

    def test_from_out_of_range(self):
        self.assert_move_out_of_range("--from", "04", "--to", "1")

    def test_to_out_of_range(self):
        self.assert_move_out_of_range("--from", "1", "--to", "04")


if __name__ == "__main__":
    unittest.main()
