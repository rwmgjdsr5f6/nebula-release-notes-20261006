"""release_notes.py copy-version 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（v1，标题"预览"，三条变更依次为
    甲、重复、重复）上执行 copy-version v1 --to v2；退出码 0、标准
    错误为空、标准输出恰为 "Copied version: v1 -> v2" 加换行；随后另一次
    命令行调用 show v2 显示新版本名、原标题与同序三条原文（重复条目
    分别保留，多行文本仍是一条记录），原版本 v1 仍在且内容不变。
    新名称含中文与首尾空格时按原文保存、不裁剪，名称仍区分大小写；
    副本条目按当前展示顺序复制（删除、移动产生的 position 空洞不
    复制）；多行变更中的内部换行与首尾空格原样保留。
  - 独立性：复制后对任一份草稿执行 set-title / set-change 只改变该
    草稿，另一份的标题、条目与顺序不受影响。
  - 目录与导出：新草稿可由 list-drafts（含 --prefix 筛选与 Unicode
    码点排序）、export-markdown 与 export-changelog 读取，沿用原有
    排序、前缀筛选和输出格式。
  - 失败路径：新名称已存在（含原名与新名相同）报 Version already
    exists，两份草稿都保持原样；数据库文件不存在、库中没有 drafts 表
    或查无原版本（含仅大小写不同；原版本不存在而新名已存在时仍报告
    原版本不存在）报 Version not found；原版本名或新版本名为空 / 仅
    空白，或 --to 未提供、缺值、为空、仅空白、重复提供，均报
    Invalid draft，且优先于数据库或版本存在性检查。所有失败退出码 1、
    标准输出为空、错误末尾带换行，不存在的数据库不会被创建，不补建
    表，不留下新草稿或部分条目，已有草稿不发生修改。

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

# 验收固定样例：版本 v1，标题"预览"，三条变更依次为 甲、重复、重复。
VERSION = "v1"
TITLE = "预览"
CHANGES = ["甲", "重复", "重复"]

EXPECTED_SHOW_V1 = (
    "Version: v1\n"
    "Title: 预览\n"
    "- 甲\n"
    "- 重复\n"
    "- 重复\n"
)

# 复制为 v2 后的期望 show 输出（按 README 固定格式独立写明）：新版本名、
# 原标题、同序三条原文，重复条目分别保留。
EXPECTED_SHOW_V2 = (
    "Version: v2\n"
    "Title: 预览\n"
    "- 甲\n"
    "- 重复\n"
    "- 重复\n"
)

# 多行与首尾空格样例。
MULTILINE_CHANGE = "第一行\n  第二行  "

# 冲突样例：v9 是另一份独立草稿，标题与条目均与 v1 不同。
CONFLICT_VERSION = "v9"
CONFLICT_TITLE = "正式版"
CONFLICT_CHANGES = ["已有变更一", "已有变更二"]

EXPECTED_SHOW_V9 = (
    "Version: v9\n"
    "Title: 正式版\n"
    "- 已有变更一\n"
    "- 已有变更二\n"
)


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、标点与首尾空格精确可比。
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


def copy_version(db_path, version, *copy_args):
    return run_cli(db_path, "copy-version", version, *copy_args)


class CopyVersionTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_main_fixture(self):
        """按验收固定样例创建 v1（预览 / 甲、重复、重复）。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create v1 退出码应为 0")
        self.assertEqual(out, "Created v1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def create_conflict_fixtures(self):
        """创建标题与条目均不同的 v1 与 v9 两份草稿。"""
        self.create_main_fixture()
        code, out, err = create(
            self.db, CONFLICT_VERSION, CONFLICT_TITLE, CONFLICT_CHANGES
        )
        self.assertEqual(code, 0, "create v9 退出码应为 0")
        self.assertEqual(out, "Created v9\n", "create v9 标准输出不符")
        self.assertEqual(err, "", "create v9 标准错误应为空")

    def draft_row_counts(self):
        """返回 {版本名: 变更条数}，用于失败后核对未留部分副本。"""
        conn = sqlite3.connect(self.db)
        try:
            versions = [
                row[0]
                for row in conn.execute("SELECT version FROM drafts")
            ]
            return {
                version: conn.execute(
                    "SELECT COUNT(*) FROM changes WHERE version = ?",
                    (version,),
                ).fetchone()[0]
                for version in versions
            }
        finally:
            conn.close()


class TestCopyVersionSuccess(CopyVersionTestCase):
    def test_copy_then_show_new_name_and_original_kept(self):
        self.create_main_fixture()

        # 即 python release_notes.py --db notes.sqlite copy-version v1 --to v2
        code, out, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual(code, 0, "copy-version 退出码应为 0")
        self.assertEqual(err, "", "copy-version 标准错误应为空")
        self.assertEqual(
            out,
            "Copied version: v1 -> v2\n",
            "copy-version 标准输出应恰为 Copied version: v1 -> v2"
            " 加末尾换行",
        )

        # 另一次命令行调用查看新名称：新版本名、原标题、同序三条原文，
        # 重复条目分别保留。
        code, out, err = show(self.db, "v2")
        self.assertEqual(code, 0, "show v2 退出码应为 0")
        self.assertEqual(err, "", "show v2 标准错误应为空")
        self.assertEqual(out, EXPECTED_SHOW_V2, "副本的标题与三条变更应按原顺序复制")

        # 原版本保留不动，内容一致。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show v1 退出码应为 0")
        self.assertEqual(err, "", "show v1 标准错误应为空")
        self.assertEqual(out, EXPECTED_SHOW_V1, "原草稿复制后应保持不变")

    def test_copy_to_name_with_cjk_and_surrounding_spaces(self):
        self.create_main_fixture()

        # 新名称含中文与首尾空格：按原文保存，不裁剪。
        new_name = " V2 副本 "
        code, out, err = copy_version(self.db, VERSION, "--to", new_name)
        self.assertEqual(code, 0, "copy-version 退出码应为 0")
        self.assertEqual(err, "", "copy-version 标准错误应为空")
        self.assertEqual(
            out,
            "Copied version: v1 ->  V2 副本 \n",
            "成功消息中的新名称应保留首尾空格，不裁剪",
        )

        # 按原文精确查看成功：首尾空格是名称的一部分。
        code, out, err = show(self.db, new_name)
        self.assertEqual(code, 0, "show 新名称退出码应为 0")
        self.assertEqual(err, "", "show 新名称标准错误应为空")
        self.assertEqual(
            out,
            "Version:  V2 副本 \n"
            "Title: 预览\n"
            "- 甲\n"
            "- 重复\n"
            "- 重复\n",
            "含中文与首尾空格的新名称应按原文保存，内容随名称复制",
        )

        # 裁剪掉首尾空格的名称不是同一版本；仅大小写不同也不命中。
        code, out, err = show(self.db, "V2 副本")
        self.assertEqual(code, 1, "裁剪后的名称不应命中")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: V2 副本\n")

    def test_copy_preserves_multiline_change_as_single_record(self):
        code, out, err = create(
            self.db, "m1", "多行标题", ["甲", MULTILINE_CHANGE, "重复", "重复"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

        code, out, err = copy_version(self.db, "m1", "--to", "m2")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, "Copied version: m1 -> m2\n")

        # 多行文本仍是一条记录：只在首行前加 "- "，内部换行与行尾空格保留。
        code, out, err = show(self.db, "m2")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: m2\n"
            "Title: 多行标题\n"
            "- 甲\n"
            "- 第一行\n"
            "  第二行  \n"
            "- 重复\n"
            "- 重复\n",
            "多行变更应作为一条记录原样复制",
        )

    def test_copy_uses_current_display_order_after_remove_and_move(self):
        # 原草稿经过删除与移动：复制以当前展示顺序与现存条目为准。
        code, out, err = create(
            self.db, "w1", "原标题", ["一", "二", "三", "二"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

        code, out, err = run_cli(self.db, "remove-change", "w1", "--index", "1")
        self.assertEqual((code, err), (0, ""))
        # 现存顺序原为 二、三、二；再把第 3 条移到第 1 条。
        code, out, err = run_cli(self.db, "move-change", "w1", "--from", "3", "--to", "1")
        self.assertEqual((code, err), (0, ""))

        code, out, err = show(self.db, "w1")
        self.assertEqual(code, 0)
        current_order = out  # 记录复制时的当前顺序

        code, out, err = copy_version(self.db, "w1", "--to", "w2")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, "Copied version: w1 -> w2\n")

        code, out, err = show(self.db, "w2")
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            current_order.replace("Version: w1", "Version: w2", 1),
            "副本应按复制时的当前展示顺序组织条目，与原草稿当前内容逐字一致"
            "（仅版本名不同）",
        )

        # 副本 position 从 0 连续编号，不复制原草稿删除/移动留下的空洞。
        conn = sqlite3.connect(self.db)
        try:
            positions = [
                row[0]
                for row in conn.execute(
                    "SELECT position FROM changes WHERE version = ?"
                    " ORDER BY position",
                    ("w2",),
                )
            ]
        finally:
            conn.close()
        self.assertEqual(
            positions,
            list(range(len(positions))),
            "副本条目的 position 应从 0 连续编号",
        )


class TestCopyVersionIndependence(CopyVersionTestCase):
    def test_editing_one_copy_leaves_other_untouched(self):
        self.create_main_fixture()

        code, out, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 修改副本的标题与一条变更：原草稿不受影响。
        code, out, err = run_cli(self.db, "set-title", "v2", "--title", "修订标题")
        self.assertEqual((code, err), (0, ""))
        code, out, err = run_cli(
            self.db, "set-change", "v2", "--index", "1", "--change", "改后甲"
        )
        self.assertEqual((code, err), (0, ""))

        code, out, err = show(self.db, "v2")
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "Version: v2\n"
            "Title: 修订标题\n"
            "- 改后甲\n"
            "- 重复\n"
            "- 重复\n",
            "副本应反映 set-title / set-change 的修改",
        )

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            EXPECTED_SHOW_V1,
            "修改副本不得影响原草稿的标题、条目与顺序",
        )

        # 反向：修改原草稿，副本同样不受影响。
        code, out, err = run_cli(self.db, "add-change", "v1", "--change", "新增尾")
        self.assertEqual((code, err), (0, ""))
        code, out, err = show(self.db, "v2")
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "Version: v2\n"
            "Title: 修订标题\n"
            "- 改后甲\n"
            "- 重复\n"
            "- 重复\n",
            "修改原草稿不得影响副本",
        )
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "Version: v1\n"
            "Title: 预览\n"
            "- 甲\n"
            "- 重复\n"
            "- 重复\n"
            "- 新增尾\n",
        )


