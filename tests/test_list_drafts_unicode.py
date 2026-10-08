# -*- coding: utf-8 -*-
"""release_notes.py list-drafts 针对版本名 Unicode 原文语义的离线回归测试。

本组测试固定使用四份“外观接近但码点不同”的草稿样例，确认目录查询始终
按版本名原文逐字符（Unicode 码点）比较与排序，不做 Unicode 规范化，
外观相同的名称不会被合并或互相匹配：

  - v-😀（U+0076 U+002D U+1F600），标题“表情标题”；
  - v-é（é 为单码点 U+00E9），标题“单字符标题”；
  - v-é（é 为 U+0065 U+0065 小写 e 加 U+0301 组合重音），
    标题“组合标题”；它与单字符 v-é 经 NFC 规范化后相等，但原文不同；
  - v-e（纯 ASCII 基础名称），标题“基础标题”。

固定创建顺序为 v-😀、v-é（单字符）、v-é（组合）、v-e；无筛选目录按
版本名原文码点升序固定为 v-e、v-é（组合，v-e 是其前缀且更短）、
v-é（单字符，第三码点 U+00E9 大于 U+0065）、v-😀（U+1F600 最大）。

覆盖范围（全部通过 release_notes.py 及 --db 公开命令行入口在独立子
进程中验收，期望均由固定样例直接写死，不从被测输出推导）：
  - 无筛选目录的固定顺序与标题对应关系，创建顺序故意与排序结果不同；
  - --prefix v-é（单字符 U+00E9）只返回单字符名称；
    --prefix v-é（组合序列 e + U+0301）只返回组合名称；
    --prefix v-e 按固定顺序返回基础名称与组合名称；
    --prefix v-É（É 为 U+00C9）没有匹配项，只输出 [] 和一个换行；
  - 输出契约：有效查询退出码为 0、标准错误为空，标准输出只含一个
    UTF-8 JSON 数组及末尾一个换行，每项只含 version 与 title，解析后
    的名称与标题逐码点等于输入，非 ASCII 文本（含 emoji 与组合序列）
    直接输出，不使用 \\uXXXX 转义；
  - 只读性：查询前后通过 show 逐份核对四份草稿的版本名、标题与变更
    文本完全一致，同一路径在新进程中重复查询得到完全相同的结果，
    临时目录不新增文件；
  - 非法前缀：空字符串、缺值、仅含空白或重复提供 --prefix 一律退出码
    1、空标准输出、标准错误恰为 “Invalid draft” 加一个换行；即使数据
    库尚不存在也优先返回此错误且不创建任何文件。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，与既有测试一起重复执行结果一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unicodedata import normalize

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# --- 固定样例常量 ---------------------------------------------------------
# 外观接近的名称一律用码点转义写死，避免两个 é 在源码中被误改成同一串。
# v-😀：U+0076('v') U+002D('-') U+1F600('😀')。
VERSION_EMOJI = "v-\U0001F600"
# v-é：é 为单个预组合码点 U+00E9，名称共 3 个码点。
VERSION_SINGLE = "v-é"
# v-é：é 为基础字母 e(U+0065) 加组合重音 U+0301，名称共 4 个码点。
VERSION_COMBINED = "v-é"
# v-e：纯 ASCII 基础名称。
VERSION_BASE = "v-e"
# v-É：大写 É 为单码点 U+00C9，与小写 U+00E9 不同，无匹配。
PREFIX_UPPER_ACUTE = "v-É"

TITLE_EMOJI = "表情标题"
TITLE_SINGLE = "单字符标题"
TITLE_COMBINED = "组合标题"
TITLE_BASE = "基础标题"

CHANGE_EMOJI = "表情示例变更"
CHANGE_SINGLE = "单字符示例变更"
CHANGE_COMBINED = "组合示例变更"
CHANGE_BASE = "基础示例变更"

# 固定创建顺序：emoji、单字符、组合、基础——与目录排序结果刻意不同。
SAMPLES = [
    (VERSION_EMOJI, TITLE_EMOJI, CHANGE_EMOJI),
    (VERSION_SINGLE, TITLE_SINGLE, CHANGE_SINGLE),
    (VERSION_COMBINED, TITLE_COMBINED, CHANGE_COMBINED),
    (VERSION_BASE, TITLE_BASE, CHANGE_BASE),
]

# 无筛选目录的固定期望（(版本名, 标题) 有序对，顺序显式写死）：
# v-e 是 v-e+U+0301 的前缀且更短，故在组合名称之前；第三码点比较中
# U+0065(e) < U+00E9(é) < U+1F600(😀)，故单字符名称与 emoji 依次在后。
EXPECTED_PAIRS_ALL = [
    (VERSION_BASE, TITLE_BASE),
    (VERSION_COMBINED, TITLE_COMBINED),
    (VERSION_SINGLE, TITLE_SINGLE),
    (VERSION_EMOJI, TITLE_EMOJI),
]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 严格解码：非法字节会直接抛错，emoji、组合
    重音与中文精确可比，不依赖运行环境的区域设置。
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


def create(db_path, version, title, change):
    return run_cli(
        db_path, "create", version, "--title", title, "--change", change
    )


def list_drafts(db_path, *extra_args):
    return run_cli(db_path, "list-drafts", *extra_args)


def prefix_query(db_path, prefix):
    return list_drafts(db_path, "--prefix", prefix)


def show(db_path, version):
    return run_cli(db_path, "show", version)


class UnicodeTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="release-notes-unicode-"
        )
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_draft(self, version, title, change):
        """创建草稿并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, version, title, change)
        self.assertEqual(code, 0, f"create {version!r} 退出码应为 0")
        self.assertEqual(
            out, f"Created {version}\n", f"create {version!r} 标准输出不符"
        )
        self.assertEqual(err, "", f"create {version!r} 标准错误应为空")

    def assert_json_array_contract(self, result, expected_pairs):
        """核对 list-drafts 的公共输出契约，返回解析后的数组。

        expected_pairs 为固定样例显式给出的 (版本名, 标题) 有序对；
        期望值一律来自本模块常量，不由被测输出推导。
        """
        code, out, err = result
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts 标准错误应为空")

        # 标准输出恰为一个单行 JSON 数组加末尾恰一个换行：期望串由固定
        # 输入与公开 JSON 格式（ensure_ascii=False）直接拼出。
        expected_items = [
            {"version": version, "title": title}
            for version, title in expected_pairs
        ]
        expected_out = json.dumps(expected_items, ensure_ascii=False) + "\n"
        self.assertEqual(
            out,
            expected_out,
            "标准输出应恰为一个 UTF-8 JSON 数组加一个末尾换行",
        )
        body = out[:-1]
        self.assertNotIn("\n", body, "JSON 数组应只占一行，无额外换行")

        items = json.loads(body)
        self.assertIsInstance(items, list, "标准输出应解析为一个 JSON 数组")
        self.assertEqual(
            len(items),
            len(expected_pairs),
            "外观接近但码点不同的名称不得被合并",
        )
        for item, (version, title) in zip(items, expected_pairs):
            self.assertIsInstance(item, dict, "数组元素应为 JSON 对象")
            self.assertEqual(
                sorted(item.keys()),
                ["title", "version"],
                "每个元素只应含 version 和 title 两个字段，不出现变更内容",
            )
            self.assertIsInstance(item["version"], str)
            self.assertIsInstance(item["title"], str)
            # 逐码点核对：名称与标题必须与固定输入完全一致，组合序列
            # （e + U+0301）不得被规范化成单码点 U+00E9，反之亦然。
            self.assertEqual(
                [ord(char) for char in item["version"]],
                [ord(char) for char in version],
                "解析后的版本名应与输入逐码点一致",
            )
            self.assertEqual(
                [ord(char) for char in item["title"]],
                [ord(char) for char in title],
                "解析后的标题应与输入逐码点一致",
            )
        return items


