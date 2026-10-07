"""release_notes.py list-drafts --prefix 前缀筛选的离线回归测试。

只验证前缀筛选这一行为，全部通过 release_notes.py 的公开命令行在独立
子进程中验收，期望均来自固定输入，不访问用户数据库，不依赖内部表结构。

覆盖范围：
  - 主样例：按 v2、v10、V1、v1 的顺序创建四份草稿，标题各不相同，
    且 v2 的标题与变更都含 v1；--prefix v1 只返回 v1、v10，顺序固定，
    标题与变更不参与匹配，大写 V1 不返回；
  - 输出契约：退出码 0、标准错误为空，标准输出只有一个 UTF-8 JSON
    数组及恰一个末尾换行，每项只含 version 与 title，解析后与原文一致；
  - 逐字符匹配：中文、首尾空格与版本名中的内部换行按原文参与匹配；
    % 与 _ 按普通字符处理（例如前缀 v% 只匹配 v%1，不匹配 vA1）；
  - 省略 --prefix 仍按版本名原文 Unicode 码点顺序列出全部草稿；
  - 非法参数：缺值、空字符串、纯空白或重复提供时退出码为 1，
    标准输出为空，标准错误恰为 "Invalid draft" 加一个换行，
    即使数据库路径不存在也优先如此，且不创建任何文件；
  - 空结果：有效前缀无匹配、数据库不存在、合法 SQLite 库没有草稿表、
    草稿表存在但没有草稿时，退出码 0、标准错误为空、输出恰为 [] 加换行；
    查询后不创建数据库、不补建表、不新增其他文件；
  - 只读与稳定：查询前后已有草稿的标题、重复及多行变更、条目数量和
    展示顺序保持原样；相同数据由两个独立进程查询得到完全一致的结果。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
可与既有测试一起被 unittest 发现入口收集并重复执行。
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

    输出按字节捕获后以 UTF-8 严格解码：非法字节会直接抛错，中文、空格
    与换行精确可比，不依赖运行环境的区域设置。
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


def prefix_query(db_path, prefix):
    return list_drafts(db_path, "--prefix", prefix)


def show(db_path, version):
    return run_cli(db_path, "show", version)


class PrefixTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="release-notes-prefix-"
        )
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_draft(self, version, title, changes):
        """创建草稿并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, version, title, changes)
        self.assertEqual(code, 0, f"create {version!r} 退出码应为 0")
        self.assertEqual(
            out,
            f"Created {version}\n",
            f"create {version!r} 标准输出不符",
        )
        self.assertEqual(err, "", f"create {version!r} 标准错误应为空")

    def assert_json_array_contract(self, result, expected_items):
        """核对前缀查询的公共输出契约，逐字节比对固定期望。

        expected_items 为固定输入按公开规则（版本名 Unicode 码点升序）
        给出的 [{"version": ..., "title": ...}, ...] 期望。
        """
        code, out, err = result
        self.assertEqual(code, 0, "list-drafts --prefix 退出码应为 0")
        self.assertEqual(err, "", "标准错误应为空")

        # 标准输出恰为一个单行 JSON 数组加末尾恰一个换行：期望串由固定
        # 输入与公开 JSON 格式直接拼出，含内部换行的版本名会被转义为
        # \\n，因此数组正文中不应出现真实换行。
        expected_out = (
            json.dumps(expected_items, ensure_ascii=False) + "\n"
        )
        self.assertEqual(
            out,
            expected_out,
            "标准输出应恰为一个 JSON 数组加一个末尾换行",
        )
        body = out[:-1]
        self.assertNotIn("\n", body, "JSON 数组应只占一行，无额外换行")

        # 再独立解析一遍：每个元素只含 version 和 title 两个字符串字段，
        # 解析后的文本与固定输入原文逐字符一致。
        items = json.loads(body)
        self.assertIsInstance(items, list, "标准输出应解析为一个 JSON 数组")
        for item in items:
            self.assertIsInstance(item, dict, "数组元素应为 JSON 对象")
            self.assertEqual(
                sorted(item.keys()),
                ["title", "version"],
                "每个元素只应含 version 和 title 两个字段",
            )
            self.assertIsInstance(item["version"], str)
            self.assertIsInstance(item["title"], str)
        self.assertEqual(items, expected_items, "解析结果应与原文一致")
        return items


