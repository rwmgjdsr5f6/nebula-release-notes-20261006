"""release_notes.py set-title 复用共用草稿访问流程重构后的离线回归测试。

背景：set-title 的数据库访问（文件存在性、drafts 表存在性、版本存在性
检查与连接释放）已改为复用 add-change / set-change / remove-change /
move-change 共用的 open_draft_for_update，不再保留自己重复的草稿存在性
处理。本测试只通过命令行与外部可观察行为验收重构后的标题修订流程，不依赖
内部实现；另以 SQLite 只读查询交叉核对草稿原文、条目数量与顺序等既有数据
格式语义。

覆盖范围（期望文本均按固定格式独立写明，不从实际输出反推）：
  - 固定验收样例：版本 v1，原标题“预览版”，三条变更依次为两条相同的
    “新增预览”和一条由第一行、第二行组成的多行变更。执行
    python release_notes.py --db notes.sqlite set-title v1 \
        --title " 修订#标题 "
    后退出码 0、标准错误为空、标准输出恰为 "Updated title: v1" 加一个
    换行；随后在新进程 show v1，标题行为 "Title:  修订#标题 "（冒号后
    呈现两个空格，标题原文两端空格保留），三条变更的内容与排列逐字不变。
  - 仅有合法 drafts 表及目标记录、没有 changes 表的数据库：标题修订仍
    成功，且不补建 changes 表、不产生任何附属文件；新标题经新连接可读。
  - 幂等与隔离：重复提交相同标题仍成功；修订只改目标草稿标题，版本名、
    变更原文、重复记录、条目数量、展示顺序以及其他草稿均保持原样；版本名
    按原文精确匹配且区分大小写。
  - 错误先后顺序与唯一结果：省略 --title，或版本名、新标题为空串或仅含
    空白时，即使数据库路径不存在、库中没有 drafts 表或查无版本也先报
    Invalid draft；输入有效而数据库文件不存在、库中没有 drafts 表（空库
    或只有无关表）或查无版本时报 Version not found。失败均退出 1、标准
    输出为空、标准错误仅含对应消息加一个换行，不创建数据库、不补建表、
    不改动原有记录。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，结束后清理临时数据库，重复执行结果一致。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定验收样例：版本 v1；标题“预览版”；三条变更依次为两条相同文本，以及
# 一条由“第一行”和“第二行”通过恰好一个换行连接的多行变更。
VERSION = "v1"
OTHER_VERSION = "v2"
ORIGINAL_TITLE = "预览版"
OTHER_TITLE = "另一版本标题"
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]
OTHER_CHANGES = ["其他变更"]

# 新标题：首尾各一个空格，含井号这一 Markdown 字符；两端空格必须按原文
# 保存。
NEW_TITLE = " 修订#标题 "

# 修订前的 show 期望文本（按 show 格式独立写明）。
EXPECTED_SHOW_ORIGINAL = (
    "Version: v1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 成功修订后的 show 期望文本：仅标题行变化。标题原文带一个前导空格，故
# “Title:”之后呈现两个空格；末尾一个尾随空格在换行前保留。三条变更的
# 原文、数量与排列逐字不变。
EXPECTED_SHOW_UPDATED = (
    "Version: v1\n"
    "Title:  修订#标题 \n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 第一行\n"
    "第二行\n"
)

# 用于缺表边界的版本与标题。
MISSING_VERSION = "v9"
MISSING_TITLE = "新标题"

# unrelated 表中的示例数据，失败修订后必须原样保留。
UNRELATED_ROWS = [(1, "示例数据")]

# 空字符串与仅含空白（空格、制表符、换行）的版本名与新标题。
BLANK_NAMES = ["", "   ", " \n\t "]
BLANK_TITLES = ["", "   ", "\n", "\t", " \n\t "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、标点、井号与空格精确可比。
    每次调用都是一个全新进程。
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


def show(db_path, version):
    return run_cli(db_path, "show", version)


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


def table_names(db_path):
    """列出库内全部用户/内部表名。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def read_title(db_path, version):
    """经独立连接只读读取目标草稿标题原文。"""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT title FROM drafts WHERE version = ?", (version,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def read_change_rows(db_path, version):
    """直接读取指定版本的 (position, content) 行，按展示顺序排列。"""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT position, content FROM changes"
            " WHERE version = ? ORDER BY position",
            (version,),
        ).fetchall()
    finally:
        conn.close()


class SetTitleSharedFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-title-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_v1(self):
        """按固定验收样例创建 v1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, ORIGINAL_TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, "Created v1\n", ""))

    def read_database_bytes(self):
        with open(self.db, "rb") as handle:
            return handle.read()

    def build_empty_sqlite_database(self, db_path=None):
        """建立不含任何表的合法 SQLite 数据库（具备文件头）。"""
        target = db_path or self.db
        conn = sqlite3.connect(target)
        try:
            conn.execute("CREATE TABLE _init (x INTEGER)")
            conn.execute("DROP TABLE _init")
            conn.commit()
        finally:
            conn.close()

    def build_unrelated_table_database(self, db_path=None):
        """建立只有 unrelated 表且保存一行示例数据的数据库。"""
        target = db_path or self.db
        conn = sqlite3.connect(target)
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

    def build_drafts_only_database(self):
        """建立只有 drafts 表、含目标记录但没有 changes 表的数据库。

        直接用 sqlite3 建库（不经过 release_notes.py），模拟既有 SQLite
        文件继续直接使用：只有合法 drafts 表与目标版本行，没有 changes
        表，也没有因 AUTOINCREMENT 产生的 sqlite_sequence。
        """
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "CREATE TABLE drafts ("
                "version TEXT PRIMARY KEY, title TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO drafts(version, title) VALUES (?, ?)",
                (VERSION, ORIGINAL_TITLE),
            )
            conn.commit()
        finally:
            conn.close()

    def assert_invalid_draft(self, result, label=""):
        code, out, err = result
        prefix = f"[{label}] " if label else ""
        self.assertEqual(code, 1, f"{prefix}无效输入退出码应为 1")
        self.assertEqual(out, "", f"{prefix}失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            f"{prefix}标准错误应恰为 Invalid draft 加一个换行",
        )
        # 唯一结果：标准错误只有一行，不含异常堆栈。
        self.assertNotIn("Traceback", err, f"{prefix}标准错误不得包含异常堆栈")

    def assert_version_not_found(self, result, version, label=""):
        code, out, err = result
        prefix = f"[{label}] " if label else ""
        self.assertEqual(code, 1, f"{prefix}版本不存在时退出码应为 1")
        self.assertEqual(out, "", f"{prefix}失败时标准输出应为空")
        self.assertEqual(
            err,
            f"Version not found: {version}\n",
            f"{prefix}标准错误应恰为 Version not found 加一个换行，"
            "版本名保留原文",
        )
        self.assertNotIn("Traceback", err, f"{prefix}标准错误不得包含异常堆栈")


class TestSetTitleV1Acceptance(SetTitleSharedFlowTestCase):
    """固定验收主流程：v1 修订后消息固定，新进程 show 仅标题行变化。"""

    def test_retitle_v1_then_show_matches_fixed_sample(self):
        self.create_v1()

        # 修订前先固定初始 show 文本。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_ORIGINAL, ""))

        # 即 python release_notes.py --db notes.sqlite \
        #   set-title v1 --title " 修订#标题 "
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0, "set-title 退出码应为 0")
        self.assertEqual(err, "", "set-title 标准错误应为空")
        self.assertEqual(
            out,
            "Updated title: v1\n",
            "set-title 标准输出应恰为 Updated title: v1 加一个换行",
        )

        # 随后在新进程 show v1：标题行为 Title:  修订#标题（冒号后两个
        # 空格，原文两端空格保留），三条变更的内容与排列不变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show 退出码应为 0")
        self.assertEqual(err, "", "show 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_UPDATED,
            "新标题两端空格与井号原样保存，三条变更内容与排列逐字不变",
        )

        # 库内交叉核对：只改标题；版本名仍是 v1，变更仍为 3 条，重复记录
        # 分别保留，position 与原文不变。
        self.assertEqual(read_title(self.db, VERSION), NEW_TITLE)
        self.assertEqual(
            read_change_rows(self.db, VERSION),
            list(enumerate(CHANGES)),
            "修订标题不得改动任何变更的原文、数量与顺序",
        )

        # 操作结束后连接已释放：目录内只有数据库文件，不残留 journal、wal。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "修订完成后目录内应只有数据库文件",
        )

    def test_resubmitting_same_title_still_succeeds(self):
        self.create_v1()

        # 重复提交相同标题也算成功（幂等），结果逐字一致。
        first = set_title(self.db, VERSION, NEW_TITLE)
        second = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(
            first, (0, "Updated title: v1\n", ""), "首次修订应成功"
        )
        self.assertEqual(
            second, (0, "Updated title: v1\n", ""), "重复提交相同标题仍应成功"
        )
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_UPDATED, ""))

    def test_other_draft_and_its_changes_remain_unchanged(self):
        self.create_v1()
        code, _, _ = create(
            self.db, OTHER_VERSION, OTHER_TITLE, OTHER_CHANGES
        )
        self.assertEqual(code, 0)

        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual((code, out, err), (0, "Updated title: v1\n", ""))

        # 目标草稿只改标题；其他草稿的标题与变更完全不受影响。
        self.assertEqual(read_title(self.db, VERSION), NEW_TITLE)
        self.assertEqual(
            read_title(self.db, OTHER_VERSION),
            OTHER_TITLE,
            "其他草稿标题不得变化",
        )
        self.assertEqual(
            read_change_rows(self.db, OTHER_VERSION),
            list(enumerate(OTHER_CHANGES)),
            "其他草稿的变更不得变化",
        )

    def test_version_match_is_exact_and_case_sensitive(self):
        self.create_v1()
        bytes_before = self.read_database_bytes()

        # 仅大小写不同的版本名视为不存在，消息保留传入原文，且不写入。
        self.assert_version_not_found(
            set_title(self.db, "V1", "不应写入"), "V1"
        )
        self.assertEqual(read_title(self.db, VERSION), ORIGINAL_TITLE)
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "大小写不匹配时不得改动数据库",
        )


class TestSetTitleWithoutChangesTable(SetTitleSharedFlowTestCase):
    """只有 drafts 表及目标记录、没有 changes 表：修订成功且不补建表。"""

    def test_retitle_succeeds_without_creating_changes_table(self):
        self.build_drafts_only_database()
        # 修订前确认只有 drafts 表。
        self.assertEqual(table_names(self.db), {"drafts"})

        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0, "没有 changes 表时修订退出码仍应为 0")
        self.assertEqual(err, "", "修订成功时标准错误应为空")
        self.assertEqual(
            out,
            "Updated title: v1\n",
            "标准输出应恰为 Updated title: v1 加一个换行",
        )

        # 不补建 changes 表，也不产生 sqlite_sequence 等内部表或附属文件。
        self.assertEqual(
            table_names(self.db),
            {"drafts"},
            "修订不得补建 changes 表或产生其他内部表",
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "修订后目录内应只有数据库文件，无 journal/wal 等附属文件",
        )

        # 经新连接可读：标题已更新，版本名仍是 v1，drafts 行原样保留其余列。
        self.assertEqual(read_title(self.db, VERSION), NEW_TITLE)
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT version, title FROM drafts ORDER BY version"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [(VERSION, NEW_TITLE)], "仅目标草稿标题被更新")

        # 再次提交相同标题仍成功，且依旧不补建 changes 表。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual((code, out, err), (0, "Updated title: v1\n", ""))
        self.assertEqual(
            table_names(self.db),
            {"drafts"},
            "幂等修订仍不得补建 changes 表",
        )


class TestSetTitleErrorBoundaries(SetTitleSharedFlowTestCase):
    """错误先后顺序与失败不写入：校验先于查库，失败不建库、不补表、不改数据。"""

    def test_invalid_input_reported_before_any_database_check(self):
        # 路径不存在、空库、只有无关表三类数据库上，无效输入都优先报
        # Invalid draft；已存在的数据库在整组失败后逐字节不变。
        empty_db = os.path.join(self._tmpdir.name, "empty.sqlite")
        unrelated_db = os.path.join(self._tmpdir.name, "unrelated.sqlite")
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
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
                    run_cli(db_path, "set-title", MISSING_VERSION), label
                )
                # 版本名为空或仅含空白。
                for name in BLANK_NAMES:
                    self.assert_invalid_draft(
                        set_title(db_path, name, "有效标题"), label
                    )
                # 新标题为空或仅含空白（空格、换行、制表符）。
                for title in BLANK_TITLES:
                    self.assert_invalid_draft(
                        set_title(db_path, MISSING_VERSION, title), label
                    )

                if exists:
                    with open(db_path, "rb") as handle:
                        self.assertEqual(
                            handle.read(),
                            bytes_before,
                            f"[{label}] 失败修订不得改动数据库",
                        )
                    self.assertEqual(
                        snapshot_database(db_path),
                        snapshot_before,
                        f"[{label}] 失败修订前后表结构与数据必须一致",
                    )
                else:
                    self.assertFalse(
                        os.path.exists(db_path),
                        f"[{label}] 无效输入不得创建数据库文件",
                    )

        # 整组失败后目录内只有两个预先建立的库，无其他附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            ["empty.sqlite", "unrelated.sqlite"],
            "无效输入不得产生任何新文件或附属文件",
        )

    def test_missing_database_path_not_found_without_creating_file(self):
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_version_not_found(
            set_title(missing_db, MISSING_VERSION, MISSING_TITLE),
            MISSING_VERSION,
            "missing-path",
        )
        self.assertFalse(
            os.path.exists(missing_db), "版本不存在时不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "失败后目录内应无任何文件"
        )

    def test_database_without_drafts_table_not_found_and_untouched(self):
        # 完全空库与只有无关表两类数据库：有效输入也统一报版本不存在，
        # 不补建 drafts/changes、不改动原有表与记录。
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
                    set_title(db_path, MISSING_VERSION, MISSING_TITLE),
                    MISSING_VERSION,
                    label,
                )

                with open(db_path, "rb") as handle:
                    self.assertEqual(
                        handle.read(),
                        bytes_before,
                        f"[{label}] 缺表时失败修订不得改动数据库",
                    )
                snapshot_after = snapshot_database(db_path)
                self.assertEqual(snapshot_after, snapshot_before)
                self.assertNotIn(
                    "drafts", table_names(db_path), f"[{label}] 不得补建 drafts 表"
                )
                self.assertNotIn(
                    "changes", table_names(db_path), f"[{label}] 不得补建 changes 表"
                )

        # 只有无关表的数据库：原有表与示例行原样保留。
        _, tables = snapshot_database(unrelated_db)
        self.assertEqual(
            tables["unrelated"], UNRELATED_ROWS, "无关表记录必须原样保留"
        )

    def test_drafts_table_without_version_not_found_and_untouched(self):
        self.create_v1()
        bytes_before = self.read_database_bytes()

        # drafts 表存在但查无版本：报版本不存在，已有数据逐字节不变。
        self.assert_version_not_found(
            set_title(self.db, MISSING_VERSION, MISSING_TITLE),
            MISSING_VERSION,
            "missing-version",
        )
        self.assertEqual(
            self.read_database_bytes(),
            bytes_before,
            "查无版本时不得改动数据库",
        )
        self.assertEqual(read_title(self.db, VERSION), ORIGINAL_TITLE)
        self.assertEqual(
            read_change_rows(self.db, VERSION),
            list(enumerate(CHANGES)),
            "查无版本时不得改动任何变更",
        )


if __name__ == "__main__":
    unittest.main()