class TestFixedSampleCodepoints(unittest.TestCase):
    """固定样例本身的码点不变量：与被测程序无关，仅锁定测试前提。"""

    def test_confusable_names_are_distinct_codepoint_sequences(self):
        self.assertEqual(
            [ord(char) for char in VERSION_EMOJI],
            [0x76, 0x2D, 0x1F600],
        )
        self.assertEqual(
            [ord(char) for char in VERSION_SINGLE], [0x76, 0x2D, 0xE9]
        )
        self.assertEqual(
            [ord(char) for char in VERSION_COMBINED],
            [0x76, 0x2D, 0x65, 0x301],
        )
        self.assertEqual(
            [ord(char) for char in VERSION_BASE], [0x76, 0x2D, 0x65]
        )
        self.assertEqual(
            [ord(char) for char in PREFIX_UPPER_ACUTE],
            [0x76, 0x2D, 0xC9],
        )

        # 两个名称外观接近、NFC 规范化后相等，但原文是不同字符串：
        # 这正是本次回归要防止被合并/互配的样例。
        self.assertEqual(len(VERSION_SINGLE), 3)
        self.assertEqual(len(VERSION_COMBINED), 4)
        self.assertNotEqual(VERSION_SINGLE, VERSION_COMBINED)
        self.assertEqual(
            normalize("NFC", VERSION_COMBINED), VERSION_SINGLE
        )

        # 前缀关系（Python 原文 startswith 语义，即产品公开的匹配规则）：
        # 组合名称以 ASCII v-e 开头，单字符名称则不然；两个 é 互不为前缀。
        self.assertTrue(VERSION_COMBINED.startswith(VERSION_BASE))
        self.assertFalse(VERSION_SINGLE.startswith(VERSION_BASE))
        self.assertFalse(VERSION_SINGLE.startswith(VERSION_COMBINED))
        self.assertFalse(VERSION_COMBINED.startswith(VERSION_SINGLE))
        self.assertFalse(VERSION_SINGLE.startswith(PREFIX_UPPER_ACUTE))