class TestMainSamplePrefix(PrefixTestCase):
    """主样例：四份草稿的前缀筛选、大小写与标题/变更无关性。"""

    # 固定输入：严格按 v2、v10、V1、v1 的顺序创建，标题各不相同；
    # v2 的标题与变更文本中都含 v1，但前缀筛选只看版本名。
    DRAFTS = [
        ("v2", "含 v1 的第二版标题", ["变更也含 v1 的文本"]),
        ("v10", "第十版标题", ["第十版变更"]),
        ("V1", "大写首版标题", ["大写变更 V1"]),
        ("v1", "首版标题", ["首版变更"]),
    ]

    def setUp(self):
        super().setUp()
        for version, title, changes in self.DRAFTS:
            self.create_draft(version, title, changes)

    def test_prefix_v1_returns_only_v1_and_v10_in_fixed_order(self):
        result = prefix_query(self.db, "v1")

        # 固定期望：只有 v1、v10（"v1" 为 "v10" 的前缀且更短，故在前）；
        # v2 的标题与变更虽含 v1 仍不返回，大写 V1 也不返回。
        items = self.assert_json_array_contract(
            result,
            [
                {"version": "v1", "title": "首版标题"},
                {"version": "v10", "title": "第十版标题"},
            ],
        )
        self.assertEqual(
            [item["version"] for item in items],
            ["v1", "v10"],
            "前缀 v1 的返回顺序应固定为 v1、v10",
        )
        versions = {item["version"] for item in items}
        self.assertNotIn("v2", versions, "标题/变更含 v1 的 v2 不应返回")
        self.assertNotIn("V1", versions, "大小写不同的 V1 不应返回")

    def test_prefix_matches_versions_only_not_titles_or_changes(self):
        # 以标题中出现的文本作前缀：版本名均不以此开头，结果应为空数组，
        # 直接证明标题与变更内容不参与匹配。
        self.assert_json_array_contract(
            prefix_query(self.db, "含 v1"), []
        )
        self.assert_json_array_contract(
            prefix_query(self.db, "第十版"), []
        )

    def test_prefix_is_case_sensitive(self):
        # 大写前缀只匹配大写版本名，不匹配 v1。
        self.assert_json_array_contract(
            prefix_query(self.db, "V1"),
            [{"version": "V1", "title": "大写首版标题"}],
        )

    def test_omitting_prefix_lists_all_in_codepoint_order(self):
        # 省略选项时保留全部目录行为：版本名原文逐字符（Unicode 码点）
        # 升序为 V1(U+0056)、v1、v10（v1 为前缀且更短）、v2。
        self.assert_json_array_contract(
            list_drafts(self.db),
            [
                {"version": "V1", "title": "大写首版标题"},
                {"version": "v1", "title": "首版标题"},
                {"version": "v10", "title": "第十版标题"},
                {"version": "v2", "title": "含 v1 的第二版标题"},
            ],
        )


class TestLiteralSpecialCharacters(PrefixTestCase):
    """% 与 _ 作为普通字符匹配，不作为 SQL LIKE 通配符。"""

    DRAFTS = [
        ("v%1", "百分号版本", ["百分号变更"]),
        ("vA1", "字母 A 版本", ["字母变更"]),
        ("v_1", "下划线版本", ["下划线变更"]),
    ]

    def setUp(self):
        super().setUp()
        for version, title, changes in self.DRAFTS:
            self.create_draft(version, title, changes)

    def test_percent_is_literal(self):
        # 前缀 v% 只匹配版本名原文以此开头的 v%1：若按 LIKE 通配，
        # vA1、v_1 也会被匹配，这里它们都不应出现。
        self.assert_json_array_contract(
            prefix_query(self.db, "v%"),
            [{"version": "v%1", "title": "百分号版本"}],
        )

    def test_underscore_is_literal(self):
        # 前缀 v_ 只匹配 v_1：下划线不代表任意单字符。
        self.assert_json_array_contract(
            prefix_query(self.db, "v_"),
            [{"version": "v_1", "title": "下划线版本"}],
        )

    def test_percent_in_prefix_matches_verbatim_to_end(self):
        self.assert_json_array_contract(
            prefix_query(self.db, "v%1"),
            [{"version": "v%1", "title": "百分号版本"}],
        )
        self.assert_json_array_contract(prefix_query(self.db, "v%2"), [])


