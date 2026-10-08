"""release_notes.py rename-version 后再 export-markdown 的离线回归测试。

覆盖范围（仅通过命令行公开接口验收：只核对退出码与 stdout/stderr 的完整
UTF-8 文本，不调用产品函数、不直接读取 SQLite 数据，期望文本均按 README
公开的固定 Markdown 格式事先独立写明，不从实际输出反推）：
  - 成功路径：在固定样例草稿（v1，标题 " 预览说明 "，首尾各一个空格；三条
    变更依次为 "新增预览"、"新增预览" 与由 "第一行"、"第二行" 组成的两行
    文本）上，通过同一 --db 路径执行 rename-version v1 --to v2：退出码 0、
    标准错误为空、标准输出恰为 "Renamed version: v1 -> v2" 加一个末尾换行；
    随后在新的进程中执行 export-markdown v2，退出码 0、标准错误为空，标准
    输出恰为固定格式全文——"# v2" 加两个换行、原标题原文加两个换行，再按
    原顺序为每条原文加 "- " 前缀和一个末尾换行：重复条目分别出现，多行条目
    的第二行不带前缀，标题首尾空格与内部换行原样保留。导出旧名 v1 时退出码
    为 1、标准输出为空、标准错误恰为 "Version not found: v1" 加一个换行。
  - 冲突路径：独立样例中预先保存 v9（标题 "正式版"，仅含 "已有内容" 一条
    变更）与同样的 v1，尝试把 v1 改名为 v9：退出码 1、标准输出为空、标准
    错误恰为 "Version already exists: v9" 加一个换行；之后分别在新进程导出
    v1 与 v9，均退出 0、标准错误为空，两份完整正文各自保持原样。

运行方式（项目根目录）：
    python -m unittest discover -s tests
也可单独执行：
    python -m unittest tests.test_rename_version_export

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取用户数据库，不联网，不生成发布说明文件，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 v1；标题 " 预览说明 " 首尾各一个空格；三条变更依次为两条
# 重复文本，以及由 "第一行" 与 "第二行" 通过恰好一个换行连接的多行文本。
VERSION = "v1"
NEW_VERSION = "v2"
TITLE = " 预览说明 "
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

# 重命名成功消息（README 固定公开行为）：
#   "Renamed version: <旧名> -> <新名>" 加一个末尾换行。
EXPECTED_RENAME_OUTPUT = "Renamed version: v1 -> v2\n"

# 重命名后的导出全文（按 README 固定 Markdown 格式独立写明）：
#   "# " + 新版本名原文 + 两个换行；
#   原标题原文（首尾各一个空格）+ 两个换行；
#   每条变更 "- " + 原文 + 一个换行，按原顺序输出。
# 两条重复文本分别出现；多行条目只在首行带 "- " 前缀，第二行 "第二行"
# 不带任何前缀；标题的首尾空格原样保留。
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

# 冲突样例：v9 是预先保存的另一份草稿，标题 "正式版"，仅含一条变更。
CONFLICT_VERSION = "v9"
CONFLICT_TITLE = "正式版"
CONFLICT_CHANGES = ["已有内容"]

# 冲突失败后导出旧名 v1 的全文：标题与三条原文均未改动，头部版本名仍为 v1。
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

# 冲突失败后导出 v9 的全文：仍是原有单条草稿，未被重命名影响。
EXPECTED_EXPORT_V9 = (
    "# v9\n"
    "\n"
    "正式版\n"
    "\n"
    "- 已有内容\n"
)


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、空格与内部换行精确可比。
    每次调用本身都是一个全新进程。
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


def rename_version(db_path, version, new_version):
    return run_cli(db_path, "rename-version", version, "--to", new_version)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


class RenameVersionExportTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录与数据库，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_main_fixture(self):
        """按固定样例创建 v1，并核对创建命令本身的公开输出。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create v1 退出码应为 0")
        self.assertEqual(out, "Created v1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")


