"""release_notes.py remove-change 之后再 set-change 的离线回归测试。

针对 set-change / remove-change 共用流程（序号校验、版本确认、按展示
序号定位条目）的回归验收，仅通过命令行与外部可观察行为验证，不依赖
内部表结构：

  - 验收主流程：固定样例草稿（demo-0.1，标题"预览版"，三条变更依次为
    新增预览、新增预览、改善提示）上先以带前导零的序号 02 删除第二条，
    再按删除后的新序号 2 把"改善提示"替换为"修订提示"；两步均退出码 0、
    标准错误为空、标准输出恰为 README 消息加换行；随后两个相互独立的
    新进程 show 与 export-markdown 结果逐字相同，只剩新增预览与修订提示。
  - 越界优先级：草稿只剩一条变更时，越界序号仍先报 Change not found
    而非 Cannot remove last change；极大的合法数值序号同样按越界处理，
    消息中的序号不保留前导零。
  - set-change 无效输入：缺失或重复 --index、缺失或重复 --change、
    新文本为空或仅空白、版本名为空或仅空白，均优先报 Invalid draft，
    退出码 1、标准输出为空，且不创建数据库文件。

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
# 与一条独立文本。
VERSION = "demo-0.1"
TITLE = "预览版"
CHANGES = ["新增预览", "新增预览", "改善提示"]

# 删除第二条再替换新第二条后的期望 show 输出（按 README 固定格式独立写明）。
EXPECTED_SHOW_FINAL = (
    "Version: demo-0.1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 修订提示\n"
)

# 同一状态下的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_FINAL = (
    "# demo-0.1\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "- 修订提示\n"
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


class ChangeFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self, changes=CHANGES):
        """按固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, changes)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestRemoveThenSetChangeFlow(ChangeFlowTestCase):
    def test_remove_02_then_set_2_then_show_and_export(self):
        self.create_demo_01()

        # 即 python release_notes.py --db notes.sqlite remove-change demo-0.1 \
        #   --index 02
        # 前导零不影响定位，输出序号不保留前导零。
        code, out, err = run_cli(
            self.db, "remove-change", VERSION, "--index", "02"
        )
        self.assertEqual(code, 0, "remove-change 退出码应为 0")
        self.assertEqual(err, "", "remove-change 标准错误应为空")
        self.assertEqual(
            out,
            "Removed change: demo-0.1 #2\n",
            "remove-change 标准输出应恰为 Removed change: demo-0.1 #2 加末尾换行",
        )

        # 删除后序号按剩余记录重新计数：新序号 2 定位原第三条"改善提示"。
        code, out, err = run_cli(
            self.db, "set-change", VERSION, "--index", "2",
            "--change", "修订提示",
        )
        self.assertEqual(code, 0, "set-change 退出码应为 0")
        self.assertEqual(err, "", "set-change 标准错误应为空")
        self.assertEqual(
            out,
            "Updated change: demo-0.1 #2\n",
            "set-change 标准输出应恰为 Updated change: demo-0.1 #2 加末尾换行",
        )

        # 两个相互独立的新进程查看，结果应逐字相同（含末尾换行）。
        first = show(self.db, VERSION)
        second = show(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程查看结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_FINAL,
            "删除第二条并替换新第二条后应只剩新增预览与修订提示",
        )

        # 两个相互独立的新进程导出，结果应逐字相同（含末尾换行）。
        first = export_markdown(self.db, VERSION)
        second = export_markdown(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程导出结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_FINAL,
            "导出文本应精确匹配固定格式：只含新增预览与修订提示两条变更",
        )


class TestOutOfRangePrecedence(ChangeFlowTestCase):
    def test_out_of_range_reported_before_last_change_guard(self):
        # 只剩一条变更的草稿：越界序号仍先报条目不存在。
        self.create_demo_01(changes=["唯一变更"])

        code, out, err = run_cli(
            self.db, "remove-change", VERSION, "--index", "2"
        )
        self.assertEqual(code, 1, "越界序号退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #2\n",
            "越界应优先于 Cannot remove last change 报条目不存在",
        )

        # 该记录原样保留。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 show 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: demo-0.1\nTitle: 预览版\n- 唯一变更\n",
            "越界的失败删除不得改变仅剩的记录",
        )

    def test_huge_valid_index_is_out_of_range(self):
        self.create_demo_01()

        # 极大的合法数值序号同样按越界返回，消息中的序号不保留前导零。
        huge = "9" * 23
        code, out, err = run_cli(
            self.db, "remove-change", VERSION,
            "--index", "000" + huge,
        )
        self.assertEqual(code, 1, "极大序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Change not found: demo-0.1 #{huge}\n",
            "极大合法序号应按越界处理，消息中的序号不保留前导零",
        )

        code, out, err = run_cli(
            self.db, "set-change", VERSION,
            "--index", huge, "--change", "不应写入",
        )
        self.assertEqual(code, 1, "极大序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Change not found: demo-0.1 #{huge}\n",
            "set-change 的极大合法序号同样按越界处理",
        )

        # 失败后内容保持不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后导出退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n- 新增预览\n- 新增预览\n- 改善提示\n",
            "越界的失败操作不得改变已有内容",
        )


class TestSetChangeInvalidInput(ChangeFlowTestCase):
    """输入校验优先于版本查找：无效输入一律 Invalid draft，且不创建数据库。"""

    def assert_invalid(self, *args, version=VERSION):
        code, out, err = run_cli(self.db, "set-change", version, *args)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )

    def test_missing_index(self):
        self.assert_invalid("--change", "新文本")

    def test_duplicate_index(self):
        self.assert_invalid(
            "--index", "1", "--index", "2", "--change", "新文本"
        )

    def test_missing_change(self):
        self.assert_invalid("--index", "1")

    def test_duplicate_change(self):
        self.assert_invalid(
            "--index", "1", "--change", "新文本", "--change", "另一条"
        )

    def test_blank_change(self):
        self.assert_invalid("--index", "1", "--change", "")
        self.assert_invalid("--index", "1", "--change", "  \n\t ")

    def test_blank_version(self):
        self.assert_invalid("--index", "1", "--change", "新文本", version="")
        self.assert_invalid(
            "--index", "1", "--change", "新文本", version="  \n\t "
        )

    def test_bad_index_values(self):
        for bad in ("0", "00", "abc", "-1", "1.5", " 1", "1 ", "+1", "1a"):
            with self.subTest(index=bad):
                self.assert_invalid("--index", bad, "--change", "新文本")


if __name__ == "__main__":
    unittest.main()
