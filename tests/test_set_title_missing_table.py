"""release_notes.py set-title 标题修订边界的离线回归测试。

背景：show 与 export-markdown 已能把缺少 drafts 表的数据库按版本不存在
处理，但 set-title 在同类数据库（完全没有任何表，或只有 unrelated 一类
无关表）上会直接执行 UPDATE drafts 而抛出 "no such table: drafts"，向
标准错误暴露异常堆栈。修复后 set-title 也应统一按版本不存在处理。

覆盖范围（仅通过命令行与外部可观察行为验收）：
  - 在两类预先准备好的数据库上调用
    set-title demo-0.9 --title "新标题"：退出码 1、标准输出为空、
    标准错误恰为 "Version not found: demo-0.9\\n"，不含异常堆栈；
  - 失败完全不写入：调用前后数据库文件逐字节一致，表结构
    （sqlite_master 全部行）与原有数据完全一致，不补建 drafts /
    changes、不插入草稿，临时目录不出现 journal、wal 等附属文件；
  - 路径不存在时仍返回同一版本不存在结果，且不创建任何文件；
  - 输入校验优先于数据库检查：版本名或新标题为空、仅含空白，或未提供
    --title 时，即使数据库缺表也统一报 "Invalid draft"，且不改动数据库；
  - drafts 表存在但查无版本时，保留既有版本不存在结果；
  - 正常路径：按版本名原文精确匹配且区分大小写，成功时退出 0、标准
    错误为空、标准输出为 "Updated title: <版本名>\\n"；再次提交相同
    标题仍成功；中文、首尾空格与内部换行原样保存；变更数量、重复条目、
    展示顺序及其他版本均不受影响；重新打开同一路径可查看新标题。

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

# 正常路径固定样例：两个版本，验证修订互不影响。
VERSION = "demo-0.1"
OTHER_VERSION = "demo-0.2"
ORIGINAL_TITLE = "预览说明"
OTHER_TITLE = "另一版本说明"
# 两条相同文本加一条内部含换行的文本，用于核对数量、重复与展示顺序。
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]
OTHER_CHANGES = ["其他变更"]

# 新标题：中文、首尾空格与内部换行都必须原样保存。
NEW_TITLE = " 修订标题：中文\n第二行 "

MISSING_VERSION = "demo-0.9"
MISSING_TITLE = "新标题"

# unrelated 表中的示例数据，修订失败后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名。
BLANK_NAMES = ["", "   ", " \n\t "]
# 空字符串与仅含空白的新标题。
BLANK_TITLES = ["", "   ", " \n\t "]


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


def set_title(db_path, version, title):
    return run_cli(db_path, "set-title", version, "--title", title)


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。

    返回 (sqlite_master 行列表, {表名: 行列表})，按固定顺序排列，
    用于核对修订前后库结构与数据完全一致。
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
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-title-")
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
            "标准错误应恰为 Version not found 加一个换行，版本名保留原文",
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
            "失败修订前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(self.db)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "失败修订前后表结构与原有数据必须完全一致",
        )
        master_after = snapshot_after[0]
        table_names = {row[1] for row in master_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "失败修订不得产生任何附属文件",
        )

    def run_missing_table_boundary(self, build_database, case_label):
        """两类数据库共用的缺表修订边界核对流程，返回修订前快照。"""
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        # 固定小样例：set-title demo-0.9 --title "新标题"。
        with self.subTest(case=case_label):
            self.assert_version_not_found(
                set_title(self.db, MISSING_VERSION, MISSING_TITLE)
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)
        return snapshot_before


class TestSetTitleMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：set-title 的修订边界。"""

    def test_empty_sqlite_database_without_any_table(self):
        # 完全没有表的合法数据库也必须得到确定的业务结果。
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database, "empty"
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库修订失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库修订失败后仍不应有任何表数据")

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

        code, out, err = set_title(db_path, MISSING_VERSION, MISSING_TITLE)
        self.assert_version_not_found((code, out, err))
        self.assertFalse(
            os.path.exists(db_path), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestValidationPriorityOnMissingTables(DatabaseFixture, unittest.TestCase):
    """缺表库上的输入校验优先级：先校验，后查库。"""

    def test_invalid_input_reports_invalid_draft_before_database_check(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes()
                snapshot_before = snapshot_database(self.db)

                # 版本名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        set_title(self.db, name, "有效标题")
                    )
                # 新标题为空或仅含空白。
                for title in BLANK_TITLES:
                    self.assert_invalid_draft(
                        set_title(self.db, MISSING_VERSION, title)
                    )
                # 未提供 --title。
                self.assert_invalid_draft(
                    run_cli(self.db, "set-title", MISSING_VERSION)
                )

                self.assert_database_unchanged(bytes_before, snapshot_before)


class TestExistingDraftsTableRulesKept(DatabaseFixture, unittest.TestCase):
    """drafts 表存在时既有行为保持不变。"""

    def test_version_missing_when_drafts_table_exists(self):
        # 草稿表存在但查无版本：保留既有版本不存在结果。
        code, out, err = create(
            self.db, VERSION, ORIGINAL_TITLE, CHANGES
        )
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        bytes_before = self.read_database_bytes()
        self.assert_version_not_found(
            set_title(self.db, MISSING_VERSION, MISSING_TITLE)
        )
        # 失败修订同样不改动任何已有数据。
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "版本不存在时不得改动数据库",
        )

    def test_case_sensitive_exact_match_does_not_write_on_mismatch(self):
        code, _, _ = create(self.db, VERSION, ORIGINAL_TITLE, CHANGES)
        self.assertEqual(code, 0)

        # 大小写不同的版本名视为不存在，消息保留实际传入的原文。
        self.assert_version_not_found(
            set_title(self.db, VERSION.upper(), "不应写入"),
            version=VERSION.upper(),
        )

        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (VERSION,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (ORIGINAL_TITLE,), "大小写不匹配时不得改写标题")


