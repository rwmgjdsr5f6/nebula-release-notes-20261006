"""release_notes.py remove-change / move-change 缺 drafts 表边界的回归测试。

背景：show / export-markdown / set-title / add-change / set-change 已能把
缺少 drafts 表的数据库（完全没有任何表，或只有 unrelated 一类无关表）按
版本不存在处理，但 remove-change 与 move-change 共用的
open_draft_for_update 直接执行 SELECT FROM drafts，在同类数据库上会抛出
"no such table: drafts"，向标准错误暴露 sqlite3 异常堆栈。修复后两个命令
都应统一按版本不存在处理。

两个固定验收样例（库文件均已存在）：
  - empty.sqlite 为没有任何表的合法 SQLite 数据库：
    python release_notes.py --db empty.sqlite remove-change v1 --index 1
    退出 1，标准输出为空，标准错误恰为 "Version not found: v1\\n"，
    库中仍无任何表；
  - unrelated.sqlite 只含 unrelated 表及一条 note 为“保留原文”的记录：
    python release_notes.py --db unrelated.sqlite move-change v1 --from 1 --to 2
    得到相同错误，原表与记录逐字节保留。

另覆盖：
  - 两类缺表库 × 两种操作的完整矩阵：退出码、stdout、stderr 与存储内容
    （文件字节、sqlite_master 全部行、各表数据、目录文件集合）完全核对，
    不补建 drafts / changes、不插入草稿、不产生 journal / wal 等附属文件；
  - 数据库路径不存在时仍报版本不存在，且保持路径不存在；
  - 输入校验优先于版本查找：缺序号选项、重复序号选项、版本名为空或仅含
    空白、序号不是由 ASCII 数字组成的正整数（含 0、负号、小数、字母、
    全角数字）时，即使数据库缺表也统一报 "Invalid draft"，且不改库；
  - drafts 表存在但查无版本（含仅大小写不同）时沿用既有版本不存在结果，
    消息中的版本名保留传入原文，且不写入；
  - 正常删除、移动路径仍按 README 成功并持久化（详细正常流程由
    test_remove_change.py / test_move_change.py 覆盖，此处只做兼容性核对）。

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

# 固定验收样例使用的版本名：错误消息必须保留传入原文 v1。
MISSING_VERSION = "v1"

# unrelated 表中的固定示例数据，失败后必须原样保留。
UNRELATED_ROWS = [(1, "保留原文")]

# 正常兼容路径使用的固定样例。
VERSION = "demo-0.1"
TITLE = "预览版"
REMOVE_CHANGES = ["新增预览", "重复条目", "改善提示"]
MOVE_CHANGES = ["新增预览", "改善提示", "重复条目"]

# 空字符串与仅含空白（空格、制表符、换行）的版本名。
BLANK_NAMES = ["", "   ", " \n\t "]

# 非法序号：0、带负号、小数、字母、首尾空格、加号、全角数字都不是由
# ASCII 数字组成的正整数。
INVALID_INDEXES = [
    "0", "00", "-1", "1.5", "1a", "abc", " 1", "1 ", "+1", "１２",
]


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


def remove_change(db_path, version, *index_args):
    return run_cli(db_path, "remove-change", version, *index_args)


def move_change(db_path, version, *move_args):
    return run_cli(db_path, "move-change", version, *move_args)


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。

    返回 (sqlite_master 行列表, {表名: 行列表})，按固定顺序排列，
    用于核对失败前后库结构与数据完全一致。
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


class DatabaseFixture(unittest.TestCase):
    """共享的临时目录、建库与核对工具。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-rm-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def path_for(self, name):
        return os.path.join(self._tmpdir.name, name)

    def read_database_bytes(self, db_path):
        with open(db_path, "rb") as handle:
            return handle.read()

    def build_empty_sqlite_database(self, db_path=None):
        """建立不含任何表的合法 SQLite 数据库（具备文件头）。"""
        db_path = db_path or self.db
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE _init (x INTEGER)")
            conn.execute("DROP TABLE _init")
            conn.commit()
        finally:
            conn.close()

    def build_unrelated_table_database(self, db_path=None):
        """建立只有 unrelated 表且保存固定示例行的数据库。"""
        db_path = db_path or self.db
        conn = sqlite3.connect(db_path)
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
        """核对缺表边界：退出 1、空 stdout、固定单行 stderr、无堆栈。"""
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

    def assert_database_unchanged(self, db_path, bytes_before, snapshot_before):
        """文件字节、表结构、各表数据与目录文件集合均保持不变。"""
        self.assertEqual(
            self.read_database_bytes(db_path),
            bytes_before,
            "失败操作前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(db_path)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "失败操作前后表结构与原有数据必须完全一致",
        )
        table_names = {row[1] for row in snapshot_after[0] if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(db_path)],
            "失败操作不得产生任何附属文件",
        )

    def run_missing_table_boundary(self, build_database, invoke, case_label):
        """两类数据库 × 两种操作共用的缺表边界核对流程。"""
        build_database()
        bytes_before = self.read_database_bytes(self.db)
        snapshot_before = snapshot_database(self.db)

        with self.subTest(case=case_label):
            self.assert_version_not_found(invoke())
            self.assert_database_unchanged(
                self.db, bytes_before, snapshot_before
            )