class UnicodeSampleTestCase(UnicodeTestCase):
    """setUp 按固定创建顺序写入四份草稿，供目录与前缀查询共用。"""

    def setUp(self):
        super().setUp()
        for version, title, change in SAMPLES:
            self.create_draft(version, title, change)


class TestCatalogOrderAndTitles(UnicodeSampleTestCase):
    """无筛选目录：固定码点顺序、标题对应与非 ASCII 直出契约。"""

    def test_full_catalog_uses_fixed_codepoint_order_with_correct_titles(self):
        result = list_drafts(self.db)
        items = self.assert_json_array_contract(result, EXPECTED_PAIRS_ALL)

        versions = [item["version"] for item in items]
        # 固定顺序：v-e、v-é(组合)、v-é(单字符)、v-😀。
        self.assertEqual(
            versions,
            [
                VERSION_BASE,
                VERSION_COMBINED,
                VERSION_SINGLE,
                VERSION_EMOJI,
            ],
            "目录应按版本名原文码点升序排列",
        )
        # 该顺序与创建顺序（emoji、单字符、组合、基础）刻意不同。
        self.assertNotEqual(
            versions,
            [sample[0] for sample in SAMPLES],
            "目录顺序不应等于创建顺序",
        )
        # 标题必须与各自原草稿对应，不随外观接近的名称串台。
        self.assertEqual(
            [item["title"] for item in items],
            [TITLE_BASE, TITLE_COMBINED, TITLE_SINGLE, TITLE_EMOJI],
        )

        # 组合名称在输出中仍占 4 个码点，未与单字符名称合并。
        by_version = {item["version"]: item for item in items}
        self.assertEqual(sorted(by_version), sorted(versions))
        self.assertEqual(
            len(by_version[VERSION_COMBINED]["version"]),
            4,
            "组合名称应保留 e + U+0301 两个码点",
        )
        self.assertEqual(
            len(by_version[VERSION_SINGLE]["version"]),
            3,
            "单字符名称应保留单个 U+00E9",
        )

        # 非 ASCII 文本直接输出：emoji、单字符 é 与组合重音序列都以原文
        # 出现，整个输出不含 \\uXXXX 转义。
        _, out, _ = result
        self.assertIn(VERSION_EMOJI, out, "emoji 应直接出现在原始输出中")
        self.assertIn(
            VERSION_SINGLE, out, "单码点 é 应直接出现在原始输出中"
        )
        self.assertIn(
            VERSION_COMBINED, out, "组合重音序列应直接出现在原始输出中"
        )
        self.assertNotIn("\\u", out, "原始输出不应包含 \\u 转义序列")


