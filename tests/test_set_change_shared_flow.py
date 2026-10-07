"""release_notes.py set-change 共用数据库访问流程重构后的离线回归测试。

背景：set-change 的数据库访问（文件存在性、drafts 表存在性、版本存在性
检查与连接释放）已改为复用 remove-change / move-change 共用的
open_draft_for_update，不再保留自己的重复检查。本测试只通过命令行与
外部可观察行为验收重构后的流程，不依赖内部实现：

  - 正常替换：固定样例 demo-0.1（标题"预览说明"，三条变更依次为
    新增预览、新增预览、含内部换行的"第一行\\n第二行"）上执行
    set-change demo-0.1 --index 02 --change "修订提示"，退出码 0、
    标准错误为空、标准输出恰为 "Updated change: demo-0.1 #2" 加换行；
    随后新进程 show 的标题与第一、第三条原文不变，仅第二条成为
    修订提示；再次提交相同文本仍成功且内容不变。
  - 删除后的序号定位：先 remove-change 删除一条，存储位置（position）
    留下间隔；set-change 按删除后的展示序号定位剩余条目，间隔不改变
    序号含义，替换只落在目标记录上。
  - 错误边界：未提供或重复提供 --index / --change、版本名或新文本为空
    或仅含空白、序号不是 ASCII 数字组成的正整数时，即使数据库不存在也
    先报 Invalid draft；输入有效但数据库文件不存在、没有 drafts 表或
    查无目标版本时报 Version not found；版本存在但序号越界时报
    Change not found（序号去前导零）。失败均退出 1、标准输出为空、
    标准错误仅含对应消息和一个换行，不创建数据库、不补建表、不改动
    任何原有内容。
  - 重构前建立的数据库：按重构前版本的建表语句与插入方式直接用
    sqlite3 建库（不经过当前 release_notes.py），重构后的 set-change
    与 show 仍能正常读取并替换其中的条目。

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

# 固定样例：版本 demo-0.1，标题"预览说明"，三条变更依次为两条重复文本
# 与一条 "第一行" 和 "第二行" 之间含真实换行的多行文本。
VERSION = "demo-0.1"
TITLE = "预览说明"
CHANGES = ["新增预览", "新增预览", "第一行\n第二行"]

NEW_CHANGE = "修订提示"

# 替换第二条后新进程 show 的期望输出（按 README 固定格式独立写明）：
# 标题与第一、第三条原文不变，仅第二条成为修订提示。
EXPECTED_SHOW_AFTER_REPLACE = (
    "Version: demo-0.1\n"
    "Title: 预览说明\n"
    "- 新增预览\n"
    "- 修订提示\n"
    "- 第一行\n"
    "第二行\n"
)

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

# 非法序号：0、带负号、小数、字母、全角数字、含空白都不是由 ASCII 数字
# 组成的正整数。
INVALID_INDEXES = ["0", "00", "-1", "1.5", "1a", "１２", " 1", "1 "]


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


def set_change(db_path, version, index, change):
    return run_cli(
        db_path, "set-change", version, "--index", str(index),
        "--change", change,
    )


class SetChangeSharedFlowTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-set-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual((code, out, err), (0, "Created demo-0.1\n", ""))

    def build_pre_refactor_database(self):
        """按重构前版本的建表与插入方式直接建库，不经过当前命令行。

        表结构与重构前的 SCHEMA 逐字相同；变更按 create 的既有约定以
        0 起始的连续 position 写入，模拟重构前版本建立的演示数据库。
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

    def read_change_rows(self):
        """直接读取库内 demo-0.1 的 (position, content) 行，按展示顺序排列。"""
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT position, content FROM changes"
                " WHERE version = ? ORDER BY position",
                (VERSION,),
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