class TestRenameThenExport(RenameVersionExportTestCase):
    def test_rename_then_export_new_name_and_old_name_not_found(self):
        self.create_main_fixture()

        # 通过同一 --db 路径执行重命名：退出码 0、标准错误为空，标准输出
        # 恰为固定成功消息加一个末尾换行。
        code, out, err = rename_version(self.db, VERSION, NEW_VERSION)
        self.assertEqual(code, 0, "rename-version 退出码应为 0")
        self.assertEqual(err, "", "rename-version 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_RENAME_OUTPUT,
            "标准输出应恰为 Renamed version: v1 -> v2 加一个末尾换行",
        )

        # 随后在新的进程中按新名称导出：退出码 0、标准错误为空，完整正文
        # 与按公开格式独立写明的文本逐字一致。
        code, out, err = export_markdown(self.db, NEW_VERSION)
        self.assertEqual(code, 0, "export-markdown v2 退出码应为 0")
        self.assertEqual(err, "", "export-markdown v2 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_V2,
            "导出正文应以 '# v2' 与两个换行开始，再接原标题与两个换行，"
            "最后按原顺序为每条原文加 '- ' 前缀和一个末尾换行；重复条目"
            "分别出现，多行条目的第二行不带前缀，标题首尾空格原样保留",
        )

        # 再次在另一个新进程导出，完整文本逐字一致（含末尾换行）。
        code2, out2, err2 = export_markdown(self.db, NEW_VERSION)
        self.assertEqual(code2, 0, "再次导出退出码应为 0")
        self.assertEqual(err2, "", "再次导出标准错误应为空")
        self.assertEqual(out2, EXPECTED_EXPORT_V2, "再次导出仍应匹配固定全文")
        self.assertEqual(out2, out, "两次独立进程导出结果应逐字相同")

        # 旧名已不存在：退出码 1、标准输出为空，标准错误恰为
        # Version not found: v1 加一个换行。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 1, "导出旧名 v1 退出码应为 1")
        self.assertEqual(out, "", "导出旧名失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "标准错误应恰为 Version not found: v1 加一个末尾换行",
        )


class TestRenameConflictLeavesExportsUnchanged(RenameVersionExportTestCase):
    def test_rename_to_existing_version_keeps_both_exports(self):
        # 独立样例：先保存 v9（标题 "正式版"，仅一条 "已有内容" 变更）。
        code, out, err = create(
            self.db, CONFLICT_VERSION, CONFLICT_TITLE, CONFLICT_CHANGES
        )
        self.assertEqual(code, 0, "create v9 退出码应为 0")
        self.assertEqual(out, "Created v9\n", "create v9 标准输出不符")
        self.assertEqual(err, "", "create v9 标准错误应为空")

        # 再创建与成功用例相同的 v1 样例。
        self.create_main_fixture()

        # 尝试把 v1 改名为已存在的 v9：退出码 1、标准输出为空，标准错误
        # 恰为 Version already exists: v9 加一个换行。
        code, out, err = rename_version(self.db, VERSION, CONFLICT_VERSION)
        self.assertEqual(code, 1, "目标名称已存在时退出码应为 1")
        self.assertEqual(out, "", "冲突失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v9\n",
            "标准错误应恰为 Version already exists: v9 加一个末尾换行",
        )

        # 之后在新进程导出 v1：退出码 0、标准错误为空，完整正文保持原样，
        # 头部版本名仍是 v1。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "冲突后导出 v1 退出码应为 0")
        self.assertEqual(err, "", "冲突后导出 v1 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_V1,
            "冲突失败后 v1 草稿导出应保持原样：标题空格、重复条目与多行"
            "条目均不变",
        )

        # 再在另一个新进程导出 v9：退出码 0、标准错误为空，完整正文仍是
        # 原有单条草稿，未被失败的重命名改动。
        code, out, err = export_markdown(self.db, CONFLICT_VERSION)
        self.assertEqual(code, 0, "冲突后导出 v9 退出码应为 0")
        self.assertEqual(err, "", "冲突后导出 v9 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_V9,
            "冲突失败后 v9 草稿导出应保持原样：正式版标题与已有内容条目"
            "均不变",
        )


if __name__ == "__main__":
    unittest.main()
