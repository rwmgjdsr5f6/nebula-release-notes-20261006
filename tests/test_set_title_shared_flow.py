"""release_notes.py set-title 共用草稿访问流程重构后的离线回归测试。

背景：set-title 的数据库访问（文件存在性、drafts 表存在性、版本存在性
检查与连接释放）已改为复用 add-change / set-change / remove-change /
move-change 共用的 open_draft_for_update，不再保留自己的重复检查。本
测试只通过命令行与外部可观察行为验收重构后的标题修订流程，不依赖内部
实现：

  - 验收主流程：固定样例 v1（标题"预览版"，三条变更依次为两条相同的
    "新增预览"与一条由第一行、第二行组成的多行变更）上执行
    set-title v1 --title " 修订#标题 "，退出码 0、标准错误为空、标准
    输出恰为 "Updated title: v1" 加一个换行；随后新进程 show v1 的
    标题行为 "Title:  修订#标题 "（冒号后一个空格再加标题自带的前导
    空格，标题尾随空格同样保留），三条变更的内容与排列逐字不变；
    export-markdown 在新进程中读到同一新标题；再次提交相同标题仍成功。
  - 只改目标草稿标题：版本名、变更原文、重复记录、条目数量与展示顺序，
    以及其他草稿的标题与变更均保持原样。
  - 错误边界：省略 --title，版本名或新标题为空、仅含空白时，即使数据库
    路径不存在或缺少 drafts 表也先报 Invalid draft；输入有效但数据库
    文件不存在、合法 SQLite 库没有 drafts 表（空库或只有无关表）或查无
    目标版本时报 Version not found（大小写不同同样视为不存在，消息保留
    传入原文）。失败均退出 1、标准输出为空、标准错误仅含对应消息和一个
    换行，不创建数据库、不补建表、不改动任何原有内容。
  - 只有合法 drafts 表及目标记录而没有 changes 表：set-title 只更新
    drafts，修订仍成功，且不补建 changes 表。
  - 重构前建立的数据库：按既有建表语句与插入方式直接用 sqlite3 建库
    （不经过当前 release_notes.py），重构后的 set-title 与 show 仍能
    正常读取并修订其中的标题。

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

# 验收固定样例：版本 v1，原标题"预览版"；三条变更依次为两条相同的新增
# 预览与一条由第一行、第二行组成的多行变更。
VERSION = "v1"
ORIGINAL_TITLE = "预览版"
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

OTHER_VERSION = "v2"
OTHER_TITLE = "另一版本说明"
OTHER_CHANGES = ["其他变更"]

# 新标题：首尾各一个空格，含中文与 Markdown 字符 #，必须按输入原文保存。
NEW_TITLE = " 修订#标题 "

MISSING_VERSION = "v9"

# 修订前的 show 期望文本（按 show 格式独立写明）。
EXPECTED_SHOW_ORIGINAL = (
    "Version: v1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 成功修订后的 show 期望文本：仅标题行变化——"Title: " 后接标题原文，
# 标题自带前导空格故冒号后呈现两个空格，尾随空格在换行前保留；三条变更
# 的原文、数量与顺序逐字不变。
EXPECTED_SHOW_UPDATED = (
    "Version: v1\n"
    "Title:  修订#标题 \n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 成功修订后的导出文本（按 README 固定格式独立写明）：头部与变更段格式
# 不变，标题段为新标题原文加两个换行，首尾空格与 # 原样保留。
EXPECTED_EXPORT_UPDATED = (
    "# v1\n"
    "\n"
    " 修订#标题 \n"
    "\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 既有建表语句（本次重构不调整表结构）。
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

# unrelated 表中的示例数据，修订失败后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名与新标题。
BLANK_NAMES = ["", "   ", "\t", " \n\t "]
BLANK_TITLES = ["", "   ", "\n", "\t", " \n\t "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、标点、# 与空格精确可比。
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


class SetTitleSharedFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-title-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_v1(self):
        """按验收固定样例创建 v1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, ORIGINAL_TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, "Created v1\n", ""))

    def build_pre_refactor_database(self):
        """按既有建表与插入方式直接建库，不经过当前命令行。

        表结构与 SCHEMA 逐字相同；三条变更按 create 的既有约定以 0 起始
        的连续 position 写入，模拟重构前版本建立的数据库。
        """
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(PRE_REFACTOR_SCHEMA)
            with conn:
                conn.execute(
                    "INSERT INTO drafts(version, title) VALUES (?, ?)",
                    (VERSION, ORIGINAL_TITLE),
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

    def build_drafts_only_database(self):
        """建立只有合法 drafts 表且含 v1 行、没有 changes 表的数据库。"""
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "CREATE TABLE drafts ("
                " version TEXT PRIMARY KEY,"
                " title TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "INSERT INTO drafts(version, title) VALUES (?, ?)",
                (VERSION, ORIGINAL_TITLE),
            )
            conn.commit()
        finally:
            conn.close()

    def build_empty_sqlite_database(self, db_path):
        """在指定路径建立不含任何表的合法 SQLite 数据库（具备文件头）。"""
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE _init (x INTEGER)")
            conn.execute("DROP TABLE _init")
            conn.commit()
        finally:
            conn.close()

    def build_unrelated_table_database(self, db_path):
        """在指定路径建立只有 unrelated 表且保存一行示例数据的数据库。"""
        conn = sqlite3.connect(db_path)
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

    def read_database_bytes(self):
        with open(self.db, "rb") as handle:
            return handle.read()

    def read_draft_rows(self):
        """直接读取库内 drafts 全部 (version, title) 行与 v1 的变更行。"""
        conn = sqlite3.connect(self.db)
        try:
            draft_rows = conn.execute(
                "SELECT version, title FROM drafts ORDER BY version"
            ).fetchall()
            change_rows = conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (VERSION,),
            ).fetchall()
        finally:
            conn.close()
        return draft_rows, change_rows

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


