"""release_notes.py rename-version 后 export-markdown 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（v1，标题" 预览说明 "——首尾各一个空格，
    三条变更依次为 新增预览、新增预览，以及一条由"第一行"与"第二行"
    两行组成的多行变更）上执行 rename-version v1 --to v2；退出码 0、
    标准错误为空、标准输出恰为 "Renamed version: v1 -> v2" 加换行。
    随后在新的进程中执行 export-markdown v2：退出码 0、标准错误为空，
    正文以 "# v2" 加两个换行开始，再接原标题（首尾空格原样保留）和
    两个换行，最后按原顺序为每条原文加 "- " 前缀与一个末尾换行；
    重复条目分别出现，多行条目的第二行不加前缀，内部换行原样保留。
    导出旧名 v1 时退出码 1、标准输出为空、标准错误恰为
    "Version not found: v1" 加换行。
  - 冲突路径：独立样例中预先保存 v9（标题"正式版"，仅含"已有内容"
    一条变更），再将同样的 v1 尝试改名为 v9；退出码 1、标准输出为空、
    标准错误恰为 "Version already exists: v9" 加换行。之后分别导出
    v1 和 v9，均退出码 0、标准错误为空，两份完整正文各自保持原样。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推，也不调用
产品函数生成期望；比较完整 UTF-8 文本而非片段。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，不生成发布说明文件，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 v1，标题" 预览说明 "（首尾各一个空格），三条变更依次
# 为两条重复文本与一条由"第一行""第二行"两行组成的多行文本。
VERSION = "v1"
TITLE = " 预览说明 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# 重命名为 v2 后的期望导出正文（按 README 固定格式独立写明）：
# "# v2" + 两个换行；原标题（首尾空格原样保留）+ 两个换行；每条原文加
# "- " 前缀与一个末尾换行，重复条目分别出现，多行条目第二行不加前缀。
EXPECTED_EXPORT_V2 = (
    "# v2\n"
    "\n"
    " 预览说明 \n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 冲突样例：v9 是另一份独立草稿，标题"正式版"，仅含"已有内容"一条变更。
CONFLICT_VERSION = "v9"
CONFLICT_TITLE = "正式版"
CONFLICT_CHANGES = ["已有内容"]

# 冲突失败后两份草稿各自保持原样的期望导出正文。
EXPECTED_EXPORT_V1 = (
    "# v1\n"
    "\n"
    " 预览说明 \n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

EXPECTED_EXPORT_V9 = (
    "# v9\n"
    "\n"
    "正式版\n"
    "\n"
    "- 已有内容\n"
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


def rename_version(db_path, version, *rename_args):
    return run_cli(db_path, "rename-version", version, *rename_args)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class RenameVersionExportTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_main_fixture(self):
        """按固定样例创建 v1（标题首尾各一个空格，三条变更）。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create v1 退出码应为 0")
        self.assertEqual(out, "Created v1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def create_conflict_fixture(self):
        """创建标题"正式版"、仅含"已有内容"一条变更的 v9。"""
        code, out, err = create(
            self.db, CONFLICT_VERSION, CONFLICT_TITLE, CONFLICT_CHANGES
        )
        self.assertEqual(code, 0, "create v9 退出码应为 0")
        self.assertEqual(out, "Created v9\n", "create v9 标准输出不符")
        self.assertEqual(err, "", "create v9 标准错误应为空")


class TestRenameThenExport(RenameVersionExportTestCase):
    def test_rename_then_export_new_name_and_old_name_gone(self):
        self.create_main_fixture()

        # 即 python release_notes.py --db notes.sqlite rename-version v1 --to v2
        code, out, err = rename_version(self.db, VERSION, "--to", "v2")
        self.assertEqual(code, 0, "rename-version 退出码应为 0")
        self.assertEqual(err, "", "rename-version 标准错误应为空")
        self.assertEqual(
            out,
            "Renamed version: v1 -> v2\n",
            "rename-version 标准输出应恰为 Renamed version: v1 -> v2"
            " 加末尾换行",
        )

        # 新的进程中导出新名称：标题首尾空格、三条原文与顺序随版本名一起
        # 保留，重复条目分别出现，多行条目第二行不加前缀。
        code, out, err = export_markdown(self.db, "v2")
        self.assertEqual(code, 0, "export-markdown v2 退出码应为 0")
        self.assertEqual(err, "", "export-markdown v2 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_V2,
            "改名后导出正文应为 # v2、原标题（首尾空格原样保留）与三条"
            "原文按原顺序加前缀输出",
        )

        # 旧名称不再存在。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 1, "export-markdown v1 退出码应为 1")
        self.assertEqual(out, "", "export-markdown v1 标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "export-markdown v1 标准错误应恰为 Version not found: v1"
            " 加末尾换行",
        )


class TestRenameConflictThenExport(RenameVersionExportTestCase):
    def test_rename_to_existing_version_keeps_both_exports(self):
        self.create_main_fixture()
        self.create_conflict_fixture()

        code, out, err = rename_version(self.db, VERSION, "--to", "v9")
        self.assertEqual(code, 1, "目标名称已存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v9\n",
            "标准错误应恰为 Version already exists: v9 加末尾换行",
        )

        # 两份草稿各自保持原样：分别导出 v1 与 v9，完整正文不变。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "export-markdown v1 退出码应为 0")
        self.assertEqual(err, "", "export-markdown v1 标准错误应为空")
        self.assertEqual(
            out, EXPECTED_EXPORT_V1, "v1 导出正文应保持不变"
        )

        code, out, err = export_markdown(self.db, CONFLICT_VERSION)
        self.assertEqual(code, 0, "export-markdown v9 退出码应为 0")
        self.assertEqual(err, "", "export-markdown v9 标准错误应为空")
        self.assertEqual(
            out, EXPECTED_EXPORT_V9, "v9 导出正文应保持不变"
        )


if __name__ == "__main__":
    unittest.main()