class TestCopyVersionDirectoryAndExport(CopyVersionTestCase):
    def test_copy_visible_to_list_and_markdown_exports(self):
        self.create_main_fixture()
        code, out, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 目录查询：按版本名 Unicode 码点升序，两份草稿都在。
        code, out, err = run_cli(self.db, "list-drafts")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            '[{"version": "v1", "title": "预览"},'
            ' {"version": "v2", "title": "预览"}]\n',
        )

        # 前缀筛选沿用原规则。
        code, out, err = run_cli(self.db, "list-drafts", "--prefix", "v2")
        self.assertEqual(code, 0)
        self.assertEqual(
            out, '[{"version": "v2", "title": "预览"}]\n'
        )

        # 单份 Markdown 导出沿用固定格式。
        code, out, err = run_cli(self.db, "export-markdown", "v2")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "# v2\n\n预览\n\n- 甲\n- 重复\n- 重复\n",
        )

        # 汇总导出：两份片段按目录顺序排列，片段逐字等同单份导出。
        code, out, err = run_cli(self.db, "export-changelog")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "# v1\n\n预览\n\n- 甲\n- 重复\n- 重复\n"
            "\n"
            "# v2\n\n预览\n\n- 甲\n- 重复\n- 重复\n",
        )


class TestCopyVersionConflict(CopyVersionTestCase):
    def test_copy_to_existing_version_keeps_both_drafts(self):
        self.create_conflict_fixtures()

        code, out, err = copy_version(self.db, VERSION, "--to", "v9")
        self.assertEqual(code, 1, "目标名称已存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v9\n",
            "标准错误应恰为 Version already exists: v9 加末尾换行",
        )

        # 两份草稿都保持原样，且不留下部分副本。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持不变")
        code, out, err = show(self.db, CONFLICT_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V9, "v9 草稿应保持不变")
        self.assertEqual(
            self.draft_row_counts(), {"v1": 3, "v9": 2}, "失败不得新增草稿或部分条目"
        )

    def test_copy_to_same_name_is_conflict(self):
        self.create_main_fixture()

        # 原名与新名完全相同也属于新名已占用（区别于 rename-version）。
        code, out, err = copy_version(self.db, VERSION, "--to", "v1")
        self.assertEqual(code, 1, "同名复制退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v1\n",
            "同名复制应报 Version already exists: v1",
        )

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "同名复制失败后草稿应保持不变")
        self.assertEqual(
            self.draft_row_counts(), {"v1": 3}, "同名复制不得新增任何记录"
        )