class TestSetTitleAcceptanceFlow(SetTitleSharedFlowTestCase):
    """验收主流程：固定样例、固定命令、固定输出，新进程 show/export 核对。"""

    def test_retitle_v1_then_show_and_export_in_fresh_processes(self):
        self.create_v1()

        # 修订前先固定初始 show 文本。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_ORIGINAL, ""))

        # 即 python release_notes.py --db notes.sqlite \
        #   set-title v1 --title " 修订#标题 "
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0, "set-title 成功退出码应为 0")
        self.assertEqual(err, "", "set-title 标准错误应为空")
        self.assertEqual(
            out,
            "Updated title: v1\n",
            "标准输出应恰为 Updated title: v1 加一个换行",
        )

        # 新进程 show：标题行为 Title:  修订#标题 （原文两端空格保留），
        # 三条变更的内容与排列不变。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_SHOW_UPDATED,
            "新标题应原样保存，三条变更的原文、数量与排列逐字不变",
        )

        # 新进程 export-markdown 同样读到新标题，格式与 show 顺序一致。
        code, out, err = run_cli(self.db, "export-markdown", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_EXPORT_UPDATED,
            "导出应读到新标题原文，首尾空格与 # 不裁剪、不转义",
        )

        # 重复提交相同标题也算成功（单条 UPDATE 幂等），输出与保存内容不变。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(
            (code, out, err),
            (0, "Updated title: v1\n", ""),
            "重复提交相同标题仍应成功",
        )

        # 重启后结果不变：两个相互独立的新进程 show 逐字相同。
        first_show = run_cli(self.db, "show", VERSION)
        second_show = run_cli(self.db, "show", VERSION)
        self.assertEqual(first_show, second_show, "两次独立进程 show 应逐字相同")
        self.assertEqual(first_show, (0, EXPECTED_SHOW_UPDATED, ""))

        # 操作结束后连接已释放：目录内只有数据库文件，不残留 journal、wal。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "修订完成后目录内应只有数据库文件",
        )

    def test_only_target_title_changes_changes_and_other_draft_untouched(self):
        self.create_v1()
        code, _, _ = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual(code, 0)

        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual((code, out, err), (0, "Updated title: v1\n", ""))

        # 库内核对：仅 v1 的 drafts 行标题变化；v1 的变更仍为 3 条
        # （两条重复记录分别保留），position 与原文不变；v2 不受影响。
        draft_rows, change_rows = self.read_draft_rows()
        self.assertEqual(
            draft_rows,
            [(VERSION, NEW_TITLE), (OTHER_VERSION, OTHER_TITLE)],
            "修订标题不改变版本集合，仅目标草稿标题变化",
        )
        self.assertEqual(
            change_rows,
            list(enumerate(CHANGES)),
            "变更原文、重复记录、条目数量与展示顺序均不受标题修订影响",
        )
        conn = sqlite3.connect(self.db)
        try:
            other_title_row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (OTHER_VERSION,)
            ).fetchone()
            other_change_rows = conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (OTHER_VERSION,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(other_title_row, (OTHER_TITLE,), "其他草稿标题不变")
        self.assertEqual(
            other_change_rows,
            list(enumerate(OTHER_CHANGES)),
            "其他草稿的变更不受影响",
        )


class TestSetTitleErrorBoundaries(SetTitleSharedFlowTestCase):
    """错误优先级与失败不写入：校验先于查库，失败不建库、不补表、不改数据。"""

    def test_invalid_input_reported_before_database_check(self):
        # 路径不存在、空库、只有无关表三类数据库上，无效输入都优先报
        # Invalid draft；且已存在的数据库在整组失败后逐字节不变。
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        empty_db = os.path.join(self._tmpdir.name, "empty.sqlite")
        unrelated_db = os.path.join(self._tmpdir.name, "unrelated.sqlite")
        self.build_empty_sqlite_database(empty_db)
        self.build_unrelated_table_database(unrelated_db)

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

                # 省略 --title。
                self.assert_invalid_draft(
                    run_cli(db_path, "set-title", VERSION)
                )
                # 版本名为空或仅含空白（标题有效）。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        set_title(db_path, name, "有效标题")
                    )
                # 新标题为空或仅含空白。
                for title in BLANK_TITLES:
                    self.assert_invalid_draft(
                        set_title(db_path, VERSION, title)
                    )

                if exists:
                    with open(db_path, "rb") as handle:
                        self.assertEqual(
                            handle.read(), bytes_before, "失败修订不得改动数据库"
                        )
                    self.assertEqual(
                        snapshot_database(db_path),
                        snapshot_before,
                        "失败修订前后表结构与原有数据必须完全一致",
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

    def test_version_not_found_boundaries(self):
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        # 数据库文件不存在：报 Version not found，不创建文件。
        self.assert_version_not_found(
            set_title(missing_db, VERSION, NEW_TITLE), VERSION
        )
        self.assertFalse(
            os.path.exists(missing_db), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )

        # 文件存在但没有 drafts 表：空库与只有 unrelated 表（带示例行）的
        # 库都按版本不存在处理，逐字节不变，不补建 drafts/changes。
        empty_db = os.path.join(self._tmpdir.name, "empty.sqlite")
        unrelated_db = os.path.join(self._tmpdir.name, "unrelated.sqlite")
        self.build_empty_sqlite_database(empty_db)
        self.build_unrelated_table_database(unrelated_db)

        for label, db_path in (("empty", empty_db), ("unrelated", unrelated_db)):
            with self.subTest(case=label):
                with open(db_path, "rb") as handle:
                    bytes_before = handle.read()
                snapshot_before = snapshot_database(db_path)

                self.assert_version_not_found(
                    set_title(db_path, MISSING_VERSION, NEW_TITLE), MISSING_VERSION
                )

                with open(db_path, "rb") as handle:
                    self.assertEqual(
                        handle.read(), bytes_before, "缺表时失败修订不得改动数据库"
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

        # drafts 表存在但查无版本（含仅大小写不同）：报 Version not found，
        # 消息中的版本名保留传入原文，已有数据不变。
        self.create_v1()
        bytes_before = self.read_database_bytes()
        self.assert_version_not_found(
            set_title(self.db, MISSING_VERSION, NEW_TITLE), MISSING_VERSION
        )
        self.assert_version_not_found(
            set_title(self.db, VERSION.upper(), "不应写入"), VERSION.upper()
        )
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "查无版本时不得改动数据库",
        )
        draft_rows, change_rows = self.read_draft_rows()
        self.assertEqual(
            draft_rows, [(VERSION, ORIGINAL_TITLE)], "版本不匹配时不得改写标题"
        )
        self.assertEqual(
            change_rows,
            list(enumerate(CHANGES)),
            "版本不匹配时不得改动任何变更",
        )

        # 整组失败后目录内只有三个预先建立/创建的库，无附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["empty.sqlite", "notes.sqlite", "unrelated.sqlite"],
            "失败修订不得产生任何附属文件",
        )


