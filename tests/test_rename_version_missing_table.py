"""release_notes.py rename-version 重命名入口缺表边界的离线回归测试。

背景：rename-version 已能在数据库文件不存在时按原版本不存在处理，但对
已存在的数据库会直接执行 SELECT FROM drafts；当库中没有 drafts 表
（完全没有任何表，或只有 unrelated 一类无关表）时会抛出
"no such table: drafts"，向标准错误暴露异常堆栈。修复后 rename-version
也应统一按原版本不存在处理，且不补建 drafts / changes、不改动库中其他
表与数据。

覆盖范围（仅通过命令行与外部可观察行为验收）：
  - 在两类预先准备好的数据库上调用
    rename-version v1 --to v2：退出码 1、标准输出为空、标准错误恰为
    "Version not found: v1\\n"，不含异常堆栈；目标与原名相同（--to v1）
    时同样返回原版本不存在；
  - 消息中的原名按输入保留中文与首尾空格；版本匹配仍区分大小写（缺表
    库中任何名称都按不存在处理，消息逐字回显传入原名）；
  - 失败完全不写入：调用前后数据库文件逐字节一致，表结构
    （sqlite_master 全部行）与原有数据完全一致，不补建 drafts /
    changes、不更新任何记录，临时目录不出现 journal、wal 等附属文件；
  - 路径不存在时仍返回原版本不存在结果，且不创建任何文件；
  - 输入校验优先于数据库检查：原名 为空 / 仅含空白，或 --to 未提供、
    缺值、为空、仅含空白、重复提供时，即使数据库缺表或路径不存在也
    统一报 "Invalid draft"，且不改动数据库；
  - drafts 表存在但查无版本（含仅大小写不同）时，保留既有原版本不存
    在结果，消息中的原名保留传入原文；
  - 正常路径：固定样例 v1（标题"预览版"，两条重复的"新增预览"与一条
    由"修正提示"与"保留原文"两行组成的多行文本）重命名为 v2 后退出 0、
    标准错误为空、标准输出为 "Renamed version: v1 -> v2\\n"；新进程
    查看新名称时标题、原文、数量与顺序不变，旧名称仍报
    Version not found: v1。

同名改名成功、目标名称冲突以及其余公开命令的行为维持现状，由
test_rename_version.py 等既有测试继续覆盖，本文件不重复。

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

# 正常路径固定样例：版本 v1，标题"预览版"，三条变更依次为两条重复文本
# 与一条两行组成的多行文本。
VERSION = "v1"
NEW_VERSION = "v2"
TITLE = "预览版"
CHANGES = ["新增预览", "新增预览", "修正提示\n保留原文"]

# 重命名后新进程 show 的期望输出（按 README 固定格式独立写明）：新版本名、
# 原标题、三条原文，重复条目分别保留，多行文本仍是一条记录。
EXPECTED_SHOW_V2 = (
    "Version: v2\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修正提示\n"
    "保留原文\n"
)

# 缺表边界固定使用的原名与新名。
MISSING_VERSION = "v1"
TARGET_VERSION = "v2"

# 含中文与首尾空格的原名：失败消息必须按输入逐字保留。
SPACY_VERSION = " 预览 版1 "

# unrelated 表中的示例数据，重命名失败后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名。
BLANK_NAMES = ["", "   ", " \n\t "]
BLANK_TARGETS = ["", "   ", " \n\t "]


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


def show(db_path, version):
    return run_cli(db_path, "show", version)


def rename_version(db_path, version, *rename_args):
    return run_cli(db_path, "rename-version", version, *rename_args)


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。

    返回 (sqlite_master 行列表, {表名: 行列表})，按固定顺序排列，
    用于核对重命名失败前后库结构与数据完全一致。
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

    def assert_version_not_found(self, result, version=MISSING_VERSION):
        """核对缺表重命名边界：退出 1、空 stdout、固定单行 stderr、无堆栈。"""
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
            "失败重命名前后数据库文件必须逐字节一致",
        )
        snapshot_after = snapshot_database(self.db)
        self.assertEqual(
            snapshot_after,
            snapshot_before,
            "失败重命名前后表结构与原有数据必须完全一致",
        )
        master_after = snapshot_after[0]
        table_names = {row[1] for row in master_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        # 目录内只有数据库文件本身，不出现 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "失败重命名不得产生任何附属文件",
        )

    def run_missing_table_boundary(self, build_database, case_label):
        """两类数据库共用的缺表重命名边界核对流程。"""
        build_database()
        bytes_before = self.read_database_bytes()
        snapshot_before = snapshot_database(self.db)

        # 固定小样例：rename-version v1 --to v2。
        with self.subTest(case=case_label):
            self.assert_version_not_found(
                rename_version(self.db, MISSING_VERSION, "--to", TARGET_VERSION)
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)

        # 目标与原名相同也返回原版本不存在，不按成功处理。
        with self.subTest(case=case_label + "-same-name"):
            self.assert_version_not_found(
                rename_version(self.db, MISSING_VERSION, "--to", MISSING_VERSION)
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)

        # 消息中的原名按输入保留中文与首尾空格。
        with self.subTest(case=case_label + "-spacy-name"):
            self.assert_version_not_found(
                rename_version(self.db, SPACY_VERSION, "--to", TARGET_VERSION),
                version=SPACY_VERSION,
            )
            self.assert_database_unchanged(bytes_before, snapshot_before)

        return snapshot_before


class TestRenameVersionMissingDraftsTable(DatabaseFixture, unittest.TestCase):
    """已存在但没有 drafts 表的数据库：rename-version 的缺表边界。"""

    def test_empty_sqlite_database_without_any_table(self):
        # 完全没有表的合法数据库也必须得到确定的业务结果。
        self.run_missing_table_boundary(
            self.build_empty_sqlite_database, "empty"
        )
        master, tables = snapshot_database(self.db)
        self.assertEqual(master, [], "空库重命名失败后仍不应有任何表")
        self.assertEqual(tables, {}, "空库重命名失败后仍不应有任何表数据")

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

        code, out, err = rename_version(
            db_path, MISSING_VERSION, "--to", TARGET_VERSION
        )
        self.assert_version_not_found((code, out, err))
        self.assertFalse(
            os.path.exists(db_path), "原版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )

        # 目标与原名相同、路径不存在时同样返回原版本不存在。
        code, out, err = rename_version(
            db_path, MISSING_VERSION, "--to", MISSING_VERSION
        )
        self.assert_version_not_found((code, out, err))
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

                # 原名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        rename_version(self.db, name, "--to", TARGET_VERSION)
                    )
                # 未提供 --to。
                self.assert_invalid_draft(
                    run_cli(self.db, "rename-version", MISSING_VERSION)
                )
                # 显式提供 --to 却缺值（nargs='?' 落空取空串常量）。
                self.assert_invalid_draft(
                    run_cli(self.db, "rename-version", MISSING_VERSION, "--to")
                )
                # --to 值为空或仅含空白。
                for target in BLANK_TARGETS:
                    self.assert_invalid_draft(
                        rename_version(self.db, MISSING_VERSION, "--to", target)
                    )
                # 重复提供 --to。
                self.assert_invalid_draft(
                    rename_version(
                        self.db, MISSING_VERSION,
                        "--to", TARGET_VERSION, "--to", "v3",
                    )
                )

                self.assert_database_unchanged(bytes_before, snapshot_before)

    def test_invalid_input_precedes_missing_path(self):
        # 路径不存在时无效输入仍优先报 Invalid draft（而非 Version not
        # found），且不会因此创建数据库或任何附属文件。
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        for name in BLANK_NAMES:
            self.assert_invalid_draft(
                rename_version(missing_db, name, "--to", TARGET_VERSION)
            )
        self.assert_invalid_draft(
            run_cli(missing_db, "rename-version", MISSING_VERSION)
        )
        self.assert_invalid_draft(
            run_cli(missing_db, "rename-version", MISSING_VERSION, "--to")
        )
        self.assert_invalid_draft(
            rename_version(
                missing_db, MISSING_VERSION,
                "--to", TARGET_VERSION, "--to", "v3",
            )
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "无效输入不得创建任何文件"
        )


class TestExistingDraftsTableRulesKept(DatabaseFixture, unittest.TestCase):
    """drafts 表存在时既有行为保持不变。"""

    def test_version_missing_when_drafts_table_exists(self):
        # 草稿表存在但查无版本：保留既有原版本不存在结果，且不写入。
        code, out, err = create(self.db, NEW_VERSION, TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {NEW_VERSION}\n", ""))

        bytes_before = self.read_database_bytes()
        self.assert_version_not_found(
            rename_version(self.db, MISSING_VERSION, "--to", "v3")
        )
        # 失败重命名同样不改动任何已有数据。
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "原版本不存在时不得改动数据库",
        )

    def test_case_sensitive_exact_match_does_not_rename_on_mismatch(self):
        code, _, _ = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0)

        # 大小写不同的原名视为不存在，消息保留实际传入的原文。
        self.assert_version_not_found(
            rename_version(self.db, VERSION.upper(), "--to", NEW_VERSION),
            version=VERSION.upper(),
        )

        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT version, title FROM drafts"
            ).fetchall()
            change_rows = conn.execute(
                "SELECT version, position, content FROM changes"
                " ORDER BY position"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            rows,
            [(VERSION, TITLE)],
            "大小写不匹配时不得重命名草稿",
        )
        self.assertEqual(
            change_rows,
            [(VERSION, position, content)
             for position, content in enumerate(CHANGES)],
            "大小写不匹配时不得改动任何变更",
        )


class TestRenameVersionNormalPath(DatabaseFixture, unittest.TestCase):
    """正常重命名：成功消息、内容随版本名保留、旧名消失、库内数据核对。"""

    def test_rename_output_persistence_order_and_old_name_gone(self):
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, f"Created {VERSION}\n", ""))

        # 成功重命名：退出 0，stderr 为空，stdout 恰为固定成功消息。
        code, out, err = rename_version(
            self.db, VERSION, "--to", NEW_VERSION
        )
        self.assertEqual(code, 0, "rename-version 成功退出码应为 0")
        self.assertEqual(
            out,
            f"Renamed version: {VERSION} -> {NEW_VERSION}\n",
            "成功时标准输出应为 Renamed version 加原名、箭头、新名与换行",
        )
        self.assertEqual(err, "", "成功时标准错误应为空")

        # 新进程查看新名称：标题、三条原文、数量与顺序不变，重复条目分别
        # 保留，多行文本仍是一条记录。
        code, out, err = show(self.db, NEW_VERSION)
        self.assertEqual(code, 0, "show v2 退出码应为 0")
        self.assertEqual(err, "", "show v2 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_V2,
            "改名后标题与变更应仍属于同一份草稿，按原顺序输出",
        )

        # 旧名称不再存在。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 1, "show v1 退出码应为 1")
        self.assertEqual(out, "", "show v1 标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {VERSION}\n",
            "旧名称应报 Version not found: v1 加末尾换行",
        )

        # 直接核对库内数据：drafts 只剩新名一行且标题不变；changes 三条
        # 记录的版本名全部更新为新名，position 与内容原文、顺序不变。
        conn = sqlite3.connect(self.db)
        try:
            draft_rows = conn.execute(
                "SELECT version, title FROM drafts"
            ).fetchall()
            change_rows = conn.execute(
                "SELECT version, position, content FROM changes"
                " ORDER BY position"
            ).fetchall()
            old_change_rows = conn.execute(
                "SELECT COUNT(*) FROM changes WHERE version = ?",
                (VERSION,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(
            draft_rows,
            [(NEW_VERSION, TITLE)],
            "drafts 中应只剩新名称一行，标题不变",
        )
        self.assertEqual(
            change_rows,
            [(NEW_VERSION, position, content)
             for position, content in enumerate(CHANGES)],
            "三条变更的版本名应随草稿更新，原文、数量与顺序不变",
        )
        self.assertEqual(
            old_change_rows, 0, "旧名称下不得残留任何变更记录"
        )

        # 重命名完成后目录内只有数据库文件，不残留 journal、wal 等。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "重命名完成后目录内应只有数据库文件",
        )


if __name__ == "__main__":
    unittest.main()