class TestCopyVersionNotFound(CopyVersionTestCase):
    def test_missing_database_file(self):
        code, out, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual(code, 1, "数据库文件不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "标准错误应恰为 Version not found: v1 加末尾换行",
        )
        self.assertFalse(
            os.path.exists(self.db), "copy-version 不得创建数据库文件"
        )

    def test_database_without_drafts_table(self):
        # 文件存在但只有无关表：与路径不存在等价，不补建 drafts / changes。
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO unrelated(note) VALUES (?)", ("示例数据",))
            conn.commit()
        finally:
            conn.close()

        code, out, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "Version not found: v1\n")

        conn = sqlite3.connect(self.db)
        try:
            names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
        finally:
            conn.close()
        self.assertEqual(names, ["unrelated"], "失败不得补建任何表")

    def test_missing_version_is_case_sensitive(self):
        self.create_main_fixture()
        # 仅大小写不同的名称同样视为不存在。
        code, out, err = copy_version(self.db, "V1", "--to", "v2")
        self.assertEqual(code, 1, "原版本不存在时退出码应为 1")
        self.assertEqual(out, "")
        self.assertEqual(err, "Version not found: V1\n")
        self.assertEqual(
            self.draft_row_counts(), {"v1": 3}, "失败不得新增草稿或部分条目"
        )

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持不变")

    def test_missing_source_with_existing_target_reports_source(self):
        # 只有 v9：原版本 v1 不存在而目标 v9 已存在时，仍报告原版本不存在。
        code, out, err = create(
            self.db, CONFLICT_VERSION, CONFLICT_TITLE, CONFLICT_CHANGES
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

        code, out, err = copy_version(self.db, VERSION, "--to", "v9")
        self.assertEqual(code, 1, "原版本不存在时退出码应为 1")
        self.assertEqual(out, "")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "原版本不存在而目标已存在时仍应报告原版本不存在",
        )
        self.assertEqual(self.draft_row_counts(), {"v9": 2})

        code, out, err = show(self.db, CONFLICT_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V9, "v9 草稿应保持不变")