class TestSetTitleNormalPath(DatabaseFixture, unittest.TestCase):
    """正常修订：精确匹配、幂等、原文保存，且不波及变更与其他版本。"""

    def expected_show_with(self, title):
        """与 show 格式一致，仅替换标题行。"""
        first_line, second_line = CHANGES[2].splitlines()
        return (
            f"Version: {VERSION}\n"
            f"Title: {title}\n"
            f"- {CHANGES[0]}\n"
            f"- {CHANGES[1]}\n"
            f"- {first_line}\n"
            f"{second_line}\n"
        )

    def test_update_title_output_persistence_and_isolation(self):
        code, out, err = create(
            self.db, VERSION, ORIGINAL_TITLE, CHANGES
        )
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))
        code, out, err = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual((code, out, err), (0, f"Created {OTHER_VERSION}\n", ""))

        # 成功修订：退出 0，stderr 为空，stdout 恰为固定成功消息。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0, "set-title 成功退出码应为 0")
        self.assertEqual(
            out,
            f"Updated title: {VERSION}\n",
            "成功时标准输出应为 Updated title 加版本名原文与一个换行",
        )
        self.assertEqual(err, "", "成功时标准错误应为空")

        # 再次提交相同标题仍成功（单条 UPDATE 幂等）。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0)
        self.assertEqual(out, f"Updated title: {VERSION}\n")
        self.assertEqual(err, "")

        # 重新打开同一路径（新进程）即可查看新标题：中文、首尾空格、
        # 内部换行原样保存。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            self.expected_show_with(NEW_TITLE),
            "新标题必须原样保存，变更的原文、数量与展示顺序不变",
        )

        # 直接核对库内数据：目标标题更新；变更仍为 3 条（重复条目分别
        # 保留），position 与原文不变；其他版本的标题与变更不受影响。
        conn = sqlite3.connect(self.db)
        try:
            title_row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (VERSION,)
            ).fetchone()
            change_rows = conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (VERSION,),
            ).fetchall()
            other_title_row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?",
                (OTHER_VERSION,),
            ).fetchone()
            other_change_rows = conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (OTHER_VERSION,),
            ).fetchall()
            draft_rows = conn.execute(
                "SELECT version, title FROM drafts ORDER BY version"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(title_row, (NEW_TITLE,))
        self.assertEqual(
            change_rows,
            list(enumerate(CHANGES)),
            "变更数量、重复条目与展示顺序均不受标题修订影响",
        )
        self.assertEqual(other_title_row, (OTHER_TITLE,), "其他版本标题不变")
        self.assertEqual(
            other_change_rows,
            list(enumerate(OTHER_CHANGES)),
            "其他版本的变更不受影响",
        )
        self.assertEqual(
            draft_rows,
            [(VERSION, NEW_TITLE), (OTHER_VERSION, OTHER_TITLE)],
            "修订标题不改变版本集合，仅目标版本标题变化",
        )

        # 修订后不得残留 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "修订完成后目录内应只有数据库文件",
        )


if __name__ == "__main__":
    unittest.main()
