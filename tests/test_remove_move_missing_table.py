"""release_notes.py remove-change / move-change 缺 drafts 表边界的离线回归测试。

背景：show / export-markdown / set-title / add-change / set-change 已能把
缺少 drafts 表的数据库按版本不存在处理，但 remove-change 与 move-change
共用的 open_draft_for_update 在同类数据库（完全没有任何表，或只有
unrelated 一类无关表）上会直接执行 SELECT FROM drafts 而抛出
"no such table: drafts"，向标准错误暴露异常堆栈。修复后两个写入口都应
统一按版本不存在处理。

覆盖范围（仅通过命令行与外部可观察行为验收）：
  - 在两类预先准备好的数据库上分别执行两个固定样例：
    remove-change v1 --index 1 与 move-change v1 --from 1 --to 2：
    退出码 1、标准输出为空、标准错误恰为 "Version not found: v1\\n"，
    不含异常堆栈；带前导零的合法序号（01 / 02）同样到达版本查找并得到
    同一结果；
  - 失败完全不写入：调用前后数据库文件逐字节一致，表结构
    （sqlite_master 全部行）与原有数据完全一致，不补建 drafts /
    changes、不插入草稿，临时目录不出现 journal、wal 等附属文件；
  - 路径不存在时仍返回同一版本不存在结果，且不创建任何文件；
  - 输入校验优先于版本查找：未提供或重复提供序号选项（remove 的
    --index；move 的 --from / --to）、版本名为空 / 仅含空白、序号不是
    由 ASCII 数字组成的正整数（含 0、负号、小数、字母、全角数字、首尾
    空格）时，即使数据库缺表也统一报 "Invalid draft"，且不改动数据库；
  - drafts 表存在但查无版本（含仅大小写不同）时，保留既有版本不存在
    结果，消息中的版本名保留传入原文；
  - 正常流程语义保持不变：删除与移动的成功消息、展示顺序、重复条目与
    其他版本隔离、序号越界报 Change not found（序号去前导零）、禁止
    删除最后一条，均与 README 一致（既有 test_remove_change.py /
    test_move_change.py 仍完整保留，本文件只做关键锚定）。

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

# 缺表边界固定样例：与验收命令逐字一致，版本名按原文保留。
MISSING_VERSION = "v1"

# 正常路径固定样例：版本 demo-0.1，标题与变更均为虚构软件样例。
VERSION = "demo-0.1"
TITLE = "预览版"
REMOVE_CHANGES = ["新增预览", "新增预览", "改善提示"]
MOVE_CHANGES = ["新增预览", "改善提示", "新增预览"]

OTHER_VERSION = "other-1.0"
OTHER_TITLE = "正式版"
OTHER_CHANGES = ["首个变更"]

# unrelated 表中的示例数据，失败后必须原样保留（与验收样例一致）。
UNRELATED_ROWS = [(1, "保留原文")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名。
BLANK_NAMES = ["", "   ", " \n\t "]

# 非法序号：0、带负号、小数、字母、全角数字、首尾空格与加号都不是由
# ASCII 数字组成的正整数。
INVALID_INDEXES = ["0", "00", "-1", "1.5", "1a", "１２", " 1", "1 ", "+1"]


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
    用于核对失败操作前后库结构与数据完全一致。
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
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-rm-mv-")
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
        """建立只有 unrelated 表且保存一条 note 为“保留原文”的数据库。"""
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

    def assert_database_unchanged(self, bytes_before, snapshot_before):
        """文件字节、表结构、各表数据与目录文件集合均保持不变。"""
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "失败操作前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(self.db)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "失败操作前后表结构与原有数据必须完全一致",
        )
        master_after = snapshot_after[0]
        table_names = {row[1] for row in master_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "失败操作不得产生任何附属文件",
        )

    def run_missing_table_boundary(self, invoke, build_database, case_label):
        """两类数据库共用的缺表边界核对流程。"""
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        with self.subTest(case=case_label):
            self.assert_version_not_found(invoke(self.db))
            self.assert_database_unchanged(bytes_before, snapshot_before)


def invoke_remove_acceptance(db_path):
    """固定验收样例：remove-change v1 --index 1。"""
    return remove_change(db_path, MISSING_VERSION, "--index", "1")


def invoke_move_acceptance(db_path):
    """固定验收样例：move-change v1 --from 1 --to 2。"""
    return move_change(
        db_path, MISSING_VERSION, "--from", "1", "--to", "2"
    )


class TestRemoveChangeMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：remove-change 的版本边界。"""

    def test_empty_sqlite_database_without_any_table(self):
        # 完全没有表的合法数据库也必须得到确定的业务结果，库中仍无表。
        self.run_missing_table_boundary(
            invoke_remove_acceptance,
            self.build_empty_sqlite_database,
            "empty",
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库删除失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库删除失败后仍不应有任何表数据")

    def test_database_with_only_unrelated_table_and_sample_row(self):
        self.run_missing_table_boundary(
            invoke_remove_acceptance,
            self.build_unrelated_table_database,
            "unrelated",
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

    def test_leading_zero_index_still_reaches_version_lookup(self):
        # 合法序号允许前导零：校验通过后仍应报版本不存在，而非 Invalid draft。
        self.run_missing_table_boundary(
            lambda db_path: remove_change(
                db_path, MISSING_VERSION, "--index", "01"
            ),
            self.build_empty_sqlite_database,
            "empty-leading-zero",
        )

    def test_missing_path_still_not_found_without_creating_file(self):
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_version_not_found(invoke_remove_acceptance(db_path))
        self.assertFalse(
            os.path.exists(db_path), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestMoveChangeMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：move-change 的版本边界。"""

    def test_empty_sqlite_database_without_any_table(self):
        self.run_missing_table_boundary(
            invoke_move_acceptance,
            self.build_empty_sqlite_database,
            "empty",
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库移动失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库移动失败后仍不应有任何表数据")

    def test_database_with_only_unrelated_table_and_sample_row(self):
        self.run_missing_table_boundary(
            invoke_move_acceptance,
            self.build_unrelated_table_database,
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

    def test_leading_zero_indexes_still_reach_version_lookup(self):
        # 合法序号允许前导零：校验通过后仍应报版本不存在。
        self.run_missing_table_boundary(
            lambda db_path: move_change(
                db_path, MISSING_VERSION, "--from", "01", "--to", "02"
            ),
            self.build_unrelated_table_database,
            "unrelated-leading-zero",
        )

    def test_missing_path_still_not_found_without_creating_file(self):
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_version_not_found(invoke_move_acceptance(db_path))
        self.assertFalse(
            os.path.exists(db_path), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )


class TestValidationPriorityOnMissingTables(DatabaseFixture, unittest.TestCase):
    """缺表库上的输入校验优先级：先校验，后查库。"""

    def test_remove_change_invalid_input_reports_invalid_draft_first(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes()
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

                self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_move_change_invalid_input_reports_invalid_draft_first(self):
        for build_database, case_label in (
            (self.build_empty_sqlite_database, "empty"),
            (self.build_unrelated_table_database, "unrelated"),
        ):
            with self.subTest(case=case_label):
                build_database()
                bytes_before = self.read_database_bytes()
                snapshot_before = snapshot_database(self.db)

                # 省略必需序号选项：两者都缺、只给 --from、只给 --to。
                self.assert_invalid_draft(move_change(self.db, MISSING_VERSION))
                self.assert_invalid_draft(
                    move_change(self.db, MISSING_VERSION, "--from", "1")
                )
                self.assert_invalid_draft(
                    move_change(self.db, MISSING_VERSION, "--to", "1")
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

                self.assert_database_unchanged(bytes_before, snapshot_before)


class TestExistingDraftsTableRulesKept(DatabaseFixture, unittest.TestCase):
    """drafts 表存在时既有版本规则保持不变。"""

    def test_remove_change_version_missing_when_drafts_table_exists(self):
        # 草稿表存在但查无版本：保留既有版本不存在结果。
        code, out, err = create(self.db, VERSION, TITLE, REMOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        bytes_before = self.read_database_bytes()
        self.assert_version_not_found(invoke_remove_acceptance(self.db))
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "版本不存在时不得改动数据库",
        )

    def test_move_change_version_missing_when_drafts_table_exists(self):
        code, out, err = create(self.db, VERSION, TITLE, MOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        bytes_before = self.read_database_bytes()
        self.assert_version_not_found(invoke_move_acceptance(self.db))
        self.assertEqual(
            self.read_database_bytes(),
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


class TestRemoveChangeNormalSemanticsKept(DatabaseFixture, unittest.TestCase):
    """正常删除流程与关键失败语义锚定（完整覆盖见 test_remove_change.py）。"""

    def create_fixtures(self):
        code, out, err = create(self.db, VERSION, TITLE, REMOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))
        code, out, err = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual(
            (code, out, err), (0, f"Created {OTHER_VERSION}\n", "")
        )

    def test_remove_output_persistence_and_isolation(self):
        self.create_fixtures()

        # 带前导零的序号 02：成功消息中的序号不保留前导零。
        code, out, err = remove_change(self.db, VERSION, "--index", "02")
        self.assertEqual(code, 0, "remove-change 成功退出码应为 0")
        self.assertEqual(
            out,
            f"Removed change: {VERSION} #2\n",
            "成功时标准输出应为 Removed change 加版本名、序号与一个换行",
        )
        self.assertEqual(err, "", "成功时标准错误应为空")

        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 改善提示\n",
            "删除后重复文本的另一条记录仍保留，其余条目原文与顺序不变",
        )

        # 其他版本不受影响；目录内不残留附属文件。
        code, out, err = run_cli(self.db, "show", OTHER_VERSION)
        self.assertEqual(
            (code, out, err),
            (
                0,
                "Version: other-1.0\nTitle: 正式版\n- 首个变更\n",
                "",
            ),
            "其他版本不应受删除影响",
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "删除完成后目录内应只有数据库文件",
        )

    def test_out_of_range_and_last_change_failures_keep_data(self):
        self.create_fixtures()

        # 序号越界：序号 04 超出三条范围，消息去掉前导零。
        bytes_before = self.read_database_bytes()
        code, out, err = remove_change(self.db, VERSION, "--index", "04")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"Change not found: {VERSION} #4\n")
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "序号越界的失败删除不得改动数据库",
        )

        # 只剩一条时禁止删除：另建只有一条变更的版本。
        code, _, _ = create(self.db, "solo-2.0", "单条版", ["唯一变更"])
        self.assertEqual(code, 0)
        code, out, err = remove_change(self.db, "solo-2.0", "--index", "1")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "Cannot remove last change: solo-2.0\n")
        code, out, err = run_cli(self.db, "show", "solo-2.0")
        self.assertEqual(
            (code, out, err),
            (0, "Version: solo-2.0\nTitle: 单条版\n- 唯一变更\n", ""),
            "被拒绝的删除不得改变仅剩的记录",
        )


class TestMoveChangeNormalSemanticsKept(DatabaseFixture, unittest.TestCase):
    """正常移动流程与越界语义锚定（完整覆盖见 test_move_change.py）。"""

    def create_fixtures(self):
        code, out, err = create(self.db, VERSION, TITLE, MOVE_CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))
        code, out, err = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual(
            (code, out, err), (0, f"Created {OTHER_VERSION}\n", "")
        )

    def test_move_output_persistence_and_isolation(self):
        self.create_fixtures()

        code, out, err = move_change(
            self.db, VERSION, "--from", "03", "--to", "1"
        )
        self.assertEqual(code, 0, "move-change 成功退出码应为 0")
        self.assertEqual(
            out,
            f"Moved change: {VERSION} #3 -> #1\n",
            "成功时标准输出应为 Moved change 加来源、目标序号与一个换行",
        )
        self.assertEqual(err, "", "成功时标准错误应为空")

        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 改善提示\n",
            "移动后所选条目恰好位于目标序号，其余条目相对顺序不变，"
            "重复记录分别保留",
        )

        code, out, err = run_cli(self.db, "show", OTHER_VERSION)
        self.assertEqual(
            (code, out, err),
            (
                0,
                "Version: other-1.0\nTitle: 正式版\n- 首个变更\n",
                "",
            ),
            "其他版本不应受移动影响",
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "移动完成后目录内应只有数据库文件",
        )

    def test_out_of_range_reports_from_first_and_keeps_order(self):
        self.create_fixtures()

        bytes_before = self.read_database_bytes()
        # 先检查来源再检查目标：两者都越界时报告来源（序号去前导零）。
        code, out, err = move_change(
            self.db, VERSION, "--from", "05", "--to", "06"
        )
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"Change not found: {VERSION} #5\n")
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "序号越界的失败移动不得改动数据库",
        )

        code, out, err = run_cli(self.db, "export-markdown", VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "# demo-0.1\n\n预览版\n\n"
            "- 新增预览\n- 改善提示\n- 新增预览\n",
            "序号越界的失败移动不得改变已有内容与顺序",
        )


if __name__ == "__main__":
    unittest.main()