class TestPrefixCodepointSemantics(UnicodeSampleTestCase):
    """前缀按版本名原文逐码点匹配：外观接近的名称互不匹配。"""

    def test_prefix_single_code_point_acute_matches_only_single(self):
        # --prefix v-é(U+00E9)：只返回单字符名称；组合名称第三码点是
        # U+0065 而非 U+00E9，不得返回。
        items = self.assert_json_array_contract(
            prefix_query(self.db, VERSION_SINGLE),
            [(VERSION_SINGLE, TITLE_SINGLE)],
        )
        self.assertEqual(
            [item["version"] for item in items], [VERSION_SINGLE]
        )

    def test_prefix_combining_sequence_matches_only_combined(self):
        # --prefix v-é(e + U+0301)：只返回组合名称；单字符 v-é 不含
        # U+0065 前缀路径，不得返回。
        items = self.assert_json_array_contract(
            prefix_query(self.db, VERSION_COMBINED),
            [(VERSION_COMBINED, TITLE_COMBINED)],
        )
        self.assertEqual(
            [ord(char) for char in items[0]["version"]],
            [0x76, 0x2D, 0x65, 0x301],
            "返回的必须是 e + U+0301 组合名称",
        )

    def test_prefix_ascii_base_matches_base_then_combined(self):
        # --prefix v-e：命中纯 ASCII 基础名称与组合名称（后者以 v-e 为
        # 前缀），单字符 v-é 与 emoji 不命中；顺序沿用码点升序。
        self.assert_json_array_contract(
            prefix_query(self.db, VERSION_BASE),
            [
                (VERSION_BASE, TITLE_BASE),
                (VERSION_COMBINED, TITLE_COMBINED),
            ],
        )

    def test_prefix_uppercase_acute_has_no_match(self):
        # --prefix v-É(U+00C9)：大小写与码点均不同，只输出 [] 和换行。
        self.assertEqual(
            prefix_query(self.db, PREFIX_UPPER_ACUTE),
            (0, "[]\n", ""),
        )