class TestSetTitleOnDraftsTableWithoutChangesTable(SetTitleSharedFlowTestCase):
    """只有合法 drafts 表及目标记录、没有 changes 表：修订成功且不补表。"""

    def test_retitle_succeeds_without_creating_changes_table(self):
        self.build_drafts_only_database()
        snapshot_before = snapshot_database(self.db)
        table_names_before = {
            row[1] for row in snapshot_before[0] if row[0] == "table"
        }
        self.assertEqual(table_names_before, {"drafts"}, "前置条件：库中只有 drafts 表")

        # set-title 只访问 drafts：没有 changes 表时修订仍成功。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(
            (code, out, err),
            (0, "Updated title: v1\n", ""),
            "只有 drafts 表时修订仍应成功",
        )

        # 重复提交相同标题仍成功，且不补建 changes 表。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual((code, out, err), (0, "Updated title: v1\n", ""))

        # 表结构核对：仍只有 drafts 表（建表 SQL 不变），绝不补建 changes。
        snapshot_after = snapshot_database(self.db)
        master_after, tables_after = snapshot_after
        table_names_after = {
            row[1] for row in master_after if row[0] == "table"
        }
        self.assertEqual(table_names_after, {"drafts"}, "修订不得补建 changes 表")
        self.assertNotIn("changes", tables_after, "库中不得出现 changes 表数据")
        self.assertEqual(
            tables_after["drafts"],
            [(VERSION, NEW_TITLE)],
            "仅目标草稿的标题被修订",
        )
        # drafts 的建表语句与修订前逐字一致。
        self.assertEqual(
            [row for row in master_after if row[1] == "drafts"],
            [row for row in snapshot_before[0] if row[1] == "drafts"],
            "drafts 表结构必须保持不变",
        )

        # 查无版本在只有 drafts 表的库上仍报 Version not found，不补表。
        self.assert_version_not_found(
            set_title(self.db, MISSING_VERSION, "其他标题"), MISSING_VERSION
        )
        snapshot_final = snapshot_database(self.db)
        self.assertEqual(
            {row[1] for row in snapshot_final[0] if row[0] == "table"},
            {"drafts"},
            "失败修订同样不得补建 changes 表",
        )
        self.assertEqual(
            snapshot_final[1]["drafts"],
            [(VERSION, NEW_TITLE)],
            "失败修订不得改动原有记录",
        )

        # 目录内只有数据库文件，不残留 journal、wal 等附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "修订完成后目录内应只有数据库文件",
        )


class TestSetTitleOnPreRefactorDatabase(SetTitleSharedFlowTestCase):
    """重构前建立的数据库：重构后的 set-title 与 show 正常读取与修订。"""

    def test_retitle_in_database_built_before_refactor(self):
        self.build_pre_refactor_database()

        # 重构后的 set-title 在既有库上执行固定验收命令。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(
            (code, out, err), (0, "Updated title: v1\n", "")
        )

        # 新进程 show：标题为新标题原文，三条变更原样保留。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_UPDATED, "修订后 show 不符")

        # 库内核对：仅 drafts 行标题更新，三条变更的 position 与原文不变。
        conn = sqlite3.connect(self.db)
        try:
            title_row = conn.execute(
                "SELECT title FROM drafts WHERE version = ?", (VERSION,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(title_row, (NEW_TITLE,), "标题应按输入原文保存")
        _, change_rows = self.read_draft_rows()
        self.assertEqual(
            change_rows,
            list(enumerate(CHANGES)),
            "既有库中的变更原文、数量与顺序不变",
        )


if __name__ == "__main__":
    unittest.main()
