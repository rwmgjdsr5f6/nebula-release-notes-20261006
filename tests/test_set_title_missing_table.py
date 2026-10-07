"""release_notes.py set-title 缺表边界的离线回归测试。

背景：show 与 export-markdown 已把缺少 drafts 表的数据库按版本不存在
处理，而 set-title 对同类数据库（能被 SQLite 正常打开但没有 drafts 表：
完全没有任何表，或只有 unrelated 一类无关表）曾直接执行 UPDATE 而抛出
"no such table: drafts"，向标准错误暴露异常堆栈。修复后应统一按
"没有可修订的版本"处理。

覆盖范围（仅通过命令行与外部可观察行为验收）：
  - 在两类预先准备好的数据库上调用 set-title demo-0.9 --title "新标题"：
    退出码 1、标准输出为空、标准错误恰为 "Version not found: demo-0.9\\n"，
    不含异常堆栈；失败后不补建 drafts / changes、不插入草稿，原表结构
    与原数据逐字节不变，临时目录不出现 journal、wal 等附属文件；
  - 数据库路径不存在时返回同一版本不存在结果，且不创建文件；
  - 输入校验优先于数据库检查：版本名或新标题为空、仅含空白，或未提供
    --title 时，即使数据库缺表也报 "Invalid draft\\n"，退出码 1、标准
    输出为空，数据库保持不变；
  - drafts 表存在但查无版本时，保留既有版本不存在结果；
  - 正常路径：按版本名原文精确匹配并区分大小写，成功时退出 0、标准错误
    为空、标准输出恰为 "Updated title: <版本名>\\n"；再次提交相同标题仍
    成功；中文、首尾空格与内部换行原样保存；变更数量、重复条目、展示
    顺序及其他版本不受影响；重新打开同一路径后 show 可见新标题。

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

MISSING_VERSION = "demo-0.9"
NEW_TITLE = "新标题"

# unrelated 表中的示例数据，修订失败后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 正常路径固定样例：两个版本，demo-0.1 带两条相同文本与一条多行变更，
# demo-0.2 用于核对其他版本不受影响。
VERSION = "demo-0.1"
OTHER_VERSION = "demo-0.2"
TITLE = "预览说明"
OTHER_TITLE = "正式说明"
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]
OTHER_CHANGES = ["修正示例"]

# 空字符串与仅含空白（空格、制表符、换行）的版本名 / 标题。
BLANK_TEXTS = ["", "   ", " \n\t "]


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


def set_title(db_path, version, *extra):
    return run_cli(db_path, "set-title", version, *extra)


def create(db_path, version, title, changes):
    args = ["create", version, "--title", title]
    for change in changes:
        args += ["--change", change]
    return run_cli(db_path, *args)


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。

    返回 (sqlite_master 行列表, {表名: 行列表})，按固定顺序排列，
    用于核对操作前后库结构与数据完全一致。
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
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-set-title-")
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

    def assert_version_not_found(self, result, version=MISSING_VERSION):
        """核对缺表修订边界：退出 1、空 stdout、固定单行 stderr、无堆栈。"""
        code, out, err = result
        self.assertEqual(code, 1, "缺少 drafts 表时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {version}\n",
            "标准错误应恰为 Version not found 加一个换行",
        )
        self.assertNotIn("Traceback", err, "标准错误不得包含异常堆栈")
        self.assertNotIn("sqlite3", err, "标准错误不得暴露底层异常")

    def assert_database_unchanged(self, bytes_before, snapshot_before):
        """文件字节、表结构、各表数据与目录文件集合均保持不变。"""
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "失败的修订前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(self.db)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "操作前后表结构与原有数据必须完全一致",
        )
        master_after = snapshot_after[0]
        table_names = {row[1] for row in master_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 或发布说明文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "失败的修订不得产生任何附属文件",
        )


class TestSetTitleMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：set-title 统一按版本不存在处理。"""

    def run_missing_table_boundary(self, build_database, case_label):
        """两类数据库共用的缺表修订边界核对流程。"""
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        with self.subTest(case=case_label):
            self.assert_version_not_found(
                set_title(self.db, MISSING_VERSION, "--title", NEW_TITLE)
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)
        return snapshot_before

    def test_empty_sqlite_database_without_any_table(self):
        # 没有任何用户表，sqlite_sequence 等内部表也不存在。
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database, "empty"
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库失败后仍不应有任何表数据")

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

    def test_missing_path_still_not_found_without_creating_file(self):
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        code, out, err = set_title(db_path, MISSING_VERSION, "--title", NEW_TITLE)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, f"Version not found: {MISSING_VERSION}\n")
        self.assertFalse(
            os.path.exists(db_path), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestSetTitleValidationPriority(DatabaseFixture, unittest.TestCase):
    """缺表库上的输入校验优先级：Invalid draft 先于任何数据库检查。"""

    def run_invalid_draft_boundary(self, build_database, case_label, argv):
        # 每种输入在全新数据库上核对：先移除上一轮的数据库文件再重建。
        if os.path.exists(self.db):
            os.remove(self.db)
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        with self.subTest(case=case_label):
            code, out, err = run_cli(self.db, *argv)
            self.assertEqual(code, 1, "无效输入退出码应为 1")
            self.assertEqual(out, "", "失败时标准输出应为空")
            self.assertEqual(
                err, "Invalid draft\n", "无效输入应优先报 Invalid draft"
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_blank_version_reports_invalid_draft_on_missing_tables(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            for name in BLANK_TEXTS:
                self.run_invalid_draft_boundary(
                    build_database,
                    f"{case_label}-version",
                    ["set-title", name, "--title", NEW_TITLE],
                )

    def test_blank_title_reports_invalid_draft_on_missing_tables(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            for title in BLANK_TEXTS:
                self.run_invalid_draft_boundary(
                    build_database,
                    f"{case_label}-title",
                    ["set-title", MISSING_VERSION, "--title", title],
                )

    def test_missing_title_option_reports_invalid_draft_on_missing_tables(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            self.run_invalid_draft_boundary(
                build_database,
                f"{case_label}-no-title",
                ["set-title", MISSING_VERSION],
            )


class TestSetTitleExistingDraftsTable(DatabaseFixture, unittest.TestCase):
    """drafts 表存在但查无版本时，保留既有版本不存在结果。"""

    def test_unknown_version_in_existing_table(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, err), (0, ""))
        snapshot_before = snapshot_database(self.db)

        self.assert_version_not_found(
            set_title(self.db, MISSING_VERSION, "--title", NEW_TITLE)
        )
        self.assertEqual(
            snapshot_database(self.db),
            snapshot_before,
            "查无版本时既有草稿与变更必须完全不变",
        )

    def test_version_match_is_exact_and_case_sensitive(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, err), (0, ""))
        snapshot_before = snapshot_database(self.db)

        # 仅大小写不同的版本名按不存在处理，原标题不变。
        self.assert_version_not_found(
            set_title(self.db, VERSION.upper(), "--title", NEW_TITLE),
            version=VERSION.upper(),
        )
        self.assertEqual(snapshot_database(self.db), snapshot_before)


class TestSetTitleNormalPath(DatabaseFixture, unittest.TestCase):
    """正常修订路径：精确匹配、原文保存、幂等与其他版本不受影响。"""

    def setUp(self):
        super().setUp()
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, err), (0, ""))
        code, out, err = create(self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES)
        self.assertEqual((code, err), (0, ""))

    def read_drafts_and_changes(self):
        """重新打开同一路径，读取全部草稿与变更（按展示顺序）。"""
        conn = sqlite3.connect(self.db)
        try:
            drafts = conn.execute(
                "SELECT version, title FROM drafts ORDER BY version"
            ).fetchall()
            changes = conn.execute(
                "SELECT version, position, content FROM changes"
                " ORDER BY version, position"
            ).fetchall()
        finally:
            conn.close()
        return drafts, changes

    def assert_success(self, result, version=VERSION):
        code, out, err = result
        self.assertEqual(code, 0, "成功修订退出码应为 0")
        self.assertEqual(err, "", "成功时标准错误应为空")
        self.assertEqual(
            out,
            f"Updated title: {version}\n",
            "标准输出应恰为 Updated title 加一个换行",
        )

    def test_update_title_preserves_everything_else(self):
        new_title = "  新标题：第一行\n第二行  "
        self.assert_success(set_title(self.db, VERSION, "--title", new_title))

        # 中文、首尾空格与内部换行原样保存；变更数量、重复条目、展示顺序
        # 及其他版本均不受影响。
        drafts, changes = self.read_drafts_and_changes()
        self.assertEqual(
            drafts,
            [(VERSION, new_title), (OTHER_VERSION, OTHER_TITLE)],
            "新标题应按原文原样保存，其他版本标题不变",
        )
        self.assertEqual(
            changes,
            [(VERSION, position, content) for position, content in enumerate(CHANGES)]
            + [
                (OTHER_VERSION, position, content)
                for position, content in enumerate(OTHER_CHANGES)
            ],
            "变更条目的数量、重复记录、原文与展示顺序必须完全不变",
        )

        # 重新打开同一路径后 show 可见新标题（新进程）。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            f"Version: {VERSION}\n"
            f"Title: {new_title}\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 第一行\n"
            "第二行\n",
            "show 应展示修订后的标题，变更原文与展示顺序不变",
        )

    def test_resubmitting_same_title_still_succeeds(self):
        self.assert_success(set_title(self.db, VERSION, "--title", NEW_TITLE))
        snapshot_after_first = snapshot_database(self.db)

        # 再次提交相同标题仍成功，数据库内容不再改变。
        self.assert_success(set_title(self.db, VERSION, "--title", NEW_TITLE))
        self.assertEqual(
            snapshot_database(self.db),
            snapshot_after_first,
            "重复提交相同标题不应改变任何数据",
        )


if __name__ == "__main__":
    unittest.main()
