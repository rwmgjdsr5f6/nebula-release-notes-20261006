"""release_notes.py rename-version 缺少 drafts 表边界的离线回归测试。

背景：rename-version 已能处理数据库文件不存在的情况，但对已存在却缺少
drafts 表的合法数据库（完全没有任何表，或只有 unrelated 一类无关表）会
直接查询 drafts 表而抛出 "no such table: drafts"，向标准错误暴露 SQLite
异常。修复后统一按原版本不存在处理。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 在两类预先准备好的数据库上调用 rename-version v1 --to v2：退出码 1、
    标准输出为空、标准错误恰为 "Version not found: v1\\n"，不含异常堆栈
    或额外提示；目标与原名相同（--to v1）同样返回原版本不存在；
  - 失败完全不写入：调用前后数据库文件逐字节一致，表结构
    （sqlite_master 全部行）与原有示例数据完全一致，不补建 drafts /
    changes，临时目录不出现 journal、wal 等附属文件；
  - 错误消息中的原名按输入原文保留中文与首尾空格，版本匹配仍区分大小写；
  - 输入校验优先于数据库检查：原名为空或仅含空白，或 --to 未提供、缺值、
    为空、仅含空白、重复提供时，即使数据库缺表或路径不存在也统一报
    "Invalid draft"，且不创建数据库、不改动已有数据库；
  - 正常路径保持现状：样例 v1（标题"预览版"，两条重复的"新增预览"加一条
    多行变更）改名为 v2 后退出 0、标准错误为空、标准输出恰为
    "Renamed version: v1 -> v2\\n"；新进程查看新名时标题、原文、数量与
    顺序不变，旧名仍报 Version not found: v1。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，重复执行结果一致。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 缺表边界固定样例：原名 v1，目标名 v2。
VERSION = "v1"
NEW_VERSION = "v2"

# 正常路径固定样例：标题"预览版"，两条重复文本加一条两行组成的多行文本。
TITLE = "预览版"
CHANGES = ["新增预览", "新增预览", "修正提示\n保留原文"]

# 改名后新进程查看新名的期望输出（按 README 固定格式独立写明）。
EXPECTED_SHOW_V2 = (
    "Version: v2\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修正提示\n"
    "保留原文\n"
)

# unrelated 表中的示例数据，改名失败后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的名称。
BLANK_NAMES = ["", "   ", " \n\t "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、标点与空格精确可比。
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


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。

    返回 (sqlite_master 行列表, {表名: 行列表})，按固定顺序排列，
    用于核对改名前后库结构与数据完全一致。
    """
    conn = sqlite3.connect(db_path)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
            " ORDER BY type, name"
        ).fetchall()
        tables = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " ORDER BY name"
        ):
            # sqlite 内部序列表（sqlite_sequence 等）也按内容一并核对。
            tables[name] = conn.execute(
                f'SELECT * FROM "{name}"'
            ).fetchall()
    finally:
        conn.close()
    return master, tables


