"""release_notes.py show / export-markdown 读取边界的离线回归测试。

背景：show 与 export-markdown 共用只读读取逻辑。对于已经存在、能被
SQLite 正常打开但没有 drafts 表的数据库（完全没有任何表，或只有
unrelated 一类无关表），修复前会直接查询 drafts 而抛出
"no such table: drafts"，向标准错误暴露异常堆栈；修复后应统一按
"没有可读取的版本"处理。

覆盖范围（仅通过命令行与外部可观察行为验收）：
  - 在两类预先准备好的数据库上分别调用 show demo-0.9 与
    export-markdown demo-0.9：退出码 1、标准输出为空、标准错误恰为
    "Version not found: demo-0.9\\n"，不含异常堆栈；
  - 查询完全只读：查询前后数据库文件逐字节一致，表结构（sqlite_master
    全部行）与原有数据完全一致，不补建 drafts / changes、不插入草稿，
    临时目录不出现 journal、wal 或发布说明等附属文件；
  - 路径不存在时 show / export-markdown 仍返回既有版本不存在错误，
    且不创建任何文件；
  - export-markdown 接收空字符串或仅含空白的版本名时，即使库中没有
    drafts 表也优先报 "Invalid draft"；show 不新增空白校验，空白版本名
    仍按既有规则报版本不存在；
  - 正常路径：create 保存 demo-0.1（标题“预览说明”，两条相同的“新增预览”
    加一条内部含换行的文本），在新进程中 show 与 export-markdown 的完整
    输出符合 README 原有格式，重复记录分别保留、多行变更只在首行带
    条目前缀、原文与展示顺序不变。

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

# 正常路径固定样例：版本 demo-0.1，标题与变更均为虚构软件样例。
VERSION = "demo-0.1"
TITLE = "预览说明"
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

MISSING_VERSION = "demo-0.9"

# show 期望文本（独立按 README 的 show 格式写明）：Version / Title /
# "- " 前缀；两条重复变更分别成行；多行变更内部换行不带前缀。
EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title: 预览说明\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# export-markdown 期望文本（独立按 README 固定格式写明）：
# "# " + 版本名 + 两个换行；标题 + 两个换行；每条变更 "- " + 原文 + 换行。
EXPECTED_EXPORT = (
    "# demo-0.1\n"
    "\n"
    "预览说明\n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# unrelated 表中的示例数据，查询后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名。
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


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。

    返回 (sqlite_master 行列表, {表名: 行列表})，按固定顺序排列，
    用于核对查询前后库结构与数据完全一致。
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
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-missing-")
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

    def assert_version_not_found(self, result):
        """核对缺表读取边界：退出 1、空 stdout、固定单行 stderr、无堆栈。"""
        code, out, err = result
        self.assertEqual(code, 1, "缺少 drafts 表时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {MISSING_VERSION}\n",
            "标准错误应恰为 Version not found 加一个换行",
        )
        self.assertNotIn("Traceback", err, "标准错误不得包含异常堆栈")
        self.assertNotIn("sqlite3", err, "标准错误不得暴露底层异常")

    def assert_database_unchanged(self, bytes_before, snapshot_before):
        """文件字节、表结构、各表数据与目录文件集合均保持不变。"""
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "只读查询前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(self.db)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "查询前后表结构与原有数据必须完全一致",
        )
        master_after = snapshot_after[0]
        table_names = {row[1] for row in master_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 或发布说明文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "查询不得产生任何附属文件",
        )

    def run_missing_table_boundary(self, build_database, case_label):
        """两类数据库共用的缺表读取边界核对流程。"""
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        # show 与 export-markdown 各自在独立进程中执行，逐一核对边界。
        for command in ("show", "export-markdown"):
            with self.subTest(case=case_label, command=command):
                self.assert_version_not_found(
                    run_cli(self.db, command, MISSING_VERSION)
                )
                self.assert_database_unchanged(bytes_before, snapshot_before)
        return snapshot_before


class TestMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：show / export-markdown 的读取边界。"""

    def test_empty_sqlite_database_without_any_table(self):
        # 没有任何用户表，sqlite_sequence 等内部表也不存在。
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database, "empty"
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库查询后仍不应有任何表")
        self.assertEqual(tables, {}, "空库查询后仍不应有任何表数据")

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

        for command in ("show", "export-markdown"):
            with self.subTest(command=command):
                code, out, err = run_cli(db_path, command, MISSING_VERSION)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, f"Version not found: {MISSING_VERSION}\n")
                self.assertFalse(
                    os.path.exists(db_path), "版本不存在时不得创建数据库文件"
                )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestBlankNamePriorityOnMissingTables(DatabaseFixture, unittest.TestCase):
    """缺表库上的版本名空白校验优先级。"""

    def test_export_blank_name_reports_invalid_draft_before_reading(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes()
                snapshot_before = snapshot_database(self.db)

                for name in BLANK_NAMES:
                    code, out, err = run_cli(
                        self.db, "export-markdown", name
                    )
                    self.assertEqual(code, 1, "空白版本名退出码应为 1")
                    self.assertEqual(out, "", "失败时标准输出应为空")
                    self.assertEqual(
                        err,
                        "Invalid draft\n",
                        "空白版本名应优先报 Invalid draft",
                    )
                self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_show_keeps_existing_blank_name_rule_without_new_validation(self):
        # show 不新增空白校验：空白版本名继续按既有规则查库并报版本不存在，
        # 缺表库同样如此，消息中保留实际传入（为空）的版本名。
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes()
                snapshot_before = snapshot_database(self.db)

                for name in BLANK_NAMES:
                    code, out, err = run_cli(self.db, "show", name)
                    self.assertEqual(code, 1)
                    self.assertEqual(out, "")
                    self.assertEqual(err, f"Version not found: {name}\n")
                self.assert_database_unchanged(bytes_before, snapshot_before)


class TestNormalPathStillFormatted(DatabaseFixture, unittest.TestCase):
    """正常路径在新进程中的完整输出继续符合 README 原有格式。"""

    def test_create_then_show_and_export_in_new_processes(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0)
        self.assertEqual(out, f"Created {VERSION}\n")
        self.assertEqual(err, "")

        files_after_create = set(os.listdir(self._tmpdir.name))

        # show 与 export-markdown 均在新的独立进程中执行。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            EXPECTED_SHOW,
            "show 输出应符合 README 格式：重复记录分别保留，多行变更只在"
            "首行带 '- ' 前缀，原文与展示顺序不变",
        )

        first_export = run_cli(self.db, "export-markdown", VERSION)
        second_export = run_cli(self.db, "export-markdown", VERSION)
        self.assertEqual(first_export, second_export, "两次新进程导出应逐字相同")
        code, out, err = first_export
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            EXPECTED_EXPORT,
            "导出应符合 README 固定 Markdown 格式：标题原样、重复变更分别"
            "保留、多行变更只在首行带条目前缀，末尾只有格式规定的换行",
        )

        # 读操作不产生附属文件；数据库内恰为 drafts / changes 两表，
        # 三条变更（含两条相同文本）按 position 升序原样保存。
        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            files_after_create,
            "查看与导出不得新增任何附属文件",
        )
        conn = sqlite3.connect(self.db)
        try:
            draft = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (VERSION,)
            ).fetchone()
            self.assertEqual(draft, (TITLE,))
            rows = conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (VERSION,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            rows,
            list(enumerate(CHANGES)),
            "三条变更应分别保留（重复不去重），原文与顺序不变",
        )


if __name__ == "__main__":
    unittest.main()
