"""release_notes.py rename-version 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（v0.1，标题"预览版"，三条变更依次为两条
    相同的"新增预览"及一条由"修正提示"、一个换行和"继续预览"组成的
    多行文本，另有一个独立版本 v0.9）上执行 rename-version v0.1 --to
    v0.2；退出码 0、标准错误为空、标准输出恰为
    "Renamed version: v0.1 -> v0.2" 加换行。随后在新进程中 show v0.2
    完整展示原标题与三个条目（数量、顺序、重复文本与多行原文不变），
    show v0.1 报 Version not found；list-drafts 只显示新名称并继续
    遵守排序与前缀筛选规则；export-markdown 只改变版本标题行，其他
    文本保持原样；另一版本 v0.9 不受影响。原名与新名完全相同也按成功
    处理，不新增记录。新名称含首尾空格、中文、标点及内部换行时按原文
    原样保存。
  - 失败路径：未提供 --to、--to 缺值、重复提供 --to、原名称或新名称
    为空 / 仅空白均报 Invalid draft，即使数据库或原版本不存在也优先
    返回此结果，且不创建数据库；数据库文件不存在或原版本不存在报
    Version not found；不同的新名称已属于另一份草稿报
    Version already exists，两份草稿均保持不变。所有失败退出码 1、
    标准输出为空、错误末尾带换行，不创建数据库、不补建草稿、不留
    部分重命名。

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

# 固定样例：版本 v0.1，标题"预览版"，三条变更依次为两条重复文本与一条
# 多行文本（"修正提示" + 一个换行 + "继续预览"）；另有一个独立版本 v0.9
# 用于验证互不影响与冲突检测。
VERSION = "v0.1"
TITLE = "预览版"
MULTILINE_CHANGE = "修正提示\n继续预览"
CHANGES = ["新增预览", "新增预览", MULTILINE_CHANGE]

OTHER_VERSION = "v0.9"
OTHER_TITLE = "正式版"
OTHER_CHANGES = ["首个变更"]

NEW_VERSION = "v0.2"

# 重命名后的期望 show 输出（按 README 固定格式独立写明）：标题与三个
# 条目原样保留，多行条目内部换行不变，仅版本名行为新名称。
EXPECTED_SHOW_AFTER_RENAME = (
    "Version: v0.2\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修正提示\n"
    "继续预览\n"
)

# 另一版本的期望 show 输出：不受 v0.1 重命名影响。
EXPECTED_SHOW_OTHER = (
    "Version: v0.9\n"
    "Title: 正式版\n"
    "- 首个变更\n"
)

# 重命名后的期望导出文本（按 README 固定格式独立写明）：只改变版本
# 标题行，其他文本保持原样。
EXPECTED_EXPORT_AFTER_RENAME = (
    "# v0.2\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修正提示\n"
    "继续预览\n"
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


class RenameVersionTestCase(unittest.TestCase):
    """每个测试使用独立临时目录，预先创建样例草稿与独立版本。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db = os.path.join(self.tmpdir.name, "notes.db")

    def create_samples(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))
        code, out, err = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual(
            (code, out, err), (0, f"Created {OTHER_VERSION}\n", "")
        )

    def rename(self, *args):
        return run_cli(self.db, "rename-version", *args)

    def test_rename_success_then_all_entries_use_new_name(self):
        self.create_samples()

        code, out, err = self.rename(VERSION, "--to", NEW_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, f"Renamed version: {VERSION} -> {NEW_VERSION}\n")

        # 新进程中 show 新名称：原标题与三个条目完整保留，顺序、重复
        # 文本与多行原文不变。
        code, out, err = run_cli(self.db, "show", NEW_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_AFTER_RENAME)

        # 旧名称不再指向该草稿。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"Version not found: {VERSION}\n")

        # 目录查询只显示新名称，排序与另一版本保持原有规则。
        code, out, err = run_cli(self.db, "list-drafts")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            '[{"version": "v0.2", "title": "预览版"}, '
            '{"version": "v0.9", "title": "正式版"}]\n',
        )

        # 前缀筛选继续按新名称原文匹配。
        code, out, err = run_cli(self.db, "list-drafts", "--prefix", "v0.2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, '[{"version": "v0.2", "title": "预览版"}]\n')
        code, out, err = run_cli(self.db, "list-drafts", "--prefix", "v0.1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "[]\n")

        # Markdown 导出只改变版本标题行，其他文本保持原样。
        code, out, err = run_cli(self.db, "export-markdown", NEW_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_EXPORT_AFTER_RENAME)

        # 另一版本不受影响。
        code, out, err = run_cli(self.db, "show", OTHER_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_OTHER)

        # 新名称可用于现有编辑入口：重命名后追加变更仍落在新名称上。
        code, out, err = run_cli(
            self.db, "add-change", NEW_VERSION, "--change", "追加条目"
        )
        self.assertEqual((code, out, err), (0, f"Added change: {NEW_VERSION}\n", ""))
        code, out, err = run_cli(self.db, "show", NEW_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_RENAME.rstrip("\n") + "\n- 追加条目\n",
        )

    def test_rename_to_same_name_succeeds_without_new_record(self):
        self.create_samples()

        code, out, err = self.rename(VERSION, "--to", VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, f"Renamed version: {VERSION} -> {VERSION}\n")

        # 不新增记录：目录仍只有原来的两个版本，内容不变。
        code, out, err = run_cli(self.db, "list-drafts")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            '[{"version": "v0.1", "title": "预览版"}, '
            '{"version": "v0.9", "title": "正式版"}]\n',
        )
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 修正提示\n"
            "继续预览\n",
        )

    def test_rename_to_name_with_spaces_and_newline_kept_verbatim(self):
        self.create_samples()
        new_name = " 预览 v0.2\n续行 "

        code, out, err = self.rename(VERSION, "--to", new_name)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out, f"Renamed version: {VERSION} -> {new_name}\n"
        )

        # 新名称按原文原样保存：不裁剪空格、不转义内部换行。
        code, out, err = run_cli(self.db, "show", new_name)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            f"Version: {new_name}\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 修正提示\n"
            "继续预览\n",
        )

    def test_invalid_input_rejected_before_any_database_access(self):
        # 数据库文件不存在时，各类无效输入仍优先报 Invalid draft，
        # 且不创建数据库。
        cases = [
            (VERSION,),                      # 未提供 --to
            (VERSION, "--to"),               # --to 缺值
            (VERSION, "--to", "a", "--to", "b"),  # --to 重复
            (VERSION, "--to", ""),           # 新名称为空
            (VERSION, "--to", "  \n "),      # 新名称仅含空白
            ("", "--to", NEW_VERSION),       # 原名称为空
            (" \t", "--to", NEW_VERSION),    # 原名称仅含空白
        ]
        for args in cases:
            with self.subTest(args=args):
                code, out, err = self.rename(*args)
                self.assertEqual((code, out, err), (1, "", "Invalid draft\n"))
                self.assertFalse(os.path.exists(self.db))

        # 数据库存在时，无效输入同样优先于版本不存在报错。
        self.create_samples()
        code, out, err = self.rename("v9.9", "--to")
        self.assertEqual((code, out, err), (1, "", "Invalid draft\n"))

    def test_version_not_found(self):
        # 数据库文件不存在：不创建数据库。
        code, out, err = self.rename(VERSION, "--to", NEW_VERSION)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"Version not found: {VERSION}\n")
        self.assertFalse(os.path.exists(self.db))

        # 数据库存在但原版本不存在。
        self.create_samples()
        code, out, err = self.rename("v9.9", "--to", NEW_VERSION)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "Version not found: v9.9\n")

    def test_conflicting_new_name_keeps_both_drafts_unchanged(self):
        self.create_samples()

        code, out, err = self.rename(VERSION, "--to", OTHER_VERSION)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"Version already exists: {OTHER_VERSION}\n")

        # 两份草稿均保持不变。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 修正提示\n"
            "继续预览\n",
        )
        code, out, err = run_cli(self.db, "show", OTHER_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_OTHER)


if __name__ == "__main__":
    unittest.main()
