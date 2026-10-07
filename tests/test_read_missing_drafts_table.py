"""release_notes.py show / export-markdown 读取边界的离线回归测试。

针对同一读取边界（read_draft）：数据库文件存在、能被 SQLite 正常打开，
但库内没有 drafts 表时，show 与 export-markdown 都应把它视为“没有可读
取的版本”，而不是把 no such table 异常与堆栈暴露出来。

覆盖范围（仅通过命令行与外部可观察行为验收）：
  - 两类预制数据库：不含任何表的合法 SQLite 数据库；只有 unrelated
    表且保存一行示例数据的数据库。对二者分别执行 show demo-0.9 与
    export-markdown demo-0.9，均退出码 1、标准输出为空、标准错误恰为
    "Version not found: demo-0.9" 加换行，不输出异常堆栈；
  - 查询为只读：查询前后文件字节、sqlite_master 中的表结构（含建表
    SQL）以及 unrelated 表的原有数据完全一致，不补建 drafts/changes
    表，不插入草稿，目录内不产生发布说明文件或 journal/wal 等附属文件；
  - 路径不存在时仍返回既有的 Version not found，且不创建文件；
  - export-markdown 接收空字符串或仅含空白的版本名时，在上述两类数据
    库上仍优先报 Invalid draft，且不改变数据库；show 不新增空白校验，
    空白名称继续按既有规则（版本不存在）处理；
  - 正常路径：用现有 create 保存 demo-0.1（标题“预览说明”，两条相同的
    “新增预览”和一条内部含换行的“第一行/第二行”），在新进程中 show 与
    export-markdown 的完整输出符合 README 原有格式，重复记录分别保留，
    多行变更只有首行带条目前缀，原文与顺序不变；
  - 已有数据库直接兼容：在只含 unrelated 表（带示例数据）的库上 create
    后，show/export 正常，unrelated 表结构与数据原样保留。

本次只处理缺少 drafts 表的情形；损坏文件、缺少字段、已有草稿却缺少
changes 表等恢复不在覆盖范围内。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，重复执行结果一致。
"""

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

VERSION = "demo-0.9"
NOT_FOUND_ERROR = "Version not found: demo-0.9\n"

# 正常路径固定样例。
OK_VERSION = "demo-0.1"
OK_TITLE = "预览说明"
OK_CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