class TestNonAsciiAndWhitespaceMatching(PrefixTestCase):
    """中文、首尾空格与版本名内部换行按原文逐字符参与匹配。"""

    def test_chinese_prefix_matches_verbatim(self):
        # 两个版本名前缀相同，仅第四个字符分别为空格(U+0020)与数字 1，
        # 中文前缀同时命中二者，码点顺序中空格排在数字之前。
        self.create_draft("中文1", "中文标题一", ["变更一"])
        self.create_draft("中文 1", "带空格的中文标题", ["变更二"])

        self.assert_json_array_contract(
            prefix_query(self.db, "中文"),
            [
                {"version": "中文 1", "title": "带空格的中文标题"},
                {"version": "中文1", "title": "中文标题一"},
            ],
        )
        # 中文前缀同样区分原文："中" 单独命中两条，"中文 1" 只命中一条。
        self.assert_json_array_contract(
            prefix_query(self.db, "中文 1"),
            [{"version": "中文 1", "title": "带空格的中文标题"}],
        )

    def test_leading_and_trailing_spaces_participate_in_match(self):
        self.create_draft(" v1", "前导空格版本", ["变更一"])
        self.create_draft("v1", "普通首版", ["变更二"])
        self.create_draft("v1 ", "尾随空格版本", ["变更三"])

        # 前导空格是版本名的真实首字符：不带空格的前缀不匹配它。
        self.assert_json_array_contract(
            prefix_query(self.db, " v"),
            [{"version": " v1", "title": "前导空格版本"}],
        )
        # 尾随空格同样逐字符参与：前缀末尾的空格不能被裁剪。
        self.assert_json_array_contract(
            prefix_query(self.db, "v1 "),
            [{"version": "v1 ", "title": "尾随空格版本"}],
        )
        # 前缀 v1 命中 v1 与 v1（尾随空格），较短名称在前；
        # " v1" 因首字符是空格而不参与。
        self.assert_json_array_contract(
            prefix_query(self.db, "v1"),
            [
                {"version": "v1", "title": "普通首版"},
                {"version": "v1 ", "title": "尾随空格版本"},
            ],
        )

    def test_internal_newline_participates_in_match(self):
        self.create_draft("a\nb", "换行 B 标题", ["变更一"])
        self.create_draft("a\nc", "换行 C 标题", ["变更二"])
        self.create_draft("ab", "无换行标题", ["变更三"])

        # 前缀中的换行是普通字符：只命中版本名含真实换行的两条，
        # 不命中 "ab"；两条结果按换行之后的码点 b<c 排序。
        self.assert_json_array_contract(
            prefix_query(self.db, "a\n"),
            [
                {"version": "a\nb", "title": "换行 B 标题"},
                {"version": "a\nc", "title": "换行 C 标题"},
            ],
        )
        # 含换行的完整前缀逐字符匹配，JSON 正文中换行被转义为 \n。
        code, out, err = prefix_query(self.db, "a\nb")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            '[{"version": "a\\nb", "title": "换行 B 标题"}]\n',
        )


class TestInvalidPrefixArguments(PrefixTestCase):
    """缺值、空串、纯空白或重复提供一律报 Invalid draft，先于数据库访问。"""

    INVALID_CASES = [
        ("缺值（--prefix 后无参数）", ["--prefix"]),
        ("空字符串", ["--prefix", ""]),
        ("纯空格", ["--prefix", "   "]),
        ("制表符与换行组成的空白", ["--prefix", " \t\n "]),
        ("重复提供且值相同", ["--prefix", "v1", "--prefix", "v1"]),
        ("重复提供且值不同", ["--prefix", "v1", "--prefix", "v2"]),
    ]

    def assert_invalid(self, db_path, extra_args, populated):
        code, out, err = list_drafts(db_path, *extra_args)
        self.assertEqual(code, 1, "非法前缀退出码应为 1")
        self.assertEqual(out, "", "非法前缀时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加换行"
        )
        if populated:
            # 非法调用不得改动已有数据：完整目录仍可查询且内容不变。
            code, out, err = list_drafts(db_path)
            self.assertEqual(
                (code, err),
                (0, ""),
                "非法前缀调用后数据库应保持可查询且无错误输出",
            )
            self.assertEqual(
                json.loads(out),
                [
                    {"version": "V1", "title": "大写首版标题"},
                    {"version": "v1", "title": "首版标题"},
                    {"version": "v10", "title": "第十版标题"},
                    {"version": "v2", "title": "含 v1 的第二版标题"},
                ],
                "非法前缀调用后已有草稿应保持原样",
            )

    def test_invalid_arguments_against_populated_database(self):
        self.create_draft("v2", "含 v1 的第二版标题", ["v2 变更"])
        self.create_draft("v10", "第十版标题", ["v10 变更"])
        self.create_draft("V1", "大写首版标题", ["V1 变更"])
        self.create_draft("v1", "首版标题", ["v1 变更"])

        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                self.assert_invalid(self.db, extra_args, populated=True)

    def test_invalid_arguments_take_precedence_over_missing_database(self):
        # 数据库路径不存在时，参数校验仍然优先：不访问数据库、不建库。
        missing_db = os.path.join(self._tmpdir.name, "absent.sqlite")
        self.assertFalse(os.path.exists(missing_db))

        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                self.assertEqual(os.listdir(self._tmpdir.name), [])
                self.assert_invalid(missing_db, extra_args, populated=False)
                # 失败后既不创建数据库文件，也不留下任何其他文件。
                self.assertFalse(
                    os.path.exists(missing_db),
                    f"{label} 后不得创建数据库文件",
                )
                self.assertEqual(
                    os.listdir(self._tmpdir.name),
                    [],
                    f"{label} 后不得新增任何文件",
                )