class TestRemoveChangeMissingDraftsTable(DatabaseFixture):
    """已存在但没有 drafts 表的数据库：remove-change 的删除边界。"""

    def test_fixed_sample_empty_sqlite_database(self):
        # 固定验收样例一：empty.sqlite 无任何表，
        # remove-change v1 --index 1 必须报版本不存在且库中仍无表。
        db_path = self.path_for("empty.sqlite")
        self.build_empty_sqlite_database(db_path)
        bytes_before = self.read_database_bytes(db_path)

        self.assert_version_not_found(
            run_cli(db_path, "remove-change", "v1", "--index", "1")
        )

        self.assertEqual(
            self.read_database_bytes(db_path),
            bytes_before,
            "失败删除前后 empty.sqlite 必须逐字节一致",
        )
        master, tables = snapshot_database(db_path)
        self.assertEqual(master, [], "空库删除失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库删除失败后仍不应有任何表数据")
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["empty.sqlite"],
            "失败删除不得产生任何附属文件",
        )

    def test_empty_sqlite_database_leading_zero_index(self):
        # 合法序号允许前导零：缺表时仍优先按版本不存在处理。
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database,
            lambda: remove_change(self.db, MISSING_VERSION, "--index", "001"),
            "empty",
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库删除失败后仍不应有任何表")
        self.assertEqual(tables, {})

    def test_unrelated_table_rows_preserved(self):
        # 只有 unrelated 表时同样按版本不存在处理，原表与记录不变。
        self.run_missing_table_boundary(
            self.build_unrelated_table_database,
            lambda: remove_change(self.db, MISSING_VERSION, "--index", "1"),
            "unrelated",
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(
            [row[1] for row in master], ["unrelated"], "只剩 unrelated 表"
        )
        self.assertEqual(
            tables["unrelated"],
            UNRELATED_ROWS,
            "unrelated 表的示例数据必须原样保留",
        )


class TestMoveChangeMissingDraftsTable(DatabaseFixture):
    """已存在但没有 drafts 表的数据库：move-change 的移动边界。"""

    def test_empty_sqlite_database(self):
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database,
            lambda: move_change(
                self.db, MISSING_VERSION, "--from", "01", "--to", "02"
            ),
            "empty",
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库移动失败后仍不应有任何表")
        self.assertEqual(tables, {})

    def test_fixed_sample_unrelated_table_rows_preserved(self):
        # 固定验收样例二：unrelated.sqlite 只含 unrelated 表及 note 为
        # “保留原文”的一条记录，move-change v1 --from 1 --to 2 必须报
        # 版本不存在，原表与记录不变。
        db_path = self.path_for("unrelated.sqlite")
        self.build_unrelated_table_database(db_path)
        bytes_before = self.read_database_bytes(db_path)

        self.assert_version_not_found(
            run_cli(
                db_path, "move-change", "v1", "--from", "1", "--to", "2"
            )
        )

        self.assertEqual(
            self.read_database_bytes(db_path),
            bytes_before,
            "失败移动前后 unrelated.sqlite 必须逐字节一致",
        )
        master, tables = snapshot_database(db_path)
        self.assertEqual(
            [row[1] for row in master],
            ["unrelated"],
            "失败移动后仍应只剩 unrelated 表",
        )
        self.assertEqual(
            tables["unrelated"],
            UNRELATED_ROWS,
            "unrelated 表的“保留原文”记录必须原样保留",
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["unrelated.sqlite"],
            "失败移动不得产生任何附属文件",
        )


class TestMissingDatabasePath(DatabaseFixture):
    """路径不存在时仍报版本不存在，并保持路径不存在。"""

    def test_remove_change_missing_path_creates_nothing(self):
        db_path = self.path_for("missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_version_not_found(
            remove_change(db_path, MISSING_VERSION, "--index", "1")
        )
        self.assertFalse(
            os.path.exists(db_path), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )

    def test_move_change_missing_path_creates_nothing(self):
        db_path = self.path_for("missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_version_not_found(
            move_change(
                db_path, MISSING_VERSION, "--from", "1", "--to", "2"
            )
        )
        self.assertFalse(
            os.path.exists(db_path), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestValidationPriorityOnMissingTables(DatabaseFixture):
    """缺表库上的输入校验优先级：先校验，后查库。"""

    def test_remove_change_invalid_input_reports_invalid_draft(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes(self.db)
                snapshot_before = snapshot_database(self.db)

                # 未提供 --index。
                self.assert_invalid_draft(
                    remove_change(self.db, MISSING_VERSION)
                )
                # 重复提供 --index。
                self.assert_invalid_draft(
                    remove_change(
                        self.db, MISSING_VERSION,
                        "--index", "1", "--index", "2",
                    )
                )
                # 版本名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        remove_change(self.db, name, "--index", "1")
                    )
                # 序号不是由 ASCII 数字组成的正整数（含 0）。
                for index in INVALID_INDEXES:
                    self.assert_invalid_draft(
                        remove_change(
                            self.db, MISSING_VERSION, "--index", index
                        )
                    )

                self.assert_database_unchanged(
                    self.db, bytes_before, snapshot_before
                )

    def test_move_change_invalid_input_reports_invalid_draft(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes(self.db)
                snapshot_before = snapshot_database(self.db)

                # 省略必需的序号选项：全缺、只给 --from、只给 --to。
                self.assert_invalid_draft(move_change(self.db, MISSING_VERSION))
                self.assert_invalid_draft(
                    move_change(self.db, MISSING_VERSION, "--from", "1")
                )
                self.assert_invalid_draft(
                    move_change(self.db, MISSING_VERSION, "--to", "2")
                )
                # 重复提供序号选项。
                self.assert_invalid_draft(
                    move_change(
                        self.db, MISSING_VERSION,
                        "--from", "1", "--from", "2", "--to", "1",
                    )
                )
                self.assert_invalid_draft(
                    move_change(
                        self.db, MISSING_VERSION,
                        "--from", "1", "--to", "1", "--to", "2",
                    )
                )
                # 版本名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        move_change(
                            self.db, name, "--from", "1", "--to", "2"
                        )
                    )
                # 任一序号不是由 ASCII 数字组成的正整数（含 0）。
                for index in INVALID_INDEXES:
                    self.assert_invalid_draft(
                        move_change(
                            self.db, MISSING_VERSION,
                            "--from", index, "--to", "1",
                        )
                    )
                    self.assert_invalid_draft(
                        move_change(
                            self.db, MISSING_VERSION,
                            "--from", "1", "--to", index,
                        )
                    )

                self.assert_database_unchanged(
                    self.db, bytes_before, snapshot_before
                )


class TestExistingDraftsTableRulesKept(DatabaseFixture):
    """drafts 表存在时既有行为保持不变。"""

    def test_remove_change_version_missing_when_drafts_table_exists(self):
        # 草稿表存在但查无版本：沿用既有版本不存在结果。
        code, out, err = create(self.db, VERSION, TITLE, REMOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        bytes_before = self.read_database_bytes(self.db)
        self.assert_version_not_found(
            remove_change(self.db, MISSING_VERSION, "--index", "1")
        )
        self.assertEqual(
            self.read_database_bytes(self.db),
            bytes_before,
            "版本不存在时不得改动数据库",
        )

    def test_move_change_version_missing_when_drafts_table_exists(self):
        code, out, err = create(self.db, VERSION, TITLE, MOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        bytes_before = self.read_database_bytes(self.db)
        self.assert_version_not_found(
            move_change(
                self.db, MISSING_VERSION, "--from", "1", "--to", "2"
            )
        )
        self.assertEqual(
            self.read_database_bytes(self.db),
            bytes_before,
            "版本不存在时不得改动数据库",
        )

    def test_case_sensitive_exact_match_does_not_write_on_mismatch(self):
        code, _, _ = create(self.db, VERSION, TITLE, REMOVE_CHANGES)
        self.assertEqual(code, 0)

        # 大小写不同的版本名视为不存在，消息保留实际传入的原文。
        self.assert_version_not_found(
            remove_change(self.db, VERSION.upper(), "--index", "1"),
            version=VERSION.upper(),
        )
        self.assert_version_not_found(
            move_change(
                self.db, VERSION.upper(), "--from", "1", "--to", "2"
            ),
            version=VERSION.upper(),
        )

        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (VERSION,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            rows,
            list(enumerate(REMOVE_CHANGES)),
            "大小写不匹配时不得删除或移动任何变更",
        )


class TestNormalFlowStillWorks(DatabaseFixture):
    """修复不影响正常删除与移动（详细正常流程见各自既有测试文件）。"""

    def test_remove_change_success_persists(self):
        code, out, err = create(self.db, VERSION, TITLE, REMOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        # 带前导零的序号 02：成功消息中的序号不保留前导零。
        code, out, err = remove_change(self.db, VERSION, "--index", "02")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, f"Removed change: {VERSION} #2\n")

        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 改善提示\n",
            "正常删除后仅移除指定条目，重复文本的其他记录仍保留",
        )

    def test_move_change_success_persists(self):
        code, out, err = create(self.db, VERSION, TITLE, MOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        code, out, err = move_change(
            self.db, VERSION, "--from", "03", "--to", "1"
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, f"Moved change: {VERSION} #3 -> #1\n")

        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 重复条目\n"
            "- 新增预览\n"
            "- 改善提示\n",
            "正常移动后所选条目应恰好落在目标序号，其余条目相对顺序不变",
        )

        # 移动完成后目录内只有数据库文件，不残留 journal、wal 等。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "移动完成后目录内应只有数据库文件",
        )


if __name__ == "__main__":
    unittest.main()
