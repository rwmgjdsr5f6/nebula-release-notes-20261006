"""release_notes.py set-change / remove-change 共用流程重构后的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 验收链路：在固定样例草稿（demo-0.1，标题"预览版"，三条变更依次为
    新增预览、新增预览、改善提示）上先以带前导零的序号 02 删除第二条，
    再按删除后的新序号 2 把"改善提示"替换为"修订提示"；每步退出码 0、
    标准错误为空、标准输出恰为 README 规定的消息加换行；随后由新进程
    查看与导出，结果均为只剩"新增预览"与"修订提示"，标题原样保留，
    且两次独立进程的查看、导出结果逐字相同。
  - 极大合法序号：版本存在时，远超条目总数的纯数字序号（含前导零形式）
    仍按越界处理，报 Change not found 且序号不保留前导零，已有内容不变。
  - set-change 输入校验：缺失或重复 --index、缺失或重复 --change、
    新文本仅空白、版本名仅空白均优先报 Invalid draft，退出码 1、
    标准输出为空，不创建数据库、不改变已有草稿。

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

# 验收链路完成后的期望 show 输出（按 README 固定格式独立写明）。
EXPECTED_SHOW_FINAL = (
    "Version: demo-0.1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 修订提示\n"
)

# 验收链路完成后的期望导出文本（按 README 固定格式独立写明）。
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

    def create_demo_01(self):
        """按固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestRemoveThenSetChain(ChangeFlowTestCase):
    def test_remove_02_then_set_2_then_fresh_process_show_and_export(self):
        self.create_demo_01()

        # 即 python release_notes.py --db notes.sqlite remove-change demo-0.1 \
        #   --index 02
        # 前导零不影响定位，输出序号不保留前导零；重复文本只定位目标一条。
        code, out, err = run_cli(
            self.db, "remove-change", VERSION, "--index", "02"
        )
        self.assertEqual(code, 0, "remove-change 退出码应为 0")
        self.assertEqual(err, "", "remove-change 标准错误应为空")
        self.assertEqual(out, "Removed change: demo-0.1 #2\n")

        # 删除后按剩余记录重新计数：新进程查看应只剩新增预览与改善提示。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "删除后 show 退出码应为 0")
        self.assertEqual(err, "", "删除后 show 标准错误应为空")
        self.assertEqual(
            out,
            "Version: demo-0.1\nTitle: 预览版\n- 新增预览\n- 改善提示\n",
            "删除第二条后应只剩新增预览与改善提示",
        )

        # 即 python release_notes.py --db notes.sqlite set-change demo-0.1 \
        #   --index 2 --change 修订提示
        # 序号按删除后的剩余记录计数，只改目标记录。
        code, out, err = run_cli(
            self.db, "set-change", VERSION, "--index", "2",
            "--change", "修订提示",
        )
        self.assertEqual(code, 0, "set-change 退出码应为 0")
        self.assertEqual(err, "", "set-change 标准错误应为空")
        self.assertEqual(out, "Updated change: demo-0.1 #2\n")

        # 两个相互独立的新进程查看，结果应逐字相同（含末尾换行）。
        first = show(self.db, VERSION)
        second = show(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程查看结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "最终 show 退出码应为 0")
        self.assertEqual(err, "", "最终 show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_FINAL,
            "最终应只剩新增预览与修订提示，标题原样保留",
        )

        # 两个相互独立的新进程导出，结果应逐字相同且符合固定格式。
        first = export_markdown(self.db, VERSION)
        second = export_markdown(self.db, VERSION)
        self.assertEqual(first, second, "两次独立进程导出结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "最终 export-markdown 退出码应为 0")
        self.assertEqual(err, "", "最终 export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_FINAL,
            "导出文本应精确匹配固定格式：只含新增预览与修订提示",
        )


class TestHugeIndexOutOfRange(ChangeFlowTestCase):
    """版本存在时，极大的合法序号也按越界返回，且不改变任何草稿。"""

    def assert_huge_index_out_of_range(self, command, extra_args=()):
        self.create_demo_01()
        huge = "9" * 100
        code, out, err = run_cli(
            self.db, command, VERSION, "--index", "0" + huge, *extra_args
        )
        self.assertEqual(code, 1, "极大序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Change not found: demo-0.1 #{huge}\n",
            "标准错误应恰为 Change not found 加末尾换行（序号不保留前导零）",
        )

        # 失败后内容保持不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "失败后导出退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n- 新增预览\n- 新增预览\n- 改善提示\n",
            "极大序号的失败操作不得改变已有内容",
        )

    def test_set_change_huge_index(self):
        self.assert_huge_index_out_of_range(
            "set-change", extra_args=("--change", "不应写入")
        )

    def test_remove_change_huge_index(self):
        self.assert_huge_index_out_of_range("remove-change")


class TestSetChangeInvalidInput(ChangeFlowTestCase):
    """输入校验优先于版本查找：无效输入一律 Invalid draft。"""

    def assert_invalid(self, *args, version=VERSION, expect_db=False):
        code, out, err = run_cli(self.db, "set-change", version, *args)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertEqual(
            os.path.exists(self.db),
            expect_db,
            "无效输入不得创建或删除数据库文件",
        )

    def test_missing_or_duplicate_index(self):
        # 数据库尚不存在：无效输入优先报 Invalid draft，且不创建数据库。
        self.assert_invalid("--change", "新文本")
        self.assert_invalid("--index", "1", "--index", "2", "--change", "新文本")

    def test_missing_or_duplicate_change(self):
        self.assert_invalid("--index", "1")
        self.assert_invalid(
            "--index", "1", "--change", "甲", "--change", "乙"
        )

    def test_blank_version(self):
        self.assert_invalid("--index", "1", "--change", "新文本", version="")
        self.assert_invalid(
            "--index", "1", "--change", "新文本", version="  \n\t "
        )

    def test_bad_index_values(self):
        for bad in ("0", "00", "abc", "-1", "1.5", " 1", "1 ", "+1", "1a"):
            with self.subTest(index=bad):
                self.assert_invalid("--index", bad, "--change", "新文本")

    def test_invalid_input_leaves_existing_draft_unchanged(self):
        # 版本已存在时无效输入仍优先报 Invalid draft，且已有内容不变。
        self.create_demo_01()
        self.assert_invalid(
            "--index", "2", "--change", "  \n\t ", expect_db=True
        )
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 show 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: demo-0.1\nTitle: 预览版\n"
            "- 新增预览\n- 新增预览\n- 改善提示\n",
            "无效输入不得改变已有草稿",
        )


if __name__ == "__main__":
    unittest.main()