class TestPrefixEmptyResults(PrefixTestCase):
    """有效前缀或空目录场景：退出码 0，输出恰为 [] 加换行。"""

    def assert_empty_result(self, db_path, prefix="v1"):
        code, out, err = list_drafts(db_path, "--prefix", prefix)
        self.assertEqual(code, 0, "退出码应为 0")
        self.assertEqual(err, "", "标准错误应为空")
        self.assertEqual(
            out, "[]\n", "空结果标准输出应恰为 [] 加一个末尾换行"
        )

    def test_valid_prefix_with_no_match(self):
        self.create_draft("v2", "第二版标题", ["v2 变更"])
        self.create_draft("V1", "大写首版标题", ["V1 变更"])
        self.assert_empty_result(self.db, "不存在的前缀")
        self.assert_empty_result(self.db, "v1")

    def test_missing_database_is_not_created(self):
        missing_db = os.path.join(self._tmpdir.name, "absent.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        self.assert_empty_result(missing_db)

        self.assertFalse(os.path.exists(missing_db), "查询不得创建数据库文件")
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "查询不得新增其他文件"
        )

    def test_sqlite_database_without_drafts_table_is_not_altered(self):
        # 一个不含 drafts 表的合法 SQLite 数据库（含其他表与文件头）。
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        files_before = sorted(os.listdir(self._tmpdir.name))

        self.assert_empty_result(self.db)

        # 不补建 drafts 表，也不改动其他表，不新增文件。
        conn = sqlite3.connect(self.db)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
        finally:
            conn.close()
        self.assertEqual(tables, ["unrelated"], "查询不得补建 drafts 表")
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "查询不得新增其他文件",
        )

    def test_drafts_table_without_rows(self):
        # drafts/changes 表存在但没有任何草稿。
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(
                "CREATE TABLE drafts (version TEXT PRIMARY KEY,"
                " title TEXT NOT NULL);"
                "CREATE TABLE changes (id INTEGER PRIMARY KEY);"
            )
            conn.commit()
        finally:
            conn.close()

        self.assert_empty_result(self.db)

        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0, "查询不得向空草稿表写入任何行")


class TestPrefixReadOnlyAndStable(PrefixTestCase):
    """前缀查询只读不改：草稿原样保留，两个独立进程结果完全一致。"""

    VERSION = "demo-1"
    TITLE = " 含重复与多行的草稿 "
    CHANGES = ["重复文本", "重复文本", "第一行\n第二行"]
    EXPECTED_SHOW = (
        "Version: demo-1\n"
        "Title:  含重复与多行的草稿 \n"
        "- 重复文本\n"
        "- 重复文本\n"
        "- 第一行\n"
        "第二行\n"
    )

    def test_drafts_unchanged_and_two_processes_identical(self):
        self.create_draft(self.VERSION, self.TITLE, self.CHANGES)
        self.create_draft("demo-2", "另一草稿", ["另一条变更"])
        self.create_draft("other", "不匹配版本", ["其他变更"])

        files_before = sorted(os.listdir(self._tmpdir.name))
        before_show = show(self.db, self.VERSION)
        self.assertEqual(
            before_show, (0, self.EXPECTED_SHOW, ""), "查询前 show 基准不符"
        )

        # 两次独立进程的前缀查询：完整三元组（退出码、输出、错误）逐字节一致。
        first = prefix_query(self.db, "demo")
        second = prefix_query(self.db, "demo")
        self.assertEqual(
            first,
            second,
            "两个独立进程的前缀查询结果应完全一致",
        )
        self.assert_json_array_contract(
            first,
            [
                {"version": "demo-1", "title": " 含重复与多行的草稿 "},
                {"version": "demo-2", "title": "另一草稿"},
            ],
        )

        # 再用无匹配前缀查询一次，覆盖空结果路径下的只读性。
        self.assert_json_array_contract(
            prefix_query(self.db, "无前缀命中"), []
        )

        # 查询后：标题、重复条目与多行变更的原文、数量、顺序保持原样；
        # 完整目录的条目数量与展示顺序不变；目录中不新增任何文件。
        after_show = show(self.db, self.VERSION)
        self.assertEqual(
            after_show,
            before_show,
            "前缀查询后草稿的标题、重复及多行变更应保持原样",
        )
        self.assert_json_array_contract(
            list_drafts(self.db),
            [
                {"version": "demo-1", "title": " 含重复与多行的草稿 "},
                {"version": "demo-2", "title": "另一草稿"},
                {"version": "other", "title": "不匹配版本"},
            ],
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "前缀查询不得新增其他文件",
        )


if __name__ == "__main__":
    unittest.main()
