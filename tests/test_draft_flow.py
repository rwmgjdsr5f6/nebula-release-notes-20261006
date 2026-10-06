"""release_notes.py 公开命令行流程的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - create 成功：退出码、标准输出/错误，草稿内容原样持久化；
  - show 跨进程重复查看结果一致，输出与 README 格式精确匹配；
  - 重复版本创建失败且不破坏已有内容；
  - 空白变更创建失败：已有数据库不被修改、不存在的路径不产生文件；
  - 查看不存在的版本：退出码与标准错误。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

VERSION = "demo-0.1"
TITLE = " 离线示例 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# README 所述 show 输出格式：Version / Title / 每条变更 "- " 前缀，
# 多行条目内部换行原样保留，整段输出以换行结尾。
EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title:  离线示例 \n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文与空格精确可比。
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


class DraftFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按正常样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestCreateAndShow(DraftFlowTestCase):
    def test_create_success_output(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Created demo-0.1\n")
        self.assertEqual(err, "")

    def test_show_output_exact_and_stable_across_processes(self):
        self.create_demo_01()

        # 创建进程结束后，两次独立进程查看结果应完全一致且精确匹配格式。
        first = show(self.db, VERSION)
        second = show(self.db, VERSION)

        self.assertEqual(first, second, "两次独立进程 show 的结果应完全一致")
        code, out, err = first
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW,
            "show 输出应精确保留标题两侧空格、重复条目顺序及多行条目内部换行",
        )


class TestDuplicateCreate(DraftFlowTestCase):
    def test_duplicate_version_fails_and_preserves_original(self):
        self.create_demo_01()

        # 使用有效的新标题和变更再次创建同名版本。
        code, out, err = create(self.db, VERSION, "另一个标题", ["另一条变更"])
        self.assertEqual(code, 1, "重复创建退出码应为 1")
        self.assertEqual(out, "", "重复创建标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: demo-0.1\n",
            "重复创建标准错误不符",
        )

        # 再次查看仍为原始内容。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW, "重复创建后原草稿内容不应改变")


class TestInvalidCreate(DraftFlowTestCase):
    INVALID_CHANGES = {
        "empty_string": "",
        "whitespace_only": "  \n\t ",
    }

    def assert_invalid_create(self, db_path, changes, case_label):
        code, out, err = create(db_path, "demo-0.2", "有效标题", changes)
        self.assertEqual(code, 1, f"[{case_label}] 无效创建退出码应为 1")
        self.assertEqual(out, "", f"[{case_label}] 无效创建标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", f"[{case_label}] 无效创建标准错误不符"
        )

    def test_invalid_change_rejected_on_existing_database(self):
        self.create_demo_01()

        for label, invalid in self.INVALID_CHANGES.items():
            with self.subTest(case=label, database="existing"):
                # 先传有效条目，再传无效条目。
                self.assert_invalid_create(
                    self.db, ["有效条目", invalid], label
                )

        # 已有数据库中的 demo-0.1 不应被任何失败创建改变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW, "无效创建不应改变已有草稿")

        # demo-0.2 不应留下任何内容。
        code, out, err = show(self.db, "demo-0.2")
        self.assertEqual(code, 1, "查看 demo-0.2 退出码应为 1")
        self.assertEqual(out, "", "查看 demo-0.2 标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: demo-0.2\n",
            "查看 demo-0.2 标准错误不符",
        )

    def test_invalid_change_rejected_on_fresh_path_without_creating_file(self):
        for label, invalid in self.INVALID_CHANGES.items():
            with self.subTest(case=label, database="fresh"):
                fresh_db = os.path.join(self._tmpdir.name, f"fresh-{label}.sqlite")
                self.assert_invalid_create(
                    fresh_db, ["有效条目", invalid], label
                )
                self.assertFalse(
                    os.path.exists(fresh_db),
                    f"[{label}] 无效创建不得在尚不存在的路径产生数据库文件",
                )

                # 该路径上查看 demo-0.2 应为版本不存在。
                code, out, err = show(fresh_db, "demo-0.2")
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, "Version not found: demo-0.2\n")


if __name__ == "__main__":
    unittest.main()