EXPECTED_SHOW = (
    "Version: demo-0.1\n"
    "Title: 预览说明\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

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

BLANK_NAMES = ["", "   ", " \n\t "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。"""
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


def create_empty_sqlite(path):
    """建立不含任何表的合法 SQLite 数据库（先建表再删除，确保文件头存在）。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE _init (x INTEGER)")
        conn.execute("DROP TABLE _init")
        conn.commit()
    finally:
        conn.close()


def create_unrelated_sqlite(path):
    """建立只有 unrelated 表且含一行示例数据的数据库，无 drafts/changes。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute(
            "INSERT INTO unrelated(id, note) VALUES (?, ?)",
            (1, "示例数据"),
        )
        conn.commit()
    finally:
        conn.close()


def snapshot(path):
    """读取文件字节哈希、全部表结构（含建表 SQL）与各表全部数据。"""
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    conn = sqlite3.connect(path)
    try:
        schema = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
            " ORDER BY type, name"
        ).fetchall()
        data = {}
        for item in schema:
            if item[0] != "table":
                continue
            name = item[1]
            # sqlite 内部表（sqlite_sequence 等）也一并按原文核对。
            data[name] = conn.execute(
                f"SELECT * FROM {name}"  # noqa: S608 - 名称取自库内元数据
            ).fetchall()
    finally:
        conn.close()
    return digest, schema, data


class MissingDraftsTableTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-missing-")
        self.addCleanup(self._tmpdir.cleanup)
        self.empty_db = os.path.join(self._tmpdir.name, "empty.sqlite")
        self.unrelated_db = os.path.join(self._tmpdir.name, "unrelated.sqlite")
        create_empty_sqlite(self.empty_db)
        create_unrelated_sqlite(self.unrelated_db)

    def assert_not_found_without_mutation(self, db_path, command):
        """对指定库执行 show/export：错误契约一致，且文件结构数据逐字节不变。"""
        files_before = set(os.listdir(self._tmpdir.name))
        digest_before, schema_before, data_before = snapshot(db_path)

        code, out, err = run_cli(db_path, command, VERSION)
        self.assertEqual(
            code, 1, f"[{os.path.basename(db_path)}/{command}] 退出码应为 1"
        )
        self.assertEqual(
            out, "", f"[{os.path.basename(db_path)}/{command}] 标准输出应为空"
        )
        self.assertEqual(
            err,
            NOT_FOUND_ERROR,
            f"[{os.path.basename(db_path)}/{command}] 标准错误应恰为"
            " Version not found 加换行，不得输出异常堆栈",
        )
        self.assertNotIn(
            "Traceback", err, "失败时不得向标准错误输出异常堆栈"
        )

        digest_after, schema_after, data_after = snapshot(db_path)
        self.assertEqual(
            digest_after,
            digest_before,
            "只读查询不得改变数据库文件的任何字节",
        )
        self.assertEqual(
            schema_after,
            schema_before,
            "查询不得补建 drafts/changes 或改变任何表结构",
        )
        self.assertEqual(
            data_after,
            data_before,
            "查询不得插入草稿或改变原有数据",
        )
        table_names = {row[1] for row in schema_after if row[0] == "table"}
        self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
        self.assertNotIn("changes", table_names, "不得补建 changes 表")
        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            files_before,
            "查询不得产生发布说明文件或 journal/wal 等附属文件",
        )

    def test_empty_database_show_and_export_report_not_found(self):
        # 不含任何表的合法 SQLite 数据库。
        self.assert_not_found_without_mutation(self.empty_db, "show")
        self.assert_not_found_without_mutation(self.empty_db, "export-markdown")

    def test_unrelated_table_show_and_export_report_not_found(self):
        # 只有 unrelated 表且保存一行示例数据。
        _, schema, data = snapshot(self.unrelated_db)
        self.assertEqual(
            [row[1] for row in schema if row[0] == "table"],
            ["unrelated"],
            "前置条件：库内只有 unrelated 表",
        )
        self.assertEqual(
            data["unrelated"], [(1, "示例数据")], "前置条件：示例数据一行"
        )

        self.assert_not_found_without_mutation(
            self.unrelated_db, "show"
        )
        self.assert_not_found_without_mutation(
            self.unrelated_db, "export-markdown"
        )

        # 两次查询后示例数据仍在。
        _, _, data_after = snapshot(self.unrelated_db)
        self.assertEqual(
            data_after["unrelated"],
            [(1, "示例数据")],
            "unrelated 表的原有数据应原样保留",
        )

    def test_missing_path_still_not_found_without_creating_file(self):
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["empty.sqlite", "unrelated.sqlite"],
        )

        for command in ("show", "export-markdown"):
            with self.subTest(command=command):
                code, out, err = run_cli(db_path, command, VERSION)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, NOT_FOUND_ERROR)
                self.assertFalse(
                    os.path.exists(db_path), "路径不存在时不得创建数据库文件"
                )

    def test_export_blank_name_still_invalid_on_tableless_databases(self):
        # export-markdown 的空白名称校验优先于数据库读取：在两类缺表库上
        # 都先报 Invalid draft，且不改变数据库。
        for db_path in (self.empty_db, self.unrelated_db):
            label = os.path.basename(db_path)
            digest_before, schema_before, data_before = snapshot(db_path)
            for index, name in enumerate(BLANK_NAMES):
                with self.subTest(db=label, name=name):
                    code, out, err = run_cli(
                        db_path, "export-markdown", name
                    )
                    self.assertEqual(code, 1, f"[{label}] 空白名称退出码应为 1")
                    self.assertEqual(out, "", f"[{label}] 标准输出应为空")
                    self.assertEqual(
                        err,
                        "Invalid draft\n",
                        f"[{label}] 标准错误应恰为 Invalid draft",
                    )
            digest_after, schema_after, data_after = snapshot(db_path)
            self.assertEqual((digest_after, schema_after, data_after),
                             (digest_before, schema_before, data_before),
                             f"[{label}] 空白名称失败不得改变数据库")

    def test_show_blank_name_keeps_existing_rule(self):
        # show 不新增空白校验：空白名称按既有规则作为版本名精确查找，
        # 缺表库中查不到即报 Version not found（错误消息保留传入原文）。
        blank = "   "
        for db_path in (self.empty_db, self.unrelated_db):
            with self.subTest(db=os.path.basename(db_path)):
                code, out, err = run_cli(db_path, "show", blank)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(err, f"Version not found: {blank}\n")


class NormalPathStillWorksTestCase(unittest.TestCase):
    """修复后正常创建/查看/导出路径与 README 原有格式保持不变。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-ok-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo(self):
        args = ["create", OK_VERSION, "--title", OK_TITLE]
        for change in OK_CHANGES:
            args += ["--change", change]
        code, out, err = run_cli(self.db, *args)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Created demo-0.1\n")
        self.assertEqual(err, "")

    def test_show_and_export_in_new_processes_match_readme_format(self):
        self.create_demo()

        # 在全新进程中查看与导出：重复记录分别保留，多行变更只有首行
        # 带条目前缀，原文与展示顺序不变。
        code, out, err = run_cli(self.db, "show", OK_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)

        code, out, err = run_cli(self.db, "export-markdown", OK_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_EXPORT)

    def test_create_is_compatible_with_existing_unrelated_database(self):
        # 先准备只含 unrelated 表与一行示例数据的库。
        create_unrelated_sqlite(self.db)

        self.create_demo()

        # 新进程查看/导出正常。
        code, out, err = run_cli(self.db, "show", OK_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW)
        code, out, err = run_cli(self.db, "export-markdown", OK_VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_EXPORT)

        # 原有 unrelated 表的结构与示例数据原样保留，drafts/changes
        # 由正常 create 路径建立。
        conn = sqlite3.connect(self.db)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            unrelated_rows = conn.execute(
                "SELECT id, note FROM unrelated"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            {"drafts", "changes", "unrelated"},
            tables & {"drafts", "changes", "unrelated"},
        )
        self.assertEqual(unrelated_rows, [(1, "示例数据")])


if __name__ == "__main__":
    unittest.main()
