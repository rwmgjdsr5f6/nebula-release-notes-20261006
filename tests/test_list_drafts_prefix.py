"""release_notes.py list-drafts --prefix 前缀筛选的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 主样例：按 v2、v10、V1、v1 的顺序创建四份草稿，标题各不相同，
    且 v2 的标题与变更均含 "v1"；--prefix v1 只返回 v1、v10（顺序
    固定如此），标题与变更不参与匹配，大小写不同的 V1 不返回；
  - 输出契约：退出码 0、标准错误为空，标准输出仅有一个 UTF-8 JSON
    数组及末尾恰一个换行，每个元素只含 version 和 title 两个字符串
    字段，解析后与原文一致；
  - 逐字符匹配：中文、首尾空格与内部换行按原文参与前缀匹配；
    % 与 _ 是普通字符（不经 SQL LIKE 通配），如前缀 v% 只匹配 v%1
    而不匹配 vA1；省略 --prefix 仍按版本名 Unicode 码点升序列出全部；
  - 无效前缀：--prefix 缺值、空字符串、纯空白或重复提供时，退出码 1、
    标准输出为空、标准错误恰为 "Invalid draft" 加一个换行；即使数据库
    路径不存在也优先如此，且不创建数据库；
  - 空结果：有效前缀无匹配项、数据库不存在、合法 SQLite 库没有草稿表、
    草稿表存在但没有草稿时，退出码 0、标准错误为空、标准输出恰为
    "[]" 加一个换行；查询后不创建数据库、不补建表、不新增其他文件；
  - 只读与稳定：含重复文本与多行变更的草稿在查询前后用 show 核对完整
    文本不变；相同数据由两个独立进程查询结果完全一致。

所有期望均由固定输入与公开格式直接给出，不由被测查询结果生成。
运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，与既有测试一起重复执行结果一致。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文与空格精确可比。
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


def list_drafts(db_path, *extra_args):
    return run_cli(db_path, "list-drafts", *extra_args)


def show(db_path, version):
    return run_cli(db_path, "show", version)


class ListDraftsPrefixTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-prefix-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_draft(self, version, title, changes):
        """创建草稿并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, version, title, changes)
        self.assertEqual(code, 0, f"create {version!r} 退出码应为 0")
        self.assertEqual(out, f"Created {version}\n", f"create {version!r} 标准输出不符")
        self.assertEqual(err, "", f"create {version!r} 标准错误应为空")

    def assert_prefix_output_contract(self, result, expected_items):
        """核对 list-drafts --prefix 的公共输出契约并返回解析后的数组。

        expected_items 为固定输入按公开规则（版本名原文前缀匹配后按
        Unicode 码点升序）给出的 [{"version": ..., "title": ...}, ...] 期望。
        """
        code, out, err = result
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts 标准错误应为空")

        # 标准输出仅有一个 JSON 数组及末尾恰一个换行：数组本身单行、无内部换行。
        self.assertTrue(out.endswith("\n"), "标准输出应以一个换行结尾")
        body = out[:-1]
        self.assertNotIn("\n", body, "JSON 数组应只占一行，无额外换行")

        items = json.loads(body)
        self.assertIsInstance(items, list, "标准输出应解析为一个 JSON 数组")
        for item in items:
            self.assertIsInstance(item, dict, "数组元素应为 JSON 对象")
            self.assertEqual(
                sorted(item.keys()),
                ["title", "version"],
                "每个元素只应含 version 和 title 两个字段",
            )
            self.assertIsInstance(item["version"], str, "version 应为字符串")
            self.assertIsInstance(item["title"], str, "title 应为字符串")

        self.assertEqual(items, expected_items, "筛选结果应与固定输入的期望一致")
        return items

    def assert_empty_catalog(self, result):
        """核对空结果契约：退出码 0、标准错误为空、输出恰为 [] 加换行。"""
        code, out, err = result
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts 标准错误应为空")
        self.assertEqual(out, "[]\n", "空结果标准输出应恰为 [] 加末尾换行")

    def assert_invalid_draft(self, result):
        """核对无效前缀契约：退出码 1、标准输出为空、标准错误恰为一行提示。"""
        code, out, err = result
        self.assertEqual(code, 1, "无效前缀的退出码应为 1")
        self.assertEqual(out, "", "无效前缀时标准输出应为空")
        self.assertEqual(err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加一个换行")


class TestPrefixMainSample(ListDraftsPrefixTestCase):
    """主样例：按 v2、v10、V1、v1 的顺序创建，--prefix v1 只返回 v1、v10。"""

    # 固定输入：创建顺序即 v2、v10、V1、v1，标题各不相同；v2 的标题与
    # 变更均含 "v1"，用于验证标题与变更不参与匹配。
    DRAFTS = [
        ("v2", "含 v1 字样的第二版标题", ["变更提到 v1 的修复", "v2 自身变更"]),
        ("v10", "第十版标题", ["v10 变更"]),
        ("V1", "大写首版标题", ["V1 变更"]),
        ("v1", "首版标题", ["v1 变更"]),
    ]

    def test_prefix_v1_matches_only_v1_and_v10(self):
        for version, title, changes in self.DRAFTS:
            self.create_draft(version, title, changes)

        result = list_drafts(self.db, "--prefix", "v1")
        items = self.assert_prefix_output_contract(
            result,
            [
                {"version": "v1", "title": "首版标题"},
                {"version": "v10", "title": "第十版标题"},
            ],
        )

        # 顺序固定为 v1、v10（"v1" 是 "v10" 的前缀且更短，码点升序在前）；
        # v2 的标题与变更虽含 "v1" 但不参与匹配；V1 仅大小写不同，不返回。
        self.assertEqual(
            [item["version"] for item in items],
            ["v1", "v10"],
            "--prefix v1 应只按版本名原文匹配，顺序固定为 v1、v10",
        )

    def test_omitted_prefix_lists_all_in_codepoint_order(self):
        for version, title, changes in self.DRAFTS:
            self.create_draft(version, title, changes)

        # 省略 --prefix：保留既有目录行为，按版本名 Unicode 码点升序列出全部。
        result = list_drafts(self.db)
        items = self.assert_prefix_output_contract(
            result,
            [
                {"version": "V1", "title": "大写首版标题"},
                {"version": "v1", "title": "首版标题"},
                {"version": "v10", "title": "第十版标题"},
                {"version": "v2", "title": "含 v1 字样的第二版标题"},
            ],
        )
        self.assertEqual(
            [item["version"] for item in items],
            ["V1", "v1", "v10", "v2"],
            "省略 --prefix 应按 Unicode 码点升序列出全部草稿",
        )


class TestPrefixVerbatimMatching(ListDraftsPrefixTestCase):
    """中文、首尾空格、内部换行按原文逐字符匹配；% 与 _ 为普通字符。"""

    def test_percent_and_underscore_are_ordinary_characters(self):
        # 固定输入：若 % 或 _ 被当作 LIKE 通配符，v% 会误中 vA1、v_ 会误中 vB1。
        self.create_draft("v%1", "百分号标题", ["变更"])
        self.create_draft("vA1", "甲标题", ["变更"])
        self.create_draft("v_1", "下划线标题", ["变更"])
        self.create_draft("vB1", "乙标题", ["变更"])

        self.assert_prefix_output_contract(
            list_drafts(self.db, "--prefix", "v%"),
            [{"version": "v%1", "title": "百分号标题"}],
        )
        self.assert_prefix_output_contract(
            list_drafts(self.db, "--prefix", "v_"),
            [{"version": "v_1", "title": "下划线标题"}],
        )

    def test_chinese_prefix_matches_verbatim(self):
        self.create_draft("版本1", "第一稿", ["变更"])
        self.create_draft("版本2", "第二稿", ["变更"])
        self.create_draft("版其他", "另一稿", ["变更"])

        self.assert_prefix_output_contract(
            list_drafts(self.db, "--prefix", "版本"),
            [
                {"version": "版本1", "title": "第一稿"},
                {"version": "版本2", "title": "第二稿"},
            ],
        )

    def test_leading_and_trailing_spaces_match_verbatim(self):
        # 首尾空格是前缀的一部分：不裁剪、不忽略。
        self.create_draft(" v1", "前导空格标题", ["变更"])
        self.create_draft("v1 ", "末尾空格标题", ["变更"])
        self.create_draft("v1", "无空格标题", ["变更"])

        self.assert_prefix_output_contract(
            list_drafts(self.db, "--prefix", " v"),
            [{"version": " v1", "title": "前导空格标题"}],
        )
        self.assert_prefix_output_contract(
            list_drafts(self.db, "--prefix", "v1 "),
            [{"version": "v1 ", "title": "末尾空格标题"}],
        )

    def test_internal_newline_matches_verbatim(self):
        self.create_draft("多\n行1", "多行一", ["变更"])
        self.create_draft("多\n行2", "多行二", ["变更"])
        self.create_draft("多\n其他", "多行其他", ["变更"])

        self.assert_prefix_output_contract(
            list_drafts(self.db, "--prefix", "多\n行"),
            [
                {"version": "多\n行1", "title": "多行一"},
                {"version": "多\n行2", "title": "多行二"},
            ],
        )


class TestInvalidPrefix(ListDraftsPrefixTestCase):
    """缺值、空串、纯空白或重复提供的 --prefix 均为无效草稿。"""

    INVALID_ARGV = [
        ("缺值", ["--prefix"]),
        ("空字符串", ["--prefix", ""]),
        ("纯空白空格", ["--prefix", "  "]),
        ("纯空白换行制表", ["--prefix", "\t\n"]),
        ("重复提供不同值", ["--prefix", "v1", "--prefix", "v2"]),
        ("重复提供相同值", ["--prefix", "v1", "--prefix", "v1"]),
        ("重复提供且其一缺值", ["--prefix", "v1", "--prefix"]),
    ]

    def test_invalid_prefix_rejected_before_any_database_access(self):
        for label, argv in self.INVALID_ARGV:
            with self.subTest(label=label):
                # 数据库路径不存在时也优先报 Invalid draft，且不创建数据库。
                self.assertFalse(os.path.exists(self.db), "数据库初始不应存在")
                self.assert_invalid_draft(list_drafts(self.db, *argv))
                self.assertFalse(
                    os.path.exists(self.db), "无效前缀不得创建数据库文件"
                )
                self.assertEqual(
                    os.listdir(self._tmpdir.name), [], "不得在临时目录新增其他文件"
                )

    def test_invalid_prefix_rejected_even_with_existing_drafts(self):
        self.create_draft("v1", "首版标题", ["v1 变更"])

        for label, argv in self.INVALID_ARGV:
            with self.subTest(label=label):
                self.assert_invalid_draft(list_drafts(self.db, *argv))

        # 既有草稿不受无效调用影响。
        self.assertEqual(
            show(self.db, "v1"),
            (0, "Version: v1\nTitle: 首版标题\n- v1 变更\n", ""),
            "无效前缀调用不得改变已有草稿",
        )


class TestPrefixEmptyResults(ListDraftsPrefixTestCase):
    """无匹配项、缺库、缺表或空表均返回空数组，且保持只读。"""

    def test_valid_prefix_with_no_match(self):
        self.create_draft("v1", "首版标题", ["v1 变更"])
        self.create_draft("v2", "第二版标题", ["v2 变更"])

        self.assert_empty_catalog(list_drafts(self.db, "--prefix", "v3"))

    def test_missing_database_path_stays_missing(self):
        self.assertEqual(os.listdir(self._tmpdir.name), [], "临时目录初始应为空")

        self.assert_empty_catalog(list_drafts(self.db, "--prefix", "v1"))

        self.assertFalse(
            os.path.exists(self.db), "查询不存在的路径不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "查询不得在临时目录新增其他文件"
        )

    def test_sqlite_database_without_drafts_table(self):
        # 先建立一个不含 drafts 表的合法 SQLite 数据库（含文件头）。
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE _init (x INTEGER)")
        conn.execute("DROP TABLE _init")
        conn.commit()
        conn.close()
        self.assertTrue(os.path.exists(self.db))

        files_before = sorted(os.listdir(self._tmpdir.name))
        self.assert_empty_catalog(list_drafts(self.db, "--prefix", "v1"))

        # 不补建 drafts 表，也不新增任何其他表或文件。
        conn = sqlite3.connect(self.db)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(tables, [], "查询不得为无表数据库补建任何表")
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "查询不得在临时目录新增其他文件",
        )

    def test_drafts_table_present_but_empty(self):
        # 直接准备一个有空 drafts 表（含变更表）但没有任何草稿的数据库。
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE drafts (version TEXT PRIMARY KEY, title TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE changes ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " version TEXT NOT NULL,"
            " position INTEGER NOT NULL,"
            " content TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        tables_before = self._table_names()
        self.assert_empty_catalog(list_drafts(self.db, "--prefix", "v1"))
        self.assert_empty_catalog(list_drafts(self.db))

        self.assertEqual(self._table_names(), tables_before, "查询不得改变表结构")

    def _table_names(self):
        conn = sqlite3.connect(self.db)
        try:
            return [
                name
                for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    " ORDER BY name"
                )
            ]
        finally:
            conn.close()


