"""release_notes.py add-change 共用草稿访问流程重构后的离线回归测试。

背景：add-change 的数据库访问（文件存在性、drafts 表存在性、版本存在性
检查与连接释放）已改为复用 set-change / remove-change / move-change
共用的 open_draft_for_update，不再保留自己的重复检查。本测试只通过
命令行与外部可观察行为验收重构后的追加流程，不依赖内部实现：

  - 正常追加：固定样例 demo-0.1（标题"预览版"，两条均为"新增预览"的
    重复记录）上执行 add-change demo-0.1 --change "修订提示"，退出码
    0、标准错误为空、标准输出恰为 "Added change: demo-0.1" 加换行；
    随后新进程 show 保留标题与两条重复记录，并仅在末尾新增修订提示；
    export-markdown 按原有格式导出同一顺序；新进程重复读取结果逐字
    相同（重启后结果不变）。
  - 末尾追加与不去重：即使草稿曾删除中间或末尾条目（存储位置留下
    空洞），新记录仍排在所有现存条目之后；相同文本再次提交产生独立
    记录；追加文本中的中文、标点、首尾空格与内部换行原样保存。
  - 错误边界：未提供或重复提供 --change、版本名或追加文本为空或仅含
    空白时，即使数据库路径不存在或缺少 drafts 表也先报 Invalid draft；
    输入有效但数据库文件不存在、合法 SQLite 库没有 drafts 表或查无
    目标版本时报 Version not found。失败均退出 1、标准输出为空、标准
    错误仅含对应消息和一个换行，不创建数据库、不补建表、不插入草稿
    或变更、不改动任何原有内容（只有无关表的数据库保留原有表和记录）。
  - 重构前建立的数据库：按重构前版本的建表语句与插入方式直接用
    sqlite3 建库（不经过当前 release_notes.py），重构后的 add-change
    与 show 仍能正常读取并在其中追加。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推；比较完整
UTF-8 文本而非片段。

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

# 验收固定样例：版本 demo-0.1，标题"预览版"，两条均为新增预览的重复记录。
VERSION = "demo-0.1"
TITLE = "预览版"
CHANGES = ["新增预览", "新增预览"]
APPENDED_CHANGE = "修订提示"

OTHER_VERSION = "demo-0.2"
OTHER_TITLE = "另一版本说明"
OTHER_CHANGES = ["其他变更"]

# 追加后新进程 show 的期望输出（按 README 固定格式独立写明）：标题不变，
# 两条重复记录仍在前，新条目仅位于末尾。
EXPECTED_SHOW_AFTER_APPEND = (
    "Version: demo-0.1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修订提示\n"
)

# 同一状态下的期望导出文本（按 README 固定格式独立写明）。
EXPECTED_EXPORT_AFTER_APPEND = (
    "# demo-0.1\n"
    "\n"
    "预览版\n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修订提示\n"
)

# 第二次追加的文本：中文、标点、首尾空格与内部换行都必须原样保存。
SPACY_CHANGE = "  追加，首行：\n第二行  "

# 重构前版本的建表语句（与重构后相同，本次重构不调整表结构）。
PRE_REFACTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    version TEXT PRIMARY KEY,
    title   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    version  TEXT NOT NULL,
    position INTEGER NOT NULL,
    content  TEXT NOT NULL,
    FOREIGN KEY (version) REFERENCES drafts(version)
);
"""

# unrelated 表中的示例数据，追加失败后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名与变更文本。
BLANK_NAMES = ["", "   ", " \n\t "]
BLANK_CHANGES = ["", "   ", " \n\t "]


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


def add_change(db_path, version, change):
    return run_cli(db_path, "add-change", version, "--change", change)


def snapshot_database(db_path):
    """读取数据库的全部表结构（sqlite_master 行）与各表内容快照。"""
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


class AddChangeSharedFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-add-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self, changes=CHANGES):
        """按验收固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, changes)
        self.assertEqual((code, out, err), (0, "Created demo-0.1\n", ""))

    def build_pre_refactor_database(self):
        """按重构前版本的建表与插入方式直接建库，不经过当前命令行。

        表结构与重构前的 SCHEMA 逐字相同；两条重复变更按 create 的既有
        约定以 0 起始的连续 position 写入。
        """
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(PRE_REFACTOR_SCHEMA)
            with conn:
                conn.execute(
                    "INSERT INTO drafts(version, title) VALUES (?, ?)",
                    (VERSION, TITLE),
                )
                conn.executemany(
                    "INSERT INTO changes(version, position, content)"
                    " VALUES (?, ?, ?)",
                    [
                        (VERSION, position, content)
                        for position, content in enumerate(CHANGES)
                    ],
                )
        finally:
            conn.close()

    def read_database_bytes(self):
        with open(self.db, "rb") as handle:
            return handle.read()

    def read_change_rows(self, version=VERSION):
        """直接读取库内指定版本的 (position, content) 行，按展示顺序排列。"""
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (version,),
            ).fetchall()
        finally:
            conn.close()

    def assert_invalid_draft(self, result):
        code, out, err = result
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加一个换行"
        )

    def assert_version_not_found(self, result, version):
        code, out, err = result
        self.assertEqual(code, 1, "版本不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {version}\n",
            "标准错误应恰为 Version not found 加一个换行，版本名保留原文",
        )
        self.assertNotIn("Traceback", err, "标准错误不得包含异常堆栈")


class TestAddChangeAcceptanceFlow(AddChangeSharedFlowTestCase):
    """验收主流程：固定样例追加后的消息、show、export 与重启一致性。"""

    def test_append_then_show_and_export_in_fresh_processes(self):
        self.create_demo_01()

        # 成功追加：退出 0，stderr 为空，stdout 恰为固定成功消息。
        code, out, err = add_change(self.db, VERSION, APPENDED_CHANGE)
        self.assertEqual(code, 0, "add-change 成功退出码应为 0")
        self.assertEqual(
            out,
            f"Added change: {VERSION}\n",
            "成功时标准输出应为 Added change 加版本名原文与一个换行",
        )
        self.assertEqual(err, "", "成功时标准错误应为空")

        # 新进程 show：标题与两条重复记录保留，新条目仅在末尾。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_APPEND,
            "追加后标题与两条重复记录不变，新条目仅位于末尾",
        )

        # 新进程 export-markdown：原有格式与同一顺序。
        code, out, err = run_cli(self.db, "export-markdown", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_EXPORT_AFTER_APPEND,
            "导出文本应按原有格式输出与 show 相同的顺序",
        )

        # 重启后结果不变：再由两个相互独立的新进程读取，逐字一致。
        first_show = run_cli(self.db, "show", VERSION)
        second_show = run_cli(self.db, "show", VERSION)
        self.assertEqual(first_show, second_show, "两次独立进程 show 应逐字相同")
        first_export = run_cli(self.db, "export-markdown", VERSION)
        second_export = run_cli(self.db, "export-markdown", VERSION)
        self.assertEqual(
            first_export, second_export, "两次独立进程导出应逐字相同"
        )
        self.assertEqual(first_show[1], EXPECTED_SHOW_AFTER_APPEND)
        self.assertEqual(first_export[1], EXPECTED_EXPORT_AFTER_APPEND)

        # 操作结束后连接已释放：目录内只有数据库文件，不残留 journal、wal。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "追加完成后目录内应只有数据库文件",
        )

    def test_duplicate_text_creates_independent_record(self):
        self.create_demo_01()

        # 再次提交与已有记录完全相同的文本：新增独立记录，不去重。
        duplicate = CHANGES[0]
        code, out, err = add_change(self.db, VERSION, duplicate)
        self.assertEqual((code, out, err), (0, f"Added change: {VERSION}\n", ""))
        self.assertEqual(
            self.read_change_rows(),
            [(0, duplicate), (1, duplicate), (2, duplicate)],
            "相同文本再次提交必须产生独立的新记录，不能去重",
        )

    def test_chinese_punctuation_spaces_and_internal_newlines_preserved(self):
        self.create_demo_01()
        code, _, _ = add_change(self.db, VERSION, APPENDED_CHANGE)
        self.assertEqual(code, 0)

        # 中文、标点、首尾空格与内部换行都按原文保存；多行条目只在首行
        # 前加 "- "，内部换行原样保留。
        code, out, err = add_change(self.db, VERSION, SPACY_CHANGE)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_SHOW_AFTER_APPEND + f"- {SPACY_CHANGE}\n",
            "追加文本的中文、标点、首尾空格与内部换行应原样保存",
        )
        self.assertEqual(
            self.read_change_rows()[-1],
            (3, SPACY_CHANGE),
            "库内保存的内容与首尾空格、内部换行必须逐字一致",
        )

    def test_other_version_and_title_remain_unchanged(self):
        self.create_demo_01()
        code, _, _ = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual(code, 0)

        code, _, _ = add_change(self.db, VERSION, APPENDED_CHANGE)
        self.assertEqual(code, 0)

        # 被追加版本的标题不变，其他版本标题与变更均不变。
        conn = sqlite3.connect(self.db)
        try:
            title_row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (VERSION,)
            ).fetchone()
            other_title_row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (OTHER_VERSION,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(title_row, (TITLE,), "标题不随追加改变")
        self.assertEqual(
            other_title_row, (OTHER_TITLE,), "其他版本的标题不受影响"
        )
        self.assertEqual(
            self.read_change_rows(OTHER_VERSION),
            list(enumerate(OTHER_CHANGES)),
            "其他版本的变更不受影响",
        )


class TestAppendAfterRemoval(AddChangeSharedFlowTestCase):
    """删除中间或末尾条目后，追加仍排在所有现存条目之后。"""

    def test_append_after_middle_entry_removed(self):
        # 三条变更，删除中间第二条（position 1 留洞），追加仍在末尾。
        self.create_demo_01(changes=["第一条", "第二条", "第三条"])
        code, _, _ = run_cli(self.db, "remove-change", VERSION, "--index", "2")
        self.assertEqual(code, 0)

        code, out, err = add_change(self.db, VERSION, "末尾追加")
        self.assertEqual((code, out, err), (0, f"Added change: {VERSION}\n", ""))

        # 存储位置留下空洞（0、2、3），但展示顺序中追加条目排在最后。
        self.assertEqual(
            self.read_change_rows(),
            [(0, "第一条"), (2, "第三条"), (3, "末尾追加")],
            "删除中间条目后追加仍取 MAX(position)+1，展示顺序在末尾",
        )
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览版\n"
            "- 第一条\n"
            "- 第三条\n"
            "- 末尾追加\n",
            "删除中间条目后新记录仍排在所有现存条目之后",
        )

    def test_append_after_last_entry_removed(self):
        # 三条变更，删除末尾第三条后现存记录为 position 0、1，追加位置取
        # 现存 MAX(position)+1 = 2（重用被删位置），展示顺序仍在最后；
        # 公开保证的是展示顺序排在末尾，与中间删除留洞的情形分别覆盖。
        self.create_demo_01(changes=["第一条", "第二条", "第三条"])
        code, _, _ = run_cli(self.db, "remove-change", VERSION, "--index", "3")
        self.assertEqual(code, 0)

        code, _, _ = add_change(self.db, VERSION, "末尾追加")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.read_change_rows(),
            [(0, "第一条"), (1, "第二条"), (2, "末尾追加")],
            "删除末尾条目后追加仍排在现存条目最后",
        )

    def test_append_after_removal_exports_same_order(self):
        # 删除后追加的状态经 export-markdown 导出，顺序与 show 一致。
        self.create_demo_01(changes=["第一条", "第二条", "第三条"])
        code, _, _ = run_cli(self.db, "remove-change", VERSION, "--index", "2")
        self.assertEqual(code, 0)
        code, _, _ = add_change(self.db, VERSION, "末尾追加")
        self.assertEqual(code, 0)

        code, out, err = run_cli(self.db, "export-markdown", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "# demo-0.1\n"
            "\n"
            "预览版\n"
            "\n"
            "- 第一条\n"
            "- 第三条\n"
            "- 末尾追加\n",
            "删除后追加的导出顺序应与 show 相同",
        )


class TestAddChangeErrorBoundaries(AddChangeSharedFlowTestCase):
    """错误优先级与失败不写入：校验先于查库，失败不建库、不补表、不改数据。"""

    def test_invalid_input_reported_before_database_check(self):
        # 路径不存在、空库、只有无关表三类数据库上，无效输入都优先报
        # Invalid draft；且已存在的数据库在整组失败后逐字节不变。
        empty_db = os.path.join(self._tmpdir.name, "empty.sqlite")
        unrelated_db = os.path.join(self._tmpdir.name, "unrelated.sqlite")

        # 合法但无任何表的 SQLite 库（建表后立即删表，保留有效文件头）。
        conn = sqlite3.connect(empty_db)
        try:
            conn.execute("CREATE TABLE _init (x INTEGER)")
            conn.execute("DROP TABLE _init")
            conn.commit()
        finally:
            conn.close()
        conn = sqlite3.connect(unrelated_db)
        try:
            conn.execute(
                "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)"
            )
            conn.execute("INSERT INTO unrelated(id, note) VALUES (1, '示例数据')")
            conn.commit()
        finally:
            conn.close()

        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        cases = [
            ("missing-path", missing_db, False),
            ("empty", empty_db, True),
            ("unrelated", unrelated_db, True),
        ]
        for label, db_path, exists in cases:
            with self.subTest(case=label):
                if exists:
                    with open(db_path, "rb") as handle:
                        bytes_before = handle.read()
                    snapshot_before = snapshot_database(db_path)

                # 未提供 --change。
                self.assert_invalid_draft(run_cli(db_path, "add-change", VERSION))
                # 重复提供 --change。
                self.assert_invalid_draft(
                    run_cli(
                        db_path, "add-change", VERSION,
                        "--change", "a", "--change", "b",
                    )
                )
                # 版本名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(add_change(db_path, name, "有效文本"))
                # 追加文本为空或仅含空白。
                for text in BLANK_CHANGES:
                    self.assert_invalid_draft(add_change(db_path, VERSION, text))

                if exists:
                    with open(db_path, "rb") as handle:
                        self.assertEqual(
                            handle.read(), bytes_before, "失败追加不得改动数据库"
                        )
                    self.assertEqual(
                        snapshot_database(db_path),
                        snapshot_before,
                        "失败追加前后表结构与原有数据必须完全一致",
                    )
                else:
                    self.assertFalse(
                        os.path.exists(db_path), "无效输入不得创建数据库文件"
                    )

        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["empty.sqlite", "unrelated.sqlite"],
            "无效输入不得产生任何新文件或附属文件",
        )

    def test_missing_database_path_not_found_without_creating_file(self):
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_version_not_found(
            add_change(missing_db, VERSION, APPENDED_CHANGE), VERSION
        )
        self.assertFalse(
            os.path.exists(missing_db), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )

    def test_database_without_drafts_table_not_found_and_untouched(self):
        # 两类缺表库各使用独立路径：完全无表的合法库、只有 unrelated 表
        # 且带一行示例数据的库。
        empty_db = os.path.join(self._tmpdir.name, "empty.sqlite")
        unrelated_db = os.path.join(self._tmpdir.name, "unrelated.sqlite")

        conn = sqlite3.connect(empty_db)
        try:
            conn.execute("CREATE TABLE _init (x INTEGER)")
            conn.execute("DROP TABLE _init")
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(unrelated_db)
        try:
            conn.execute(
                "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)"
            )
            conn.executemany(
                "INSERT INTO unrelated(id, note) VALUES (?, ?)", UNRELATED_ROWS
            )
            conn.commit()
        finally:
            conn.close()

        for label, db_path in (("empty", empty_db), ("unrelated", unrelated_db)):
            with self.subTest(case=label):
                with open(db_path, "rb") as handle:
                    bytes_before = handle.read()
                snapshot_before = snapshot_database(db_path)

                self.assert_version_not_found(
                    add_change(db_path, "demo-0.9", APPENDED_CHANGE), "demo-0.9"
                )

                # 逐字节、表结构、各表数据均不变，不补建 drafts/changes。
                with open(db_path, "rb") as handle:
                    self.assertEqual(
                        handle.read(), bytes_before, "缺表时失败追加不得改动数据库"
                    )
                snapshot_after = snapshot_database(db_path)
                self.assertEqual(snapshot_after, snapshot_before)
                table_names = {
                    row[1] for row in snapshot_after[0] if row[0] == "table"
                }
                self.assertNotIn("drafts", table_names, "不得补建 drafts 表")
                self.assertNotIn("changes", table_names, "不得补建 changes 表")

        # 只有无关表的数据库：原有表与记录原样保留。
        _, tables = snapshot_database(unrelated_db)
        self.assertEqual(tables["unrelated"], UNRELATED_ROWS, "无关表记录必须保留")

        # 整组失败后目录内只有两个预先建立的库，无 journal/wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["empty.sqlite", "unrelated.sqlite"],
            "失败追加不得产生任何附属文件",
        )

    def test_drafts_table_without_version_not_found_and_untouched(self):
        self.create_demo_01()
        bytes_before = self.read_database_bytes()

        # 草稿表存在但查无版本：保留既有版本不存在结果。
        self.assert_version_not_found(
            add_change(self.db, "demo-0.9", APPENDED_CHANGE), "demo-0.9"
        )
        # 仅大小写不同同样视为不存在，消息保留传入原文。
        self.assert_version_not_found(
            add_change(self.db, VERSION.upper(), "不应写入"), VERSION.upper()
        )
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "查无版本时不得改动数据库",
        )
        self.assertEqual(
            self.read_change_rows(),
            list(enumerate(CHANGES)),
            "版本不匹配时不得追加任何变更",
        )


class TestAddChangeOnPreRefactorDatabase(AddChangeSharedFlowTestCase):
    """重构前建立的数据库：重构后的 add-change 与 show 正常读取与追加。"""

    def test_append_in_database_built_before_refactor(self):
        self.build_pre_refactor_database()

        # 重构后的 add-change 在重构前建立的库上执行固定命令。
        code, out, err = add_change(self.db, VERSION, APPENDED_CHANGE)
        self.assertEqual(
            (code, out, err), (0, f"Added change: {VERSION}\n", "")
        )

        # 新进程 show：标题与两条重复记录保留，新条目仅在末尾。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_AFTER_APPEND, "追加后 show 不符")

        # 库内核对：仅在末尾插入 position 2 的新记录，旧记录原样保留。
        self.assertEqual(
            self.read_change_rows(),
            [(0, "新增预览"), (1, "新增预览"), (2, APPENDED_CHANGE)],
            "重构前建立的库中新条目只追加到末尾",
        )


if __name__ == "__main__":
    unittest.main()