class TestSetChangeNormalReplace(SetChangeSharedFlowTestCase):
    """正常替换：固定样例、固定命令、固定输出与新进程 show 核对。"""

    def test_replace_second_entry_with_leading_zero_index(self):
        self.create_demo_01()

        # 即 python release_notes.py --db notes.sqlite set-change demo-0.1 \
        #   --index 02 --change "修订提示"
        # 前导零不影响定位，输出序号不保留前导零。
        code, out, err = set_change(self.db, VERSION, "02", NEW_CHANGE)
        self.assertEqual(code, 0, "set-change 退出码应为 0")
        self.assertEqual(err, "", "set-change 标准错误应为空")
        self.assertEqual(
            out,
            "Updated change: demo-0.1 #2\n",
            "set-change 标准输出应恰为 Updated change: demo-0.1 #2 加换行",
        )

        # 新进程 show：标题与第一、第三条原文不变，仅第二条成为修订提示。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_AFTER_REPLACE, "替换后 show 不符")

        # 库内核对：条目数量与位置不变，仅 position 1 的内容被替换，
        # 重复文本的另一条与多行条目原样保留。
        self.assertEqual(
            self.read_change_rows(),
            [(0, "新增预览"), (1, NEW_CHANGE), (2, "第一行\n第二行")],
            "只改指定条目原文，数量、顺序、重复条目与多行文本不变",
        )

        # 提交相同文本仍成功，保存内容不再改变。
        code, out, err = set_change(self.db, VERSION, "2", NEW_CHANGE)
        self.assertEqual(
            (code, out, err),
            (0, "Updated change: demo-0.1 #2\n", ""),
            "相同文本再次提交仍应成功",
        )
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out, EXPECTED_SHOW_AFTER_REPLACE, "相同文本再次提交后内容不变"
        )

        # 操作结束后连接已释放：目录内只有数据库文件，不残留 journal、wal。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            [os.path.basename(self.db)],
            "替换完成后目录内应只有数据库文件",
        )


class TestSetChangeAfterRemoval(SetChangeSharedFlowTestCase):
    """删除留下存储位置间隔后，序号仍按当前展示顺序定位。"""

    def test_index_counts_remaining_entries_after_removal(self):
        self.create_demo_01()

        # 删除第一条（position 0）：剩余记录的 position 留下间隔（1、2），
        # 展示序号按剩余记录从 1 重新计算。
        code, out, err = run_cli(self.db, "remove-change", VERSION, "--index", "1")
        self.assertEqual((code, out, err), (0, "Removed change: demo-0.1 #1\n", ""))

        # 删除后的序号 2 指向原第三条（多行文本），而非 position 2 之外的位置。
        code, out, err = set_change(self.db, VERSION, "02", NEW_CHANGE)
        self.assertEqual(
            (code, out, err), (0, "Updated change: demo-0.1 #2\n", "")
        )

        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: demo-0.1\n"
            "Title: 预览说明\n"
            "- 新增预览\n"
            "- 修订提示\n",
            "删除后序号按剩余条目的展示顺序定位",
        )

        # 存储位置间隔原样保留：剩余记录仍在 position 1 与 2，间隔不改变
        # 序号含义，替换只落在目标记录上。
        self.assertEqual(
            self.read_change_rows(),
            [(1, "新增预览"), (2, NEW_CHANGE)],
            "删除留下的位置间隔不改变序号含义",
        )

        # 删除两条后只剩一条时，序号 2 越界：报 Change not found 而非替换。
        code, out, err = run_cli(self.db, "remove-change", VERSION, "--index", "1")
        self.assertEqual((code, out, err), (0, "Removed change: demo-0.1 #1\n", ""))
        code, out, err = set_change(self.db, VERSION, "2", "不应写入")
        self.assertEqual(code, 1, "只剩一条时序号 2 越界，退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #2\n",
            "标准错误应恰为 Change not found 加一个换行",
        )
        self.assertEqual(
            self.read_change_rows(),
            [(2, NEW_CHANGE)],
            "越界替换不得改动仅剩的条目",
        )