class TestQueriesReadOnlyAndRepeatable(UnicodeSampleTestCase):
    """查询始终只读：show 文本前后一致，新进程重复查询结果相同。"""

    def test_show_identical_around_queries_and_repeated_queries_match(self):
        # show 的固定期望完全由固定样例常量拼出。
        expected_show = {
            version: (
                0,
                f"Version: {version}\nTitle: {title}\n- {change}\n",
                "",
            )
            for version, title, change in SAMPLES
        }

        files_before = sorted(os.listdir(self._tmpdir.name))

        # 查询前逐份核对版本名、标题与变更文本。
        before = {}
        for version in expected_show:
            result = show(self.db, version)
            self.assertEqual(
                result,
                expected_show[version],
                f"查询前 show {version!r} 基准不符",
            )
            before[version] = result

        # 有效查询集合：无筛选、三个有匹配前缀、一个无匹配前缀。
        queries = [
            ([], EXPECTED_PAIRS_ALL),
            (
                ["--prefix", VERSION_SINGLE],
                [(VERSION_SINGLE, TITLE_SINGLE)],
            ),
            (
                ["--prefix", VERSION_COMBINED],
                [(VERSION_COMBINED, TITLE_COMBINED)],
            ),
            (
                ["--prefix", VERSION_BASE],
                [
                    (VERSION_BASE, TITLE_BASE),
                    (VERSION_COMBINED, TITLE_COMBINED),
                ],
            ),
            (["--prefix", PREFIX_UPPER_ACUTE], []),
        ]
        for extra_args, expected_pairs in queries:
            first = list_drafts(self.db, *extra_args)
            # 每次 run_cli 都是独立的新进程；同一路径重复查询结果逐字节一致。
            second = list_drafts(self.db, *extra_args)
            self.assertEqual(
                first,
                second,
                f"参数 {extra_args!r} 的新进程重复查询结果应完全一致",
            )
            self.assert_json_array_contract(first, expected_pairs)

        # 查询后再次逐份 show：版本名、标题与变更文本与查询前完全一致。
        for version in expected_show:
            after = show(self.db, version)
            self.assertEqual(
                after,
                before[version],
                f"list-drafts 查询不得改变 {version!r} 的版本名、标题或变更",
            )
            self.assertEqual(
                after,
                expected_show[version],
                f"查询后 {version!r} 的版本名、标题与变更文本应保持原样",
            )

        # 查询不新增任何文件（数据库本身在创建草稿时已存在）。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "查询不得在临时目录新增其他文件",
        )


class TestInvalidPrefixArguments(UnicodeTestCase):
    """空串、缺值、纯空白或重复 --prefix 一律报 Invalid draft。"""

    INVALID_CASES = [
        ("空字符串", ["--prefix", ""]),
        ("缺值（--prefix 后无参数）", ["--prefix"]),
        ("纯空格", ["--prefix", "   "]),
        ("制表符与换行组成的空白", ["--prefix", " \t\n "]),
        (
            "重复提供且值相同",
            ["--prefix", VERSION_BASE, "--prefix", VERSION_BASE],
        ),
        (
            "重复提供外观相同但码点不同的值",
            ["--prefix", VERSION_SINGLE, "--prefix", VERSION_COMBINED],
        ),
    ]

    def assert_invalid(self, extra_args):
        code, out, err = list_drafts(self.db, *extra_args)
        self.assertEqual(code, 1, "非法前缀退出码应为 1")
        self.assertEqual(out, "", "非法前缀时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加换行"
        )

    def test_invalid_prefixes_against_populated_database(self):
        for version, title, change in SAMPLES:
            self.create_draft(version, title, change)

        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                self.assert_invalid(extra_args)
                # 非法调用不得改动已有数据：完整目录仍按固定顺序返回。
                self.assert_json_array_contract(
                    list_drafts(self.db), EXPECTED_PAIRS_ALL
                )

    def test_invalid_prefixes_take_precedence_when_database_missing(self):
        # 数据库尚不存在：参数校验优先于数据库访问，且不创建任何文件。
        missing_db = os.path.join(self._tmpdir.name, "absent.sqlite")
        self.assertFalse(os.path.exists(missing_db))

        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                # run_cli 需要显式指定这个尚不存在的路径。
                code, out, err = run_cli(missing_db, "list-drafts", *extra_args)
                self.assertEqual(code, 1, f"{label}：退出码应为 1")
                self.assertEqual(out, "", f"{label}：标准输出应为空")
                self.assertEqual(
                    err,
                    "Invalid draft\n",
                    f"{label}：标准错误应恰为 Invalid draft 加换行",
                )
                self.assertFalse(
                    os.path.exists(missing_db),
                    f"{label} 后不得创建数据库文件",
                )
                self.assertEqual(
                    os.listdir(self._tmpdir.name),
                    [],
                    f"{label} 后不得新增任何文件",
                )


if __name__ == "__main__":
    unittest.main()