class TestCopyVersionInvalidInput(CopyVersionTestCase):
    """输入校验优先于数据库或版本存在性检查：无效输入一律 Invalid draft。"""

    def assert_invalid(self, *copy_args, version=VERSION):
        code, out, err = copy_version(self.db, version, *copy_args)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )

    def test_blank_version(self):
        self.assert_invalid("--to", "v2", version="")
        self.assert_invalid("--to", "v2", version="  \n\t ")

    def test_missing_to_option(self):
        self.assert_invalid()

    def test_to_option_missing_value(self):
        self.assert_invalid("--to")

    def test_to_option_blank_value(self):
        self.assert_invalid("--to", "")
        self.assert_invalid("--to", "  \n\t ")

    def test_duplicate_to_option(self):
        self.assert_invalid("--to", "v2", "--to", "v3")

    def test_invalid_input_precedes_database_and_version_checks(self):
        # 数据库文件不存在时无效输入仍优先报 Invalid draft（而非
        # Version not found），且不会因此创建数据库。
        self.assert_invalid("--to")

        # 数据库存在、原版本不存在时，无效输入同样优先报 Invalid draft，
        # 已有草稿不发生修改。
        self.create_main_fixture()
        code, out, err = copy_version(self.db, "v8", "--to")
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "")
        self.assertEqual(err, "Invalid draft\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持不变")
        self.assertEqual(self.draft_row_counts(), {"v1": 3})


if __name__ == "__main__":
    unittest.main()