class TestSetChangeErrorBoundaries(SetChangeSharedFlowTestCase):
    """错误优先级与失败不写入：校验先于查库，失败不建库、不补表、不改数据。"""

    def test_invalid_input_before_any_database_check(self):
        # 数据库路径不存在时，各类无效输入仍优先报 Invalid draft。
        db_path = os.path.join(self._tmpdir.name, "missing.sqlite")

        # 未提供 --index。
        self.assert_invalid_draft(
            run_cli(db_path, "set-change", VERSION, "--change", NEW_CHANGE)
        )
        # 重复提供 --index。
        self.assert_invalid_draft(
            run_cli(
                db_path, "set-change", VERSION,
                "--index", "1", "--index", "2", "--change", NEW_CHANGE,
            )
        )
        # 未提供 --change。
        self.assert_invalid_draft(
            run_cli(db_path, "set-change", VERSION, "--index", "1")
        )
        # 重复提供 --change。
        self.assert_invalid_draft(
            run_cli(
                db_path, "set-change", VERSION,
                "--index", "1", "--change", "a", "--change", "b",
            )
        )
        # 版本名为空或仅含空白。
        for name in ["", "   ", " \n\t "]:
            self.assert_invalid_draft(set_change(db_path, name, "1", NEW_CHANGE))
        # 新文本为空或仅含空白。
        for text in ["", "   ", " \n\t "]:
            self.assert_invalid_draft(set_change(db_path, VERSION, "1", text))
        # 序号不是由 ASCII 数字组成的正整数。
        for index in INVALID_INDEXES:
            self.assert_invalid_draft(set_change(db_path, VERSION, index, NEW_CHANGE))

        self.assertFalse(
            os.path.exists(db_path), "无效输入不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "无效输入后目录内应无任何文件"
        )

    def test_version_not_found_boundaries(self):
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")

        # 数据库文件不存在：报 Version not found，不创建文件。
        self.assert_version_not_found(
            set_change(missing_db, VERSION, "01", NEW_CHANGE), VERSION
        )
        self.assertFalse(
            os.path.exists(missing_db), "版本不存在时不得创建数据库文件"
        )

        # 文件存在但没有 drafts 表（只有无关表）：同样按版本不存在处理，
        # 不补建表、不改动原有数据。
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)"
            )
            conn.execute(
                "INSERT INTO unrelated(id, note) VALUES (1, '示例数据')"
            )
            conn.commit()
        finally:
            conn.close()
        with open(self.db, "rb") as handle:
            bytes_before = handle.read()

        self.assert_version_not_found(
            set_change(self.db, VERSION, "01", NEW_CHANGE), VERSION
        )
        with open(self.db, "rb") as handle:
            self.assertEqual(
                handle.read(), bytes_before, "缺表时失败替换不得改动数据库"
            )

        # drafts 表存在但查无目标版本（含仅大小写不同）：报 Version not
        # found，消息中的版本名保留传入原文，已有数据不变。
        self.create_demo_01()
        with open(self.db, "rb") as handle:
            bytes_before = handle.read()
        self.assert_version_not_found(
            set_change(self.db, "demo-0.9", "1", NEW_CHANGE), "demo-0.9"
        )
        self.assert_version_not_found(
            set_change(self.db, VERSION.upper(), "1", NEW_CHANGE),
            VERSION.upper(),
        )
        with open(self.db, "rb") as handle:
            self.assertEqual(
                handle.read(), bytes_before, "查无版本时不得改动数据库"
            )

    def test_change_not_found_when_index_out_of_range(self):
        self.create_demo_01()
        with open(self.db, "rb") as handle:
            bytes_before = handle.read()

        # 序号 04 超出三条变更的范围：消息中的序号去掉前导零。
        code, out, err = set_change(self.db, VERSION, "04", "不应写入")
        self.assertEqual(code, 1, "序号越界时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Change not found: demo-0.1 #4\n",
            "标准错误应恰为 Change not found 加一个换行，序号去掉前导零",
        )
        with open(self.db, "rb") as handle:
            self.assertEqual(
                handle.read(), bytes_before, "序号越界的失败替换不得改动数据库"
            )


class TestSetChangeOnPreRefactorDatabase(SetChangeSharedFlowTestCase):
    """重构前建立的数据库：重构后的 set-change 与 show 正常读取与替换。"""

    def test_replace_in_database_built_before_refactor(self):
        self.build_pre_refactor_database()

        # 重构后的 set-change 在重构前建立的库上执行固定命令。
        code, out, err = set_change(self.db, VERSION, "02", NEW_CHANGE)
        self.assertEqual(
            (code, out, err), (0, "Updated change: demo-0.1 #2\n", "")
        )

        # 新进程 show：标题与第一、第三条原文不变，仅第二条成为修订提示。
        code, out, err = run_cli(self.db, "show", VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_AFTER_REPLACE, "替换后 show 不符")

        # 库内核对：仅 position 1 的内容被替换，其余记录原样保留。
        self.assertEqual(
            self.read_change_rows(),
            [(0, "新增预览"), (1, NEW_CHANGE), (2, "第一行\n第二行")],
            "重构前建立的库中只改指定条目",
        )


if __name__ == "__main__":
    unittest.main()