class TestPrefixReadOnlyAndStable(ListDraftsPrefixTestCase):
    """前缀查询只读且跨进程稳定：草稿内容不变，两次查询结果一致。"""

    VERSION = "v1-演示"
    TITLE = " 含重复与多行的草稿 "
    CHANGES = ["重复文本", "重复文本", "第一行\n第二行"]
    EXPECTED_SHOW = (
        "Version: v1-演示\n"
        "Title:  含重复与多行的草稿 \n"
        "- 重复文本\n"
        "- 重复文本\n"
        "- 第一行\n"
        "第二行\n"
    )

    def test_drafts_unchanged_and_repeated_query_identical(self):
        self.create_draft(self.VERSION, self.TITLE, self.CHANGES)
        self.create_draft("v1-其他", "另一草稿", ["另一条变更"])
        self.create_draft("v2-无关", "无关草稿", ["无关变更"])

        # 查询前用 show 核对完整文本基准。
        before = show(self.db, self.VERSION)
        self.assertEqual(before, (0, self.EXPECTED_SHOW, ""), "查询前 show 基准不符")
        files_before = sorted(os.listdir(self._tmpdir.name))

        # 相同数据由两个独立进程查询：完整结果（含退出码与两个输出流）一致。
        first = list_drafts(self.db, "--prefix", "v1-")
        second = list_drafts(self.db, "--prefix", "v1-")
        self.assertEqual(first, second, "两次独立进程的前缀查询结果应完全一致")
        self.assert_prefix_output_contract(
            first,
            [
                {"version": "v1-其他", "title": "另一草稿"},
                {"version": "v1-演示", "title": " 含重复与多行的草稿 "},
            ],
        )

        # 查询后用 show 复核：标题、重复条目、多行条目的原文、数量与顺序不变。
        after = show(self.db, self.VERSION)
        self.assertEqual(after, before, "前缀查询不得改变草稿内容")
        self.assertEqual(
            after,
            (0, self.EXPECTED_SHOW, ""),
            "查询后标题、重复条目与多行条目的原文及顺序应保持不变",
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "查询不得在临时目录新增其他文件",
        )


if __name__ == "__main__":
    unittest.main()
