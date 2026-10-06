"""release_notes.py remove-change 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（demo-0.1，标题"预览版"，三条变更依次为
    新增预览、新增预览、改善提示，另有一个独立版本）上以带前导零的序号
    02 删除第二条变更；退出码 0、标准错误为空、标准输出恰为
    "Removed change: demo-0.1 #2" 加换行；随后 show 只剩新增预览与
    改善提示，标题与另一版本保持原样，新进程查看结果相同；删除后
    set-change 按新序号定位、add-change 仍追加到末尾，export-markdown
    按固定格式导出剩余条目且不产生文件。
  - 失败路径：未提供或重复提供 --index、版本名为空 / 仅空白、序号不符合
    要求（0、非数字、负数、小数）均报 Invalid draft 且不创建数据库；
    数据库文件不存在或版本不存在报 Version not found；序号越界报
    Change not found（序号去前导零）；草稿只剩一条且删除这一条报
    Cannot remove last change 且记录保留。所有失败退出码 1、标准输出
    为空、错误末尾带换行，已有数据不变。

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
# 与一条独立文本；另有一个独立版本 other-1.0 用于验证互不影响。
VERSION = "demo-0.1"
TITLE = "预览版"
CHANGES = ["新增预览", "新增预览", "改善提示"]

OTHER_VERSION = "other-1.0"
OTHER_TITLE = "正式版"
OTHER_CHANGES = ["首个变更"]

# 删除第二条后的期望 show 输出（按 README 固定格式独立写明）：
# 重复文本的另一条记录仍保留，第三条相对顺序不变。
EXPECTED_SHOW_AFTER_REMOVE = (
    "Version: demo-0.1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 改善提示\n"
)

# 另一版本的期望 show 输出：不受 demo-0.1 删除影响。
EXPECTED_SHOW_OTHER = (
    "Version: other-1.0\n"
    "Title: 正式版\n"
    "- 首个变更\n"
)

# 删除第二条后的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_AFTER_REMOVE = (
    "# demo-0.1\n"
    "\n"
    "预览版\n"
    "\n"
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


def remove_change(db_path, version, *index_args):
    return run_cli(db_path, "remove-change", version, *index_args)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class RemoveChangeTestCase(unittest.TestCase):
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


class TestRemoveChangeSuccess(RemoveChangeTestCase):
    def test_remove_second_entry_then_show_export_and_followups(self):
        self.create_fixtures()

        # 即 python release_notes.py --db notes.sqlite remove-change demo-0.1 \
        #   --index 02
        # 前导零不影响定位，输出序号不保留前导零。
        code, out, err = remove_change(self.db, VERSION, "--index", "02")
        self.assertEqual(code, 0, "remove-change 退出码应为 0")
        self.assertEqual(err, "", "remove-change 标准错误应为空")
        self.assertEqual(
            out,
            "Removed change: demo-0.1 #2\n",
            "remove-change 标准输出应恰为 Removed change: demo-0.1 #2 加末尾换行",
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
            EXPECTED_SHOW_AFTER_REMOVE,
            "删除后应只剩新增预览与改善提示：相同文本的另一条记录仍保留，"
            "其余条目原文与相对顺序不变",
        )

        # 标题与另一版本保持原样。
        code, out, err = show(self.db, OTHER_VERSION)
        self.assertEqual(code, 0, "show other-1.0 退出码应为 0")
        self.assertEqual(err, "", "show other-1.0 标准错误应为空")
        self.assertEqual(out, EXPECTED_SHOW_OTHER, "另一版本不应受影响")

        # 导出按固定格式输出剩余条目，且不产生任何文件。
        before = set(os.listdir(self._tmpdir.name))
        code, out, err = export_markdown(self.db, VERSION)
        after = set(os.listdir(self._tmpdir.name))
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_REMOVE,
            "导出文本应精确匹配固定格式：只含删除后剩余的两条变更",
        )
        self.assertEqual(before, after, "export-markdown 不应产生任何文件")

        # 删除后序号按剩余记录计算：set-change 按新序号定位第二条。
        code, out, err = run_cli(
            self.db, "set-change", VERSION, "--index", "2",
            "--change", "改善提示（修订）",
        )
        self.assertEqual(code, 0, "删除后 set-change 退出码应为 0")
        self.assertEqual(out, "Updated change: demo-0.1 #2\n")
        self.assertEqual(err, "")

        # add-change 仍追加到末尾。
        code, out, err = run_cli(
            self.db, "add-change", VERSION, "--change", "补充说明",
        )
        self.assertEqual(code, 0, "删除后 add-change 退出码应为 0")
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
            "- 改善提示（修订）\n"
            "- 补充说明\n",
            "set-change 应按删除后的新序号定位，add-change 应追加到末尾",
        )


class TestRemoveChangeInvalidInput(RemoveChangeTestCase):
    """输入校验优先于版本查找：无效输入一律 Invalid draft，且不创建数据库。"""

    def assert_invalid(self, *index_args, version=VERSION):
        code, out, err = remove_change(self.db, version, *index_args)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )

    def test_missing_index(self):
        self.assert_invalid()

    def test_duplicate_index(self):
        self.assert_invalid("--index", "1", "--index", "2")

    def test_blank_version(self):
        self.assert_invalid("--index", "1", version="")
        self.assert_invalid("--index", "1", version="  \n\t ")

    def test_bad_index_values(self):
        for bad in ("0", "00", "abc", "-1", "1.5", " 1", "1 ", "+1", "1a"):
            with self.subTest(index=bad):
                self.assert_invalid("--index", bad)


class TestRemoveChangeNotFound(RemoveChangeTestCase):
    def test_missing_database_file(self):
        code, out, err = remove_change(self.db, VERSION, "--index", "1")
        self.assertEqual(code, 1, "数据库文件不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: demo-0.1\n",
            "标准错误应恰为 Version not found: demo-0.1 加末尾换行",
        )
        self.assertFalse(
            os.path.exists(self.db), "remove-change 不得创建数据库文件"
        )

    def test_missing_version(self):
        self.create_fixtures()
        code, out, err = remove_change(self.db, "Demo-0.1", "--index", "1")
        self.assertEqual(code, 1, "版本不存在（含仅大小写不同）退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: Demo-0.1\n")

    def test_out_of_range_index(self):
        self.create_fixtures()
        code, out, err = remove_change(self.db, VERSION, "--index", "04")
        self.assertEqual(code, 1, "序号越界退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #4\n",
            "标准错误应恰为 Change not found: demo-0.1 #4 加末尾换行"
            "（序号不保留前导零）",
        )
        # 失败后内容保持不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n- 新增预览\n- 新增预览\n- 改善提示\n",
            "序号越界的失败删除不得改变已有内容",
        )


class TestRemoveLastChange(RemoveChangeTestCase):
    def test_cannot_remove_last_change(self):
        code, out, err = create(self.db, VERSION, TITLE, ["唯一变更"])
        self.assertEqual(code, 0, "create 退出码应为 0")

        code, out, err = remove_change(self.db, VERSION, "--index", "1")
        self.assertEqual(code, 1, "删除最后一条变更退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Cannot remove last change: demo-0.1\n",
            "标准错误应恰为 Cannot remove last change: demo-0.1 加末尾换行",
        )

        # 该记录原样保留。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "失败后 show 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: demo-0.1\nTitle: 预览版\n- 唯一变更\n",
            "被拒绝的删除不得改变仅剩的记录",
        )


if __name__ == "__main__":
    unittest.main()