class DatabaseFixture:
    """共享的临时目录、建库与核对工具；不定义测试方法，不被 unittest 收集。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-rename-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def read_database_bytes(self):
        with open(self.db, "rb") as handle:
            return handle.read()

    def build_empty_sqlite_database(self):
        """建立不含任何表的合法 SQLite 数据库（具备文件头）。"""
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE _init (x INTEGER)")
            conn.execute("DROP TABLE _init")
            conn.commit()
        finally:
            conn.close()

    def build_unrelated_table_database(self):
        """建立只有 unrelated 表且保存一行示例数据的数据库。"""
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)"
            )
            conn.executemany(
                "INSERT INTO unrelated(id, note) VALUES (?, ?)",
                UNRELATED_ROWS,
            )
            conn.commit()
        finally:
            conn.close()

    def assert_version_not_found(self, result, version=VERSION):
        """核对缺表改名边界：退出 1、空 stdout、固定单行 stderr、无堆栈。"""
        code, out, err = result
        self.assertEqual(code, 1, "缺少 drafts 表时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {version}\n",
            "标准错误应恰为 Version not found 加一个换行，原名保留原文",
        )
        self.assertNotIn("Traceback", err, "标准错误不得包含异常堆栈")
        self.assertNotIn("sqlite3", err, "标准错误不得暴露底层异常")
        self.assertNotIn("OperationalError", err, "标准错误不得暴露底层异常")

    def assert_invalid_draft(self, result):
        """核对输入校验：退出 1、空 stdout、stderr 恰为 Invalid draft。"""
        code, out, err = result
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加一个换行"
        )

    def assert_database_unchanged(self, bytes_before, snapshot_before):
        """文件字节、表结构、各表数据与目录文件集合均保持不变。"""
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "失败改名前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(self.db)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "失败改名前后表结构与原有数据必须完全一致",
        )
        master_after = snapshot_after[0]
        table_names = {row[1] for row in master_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "失败改名不得产生任何附属文件",
        )


class TestRenameVersionMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：rename-version 的缺表边界。"""

    def run_missing_table_boundary(self, build_database, case_label):
        """两类数据库共用的缺表改名边界核对流程。"""
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        # 固定小样例：rename-version v1 --to v2。
        with self.subTest(case=case_label):
            self.assert_version_not_found(
                rename_version(self.db, VERSION, "--to", NEW_VERSION)
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_empty_sqlite_database_without_any_table(self):
        # 完全没有表的合法数据库也必须得到确定的业务结果。
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database, "empty"
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库改名失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库改名失败后仍不应有任何表数据")

    def test_database_with_only_unrelated_table_and_sample_row(self):
        self.run_missing_table_boundary(
            self.build_unrelated_table_database, "unrelated"
        )
        # 额外核对 unrelated 的建表语句与示例行原样保留。
        master, tables = snapshot_database(self.db)
        self.assertEqual(
            [row[1] for row in master], ["unrelated"], "只剩 unrelated 表"
        )
        self.assertEqual(
            tables["unrelated"],
            UNRELATED_ROWS,
            "unrelated 表的示例数据必须原样保留",
        )

    def test_same_name_rename_on_missing_table_still_not_found(self):
        # 目标与原名相同也返回原版本不存在，数据库保持不变。
        self.build_unrelated_table_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        self.assert_version_not_found(
            rename_version(self.db, VERSION, "--to", VERSION)
        )
        self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_original_name_preserved_verbatim_in_message(self):
        # 原名含中文与首尾空格时按输入原文保留在错误消息中，不裁剪；
        # 缺表库上任何原名都按版本不存在处理。
        self.build_unrelated_table_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        for name in (" V1 预览版 ", "V1"):
            with self.subTest(name=name):
                self.assert_version_not_found(
                    rename_version(self.db, name, "--to", NEW_VERSION),
                    version=name,
                )
        self.assert_database_unchanged(bytes_before, snapshot_before)


class TestValidationPriorityOnMissingTables(DatabaseFixture, unittest.TestCase):
    """缺表库与不存在路径上的输入校验优先级：先校验，后查库。"""

    def test_invalid_input_reports_invalid_draft_before_database_check(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes()
                snapshot_before = snapshot_database(self.db)

                # 原版本名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        rename_version(self.db, name, "--to", NEW_VERSION)
                    )
                # --to 未提供。
                self.assert_invalid_draft(rename_version(self.db, VERSION))
                # --to 显式给出却缺值。
                self.assert_invalid_draft(
                    rename_version(self.db, VERSION, "--to")
                )
                # --to 值为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        rename_version(self.db, VERSION, "--to", name)
                    )
                # --to 重复提供。
                self.assert_invalid_draft(
                    rename_version(self.db, VERSION, "--to", "v2", "--to", "v3")
                )

                self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_invalid_input_on_missing_path_does_not_create_database(self):
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        # 路径不存在时无效输入同样优先报 Invalid draft，且不创建任何文件。
        self.assert_invalid_draft(rename_version(db_path, VERSION, "--to"))
        self.assert_invalid_draft(
            rename_version(db_path, "", "--to", NEW_VERSION)
        )
        self.assertFalse(
            os.path.exists(db_path), "无效输入不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestRenameVersionNormalPathKept(DatabaseFixture, unittest.TestCase):
    """drafts 表存在时既有行为保持不变：成功改名与旧名失效。"""

    def test_rename_then_show_new_name_and_old_name_gone(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, "Created v1\n", ""))

        # 即 python release_notes.py --db notes.sqlite rename-version v1 --to v2
        code, out, err = rename_version(self.db, VERSION, "--to", NEW_VERSION)
        self.assertEqual(code, 0, "rename-version 退出码应为 0")
        self.assertEqual(err, "", "rename-version 标准错误应为空")
        self.assertEqual(
            out,
            "Renamed version: v1 -> v2\n",
            "rename-version 标准输出应恰为 Renamed version: v1 -> v2"
            " 加末尾换行",
        )

        # 新进程查看新名称：标题与三条原文随版本名一起保留，重复条目分别
        # 保留，多行文本仍是一条记录，顺序不变。
        code, out, err = run_cli(self.db, "show", NEW_VERSION)
        self.assertEqual(code, 0, "show v2 退出码应为 0")
        self.assertEqual(err, "", "show v2 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_V2,
            "改名后标题与变更应仍属于同一份草稿：新版本名、原标题、"
            "三条原文按原顺序输出",
        )

        # 原名称不再存在。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual(code, 1, "show v1 退出码应为 1")
        self.assertEqual(out, "", "show v1 标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "show v1 标准错误应恰为 Version not found: v1 加末尾换行",
        )


if __name__ == "__main__":
    unittest.main()
