"""release_notes.py list-drafts 版本名 Unicode 原文语义的离线回归测试。

针对“外观接近但码点不同”的版本名，确认目录查询严格按版本名原文
逐字符（Unicode 码点）处理：不做归一化、不合并、不互相匹配。全部
验收均通过 release_notes.py 的公开命令行（--db 入口）在独立子进程
中进行，使用独立临时 SQLite 数据库，只依赖 Python 3 标准库。

固定样例严格按以下顺序创建，每份草稿一条示例变更：
  1. v-😀  表情标题   （😀 为 U+1F600，单个码点）
  2. v-é   单字符标题 （é 为 U+00E9，预组合单码点，NFC 形态）
  3. v-é   组合标题   （é 为 U+0065 + U+0301，基础字母加组合重音，NFD 形态）
  4. v-e   基础标题

第 2、3 个名称渲染后外观接近、Unicode 归一化后等价，但原文码点
序列不同；第 4 个是第 3 个名称的严格前缀。无筛选目录的固定顺序
（码点升序，较短名称在前）为：
  v-e、v-é(U+0065 U+0301)、v-é(U+00E9)、v-😀(U+1F600)

前缀匹配的固定集合同样不做归一化：
  --prefix v-é(U+00E9)  只返回单字符名称；
  --prefix v-é(U+0065 U+0301)  只返回组合名称；
  --prefix v-e          返回基础名称与组合名称（基础名称在前）；
  --prefix v-É(U+00C9)  无匹配，只输出 [] 加一个换行。

覆盖范围：
  - 无筛选目录的码点顺序与标题对应，四个名称互不合并；
  - 输出契约：退出码 0、标准错误为空，标准输出只有一个单行 UTF-8
    JSON 数组加恰一个末尾换行，每项只含 version 与 title，非 ASCII
    文本（中文、émoji）原文直出而非 \\uXXXX 转义，解析后逐字符
    等于固定输入；
  - 只读与稳定：查询前后用 show 核对四份草稿的版本名、标题与变更
    文本完全一致；同一路径由新进程重复查询得到逐字节相同的结果；
  - 非法参数：空字符串、仅含空白或重复提供 --prefix 时退出码 1、
    标准输出为空、标准错误恰为 "Invalid draft" 加换行；即使数据库
    尚不存在也优先返回此错误，且不创建任何文件。

运行方式（项目根目录）：
    python -m unittest discover -s tests

期望顺序与匹配集合均由上面的固定样例显式给出，不从被测输出推导。
"""

import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# --- 固定样例 -------------------------------------------------------------
# 视觉相近的名称一律用显式码点转义构造，避免源码在编辑或传输中被
# Unicode 归一化后两个名称变成同一串字节。
EMOJI_NAME = "v-\U0001F600"          # v-😀
NFC_NAME = "v-é"              # v-é：单码点 U+00E9
NFD_NAME = "v-é"               # v-é：U+0065 + U+0301
BASE_NAME = "v-e"
UPPER_E_ACUTE_PREFIX = "v-É"  # É 为 U+00C9，无匹配

EMOJI_TITLE, EMOJI_CHANGE = "表情标题", "表情变更示例"
NFC_TITLE, NFC_CHANGE = "单字符标题", "单字符变更示例"
NFD_TITLE, NFD_CHANGE = "组合标题", "组合变更示例"
BASE_TITLE, BASE_CHANGE = "基础标题", "基础变更示例"

# 创建顺序是样例的一部分：表情、单字符、组合、基础。
DRAFTS = [
    (EMOJI_NAME, EMOJI_TITLE, EMOJI_CHANGE),
    (NFC_NAME, NFC_TITLE, NFC_CHANGE),
    (NFD_NAME, NFD_TITLE, NFD_CHANGE),
    (BASE_NAME, BASE_TITLE, BASE_CHANGE),
]

# 无筛选目录的固定期望：码点升序，前缀相同时较短名称在前。
# 此顺序显式写出，不通过对被测输出或创建顺序排序推导。
EXPECTED_ALL_VERSIONS = [BASE_NAME, NFD_NAME, NFC_NAME, EMOJI_NAME]
EXPECTED_ALL_ITEMS = [
    {"version": BASE_NAME, "title": BASE_TITLE},
    {"version": NFD_NAME, "title": NFD_TITLE},
    {"version": NFC_NAME, "title": NFC_TITLE},
    {"version": EMOJI_NAME, "title": EMOJI_TITLE},
]
EXPECTED_BASE_PREFIX_ITEMS = [
    {"version": BASE_NAME, "title": BASE_TITLE},
    {"version": NFD_NAME, "title": NFD_TITLE},
]

# 每个名称固定的码点序列，用于锁定样例定义本身。
EXPECTED_CODEPOINTS = {
    EMOJI_NAME: [0x76, 0x2D, 0x1F600],
    NFC_NAME: [0x76, 0x2D, 0x00E9],
    NFD_NAME: [0x76, 0x2D, 0x0065, 0x0301],
    BASE_NAME: [0x76, 0x2D, 0x0065],
}


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 严格解码：非法字节会直接抛错，中文、
    é、😀 与换行精确可比，不依赖运行环境的区域设置。
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


def create_draft(db_path, version, title, change):
    return run_cli(
        db_path, "create", version, "--title", title, "--change", change
    )


def list_drafts(db_path, *extra_args):
    return run_cli(db_path, "list-drafts", *extra_args)


def prefix_query(db_path, prefix):
    return list_drafts(db_path, "--prefix", prefix)


def show(db_path, version):
    return run_cli(db_path, "show", version)


class UnicodeDraftsTestCase(unittest.TestCase):
    """每个测试使用独立临时目录，并按固定顺序创建四份草稿。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="release-notes-unicode-"
        )
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")
        for version, title, change in DRAFTS:
            code, out, err = create_draft(self.db, version, title, change)
            self.assertEqual(code, 0, f"创建 {version!r} 的退出码应为 0")
            self.assertEqual(
                out, f"Created {version}\n", f"创建 {version!r} 输出不符"
            )
            self.assertEqual(err, "", f"创建 {version!r} 的标准错误应为空")

    def assert_listing_contract(self, result, expected_items):
        """核对 list-drafts 查询的公共输出契约，逐字节比对固定期望。"""
        code, out, err = result
        self.assertEqual(code, 0, "有效查询退出码应为 0")
        self.assertEqual(err, "", "有效查询标准错误应为空")

        # 标准输出恰为一个单行 JSON 数组加末尾恰一个换行；非 ASCII 字符
        # 原文直出（ensure_ascii=False），不应出现 \uXXXX 转义。
        expected_out = json.dumps(expected_items, ensure_ascii=False) + "\n"
        self.assertEqual(
            out,
            expected_out,
            "标准输出应只含一个 UTF-8 JSON 数组及末尾一个换行",
        )
        self.assertNotIn("\n", out[:-1], "JSON 数组正文不应含真实换行")
        self.assertNotIn("\\u", out, "非 ASCII 文本应原文直出而非转义")
        for item in expected_items:
            for text in item.values():
                if any(ord(ch) > 0x7F for ch in text):
                    self.assertIn(text, out, "非 ASCII 原文应直接出现在输出中")

        # 再独立解析一遍：每个元素只含 version、title 两个字符串字段，
        # 解析后的名称与标题与固定输入逐字符相等。
        items = json.loads(out[:-1])
        self.assertIsInstance(items, list)
        for item in items:
            self.assertIsInstance(item, dict)
            self.assertEqual(sorted(item.keys()), ["title", "version"])
            self.assertIsInstance(item["version"], str)
            self.assertIsInstance(item["title"], str)
        self.assertEqual(items, expected_items, "解析结果应与固定输入逐字符一致")
        return items


class TestFixedSampleDefinition(UnicodeDraftsTestCase):
    """锁定固定样例本身：外观相近的名称码点不同、彼此独立。"""

    def test_confusable_names_have_fixed_distinct_codepoints(self):
        for name, codepoints in EXPECTED_CODEPOINTS.items():
            self.assertEqual(
                [ord(ch) for ch in name],
                codepoints,
                f"固定样例 {name!r} 的码点序列应保持不变",
            )
        # 四个名称两两不同，绝不能在输入侧被归一化成同一名称。
        self.assertEqual(len(set(EXPECTED_CODEPOINTS)), 4)
        self.assertNotEqual(NFC_NAME, NFD_NAME, "单字符与组合名称必须不同")
        # 二者仅在归一化后等价，以此固定“外观接近、码点不同”的语义。
        self.assertEqual(
            unicodedata.normalize("NFC", NFD_NAME),
            NFC_NAME,
            "组合名称归一化后才应与单字符名称等价，原文不得相等",
        )

    def test_four_drafts_were_created_without_merging(self):
        # 创建四份名称各异的草稿本身已在 setUp 中成功；目录应返回四项，
        # 证明数据库按原文保存、未把形近名称合并为同一草稿。
        items = self.assert_listing_contract(
            list_drafts(self.db), EXPECTED_ALL_ITEMS
        )
        self.assertEqual(len(items), 4)
        self.assertEqual(
            len({item["version"] for item in items}),
            4,
            "目录中四个形近名称应各自独立，不被合并",
        )


class TestUnicodeListOrderAndPrefixes(UnicodeDraftsTestCase):
    """无筛选顺序与各前缀的固定匹配集合。"""

    def test_no_filter_returns_codepoint_order_with_matching_titles(self):
        items = self.assert_listing_contract(
            list_drafts(self.db), EXPECTED_ALL_ITEMS
        )
        # 顺序与标题归属同时核对：v-e、组合、单字符、表情。
        self.assertEqual(
            [item["version"] for item in items],
            EXPECTED_ALL_VERSIONS,
            "无筛选目录应按版本名原文码点升序排列",
        )
        self.assertEqual(
            [item["title"] for item in items],
            [BASE_TITLE, NFD_TITLE, NFC_TITLE, EMOJI_TITLE],
            "每个版本名应与原草稿标题对应，不得因名称形近而串配",
        )

    def test_prefix_single_codepoint_e_acute_returns_only_nfc_name(self):
        items = self.assert_listing_contract(
            prefix_query(self.db, NFC_NAME),
            [{"version": NFC_NAME, "title": NFC_TITLE}],
        )
        self.assertEqual([item["version"] for item in items], [NFC_NAME])

    def test_prefix_composed_sequence_returns_only_nfd_name(self):
        # 前缀 U+0065 U+0301 逐字符匹配，只命中组合名称；
        # 单字符名称 U+00E9 虽外观相同也不得返回。
        items = self.assert_listing_contract(
            prefix_query(self.db, NFD_NAME),
            [{"version": NFD_NAME, "title": NFD_TITLE}],
        )
        self.assertEqual([item["version"] for item in items], [NFD_NAME])

    def test_prefix_base_returns_base_then_nfd(self):
        # v-e 是组合名称（v-e + U+0301）的严格前缀，二者都返回；
        # 单字符名称 v-é(U+00E9) 第三码点不是 U+0065，不返回。
        items = self.assert_listing_contract(
            prefix_query(self.db, BASE_NAME), EXPECTED_BASE_PREFIX_ITEMS
        )
        self.assertEqual(
            [item["version"] for item in items],
            [BASE_NAME, NFD_NAME],
            "基础名称应在前，其后为组合名称",
        )

    def test_prefix_uppercase_e_acute_has_no_match(self):
        # v-É(U+00C9) 与全部名称原文不同：只输出 [] 加一个换行。
        self.assert_listing_contract(
            prefix_query(self.db, UPPER_E_ACUTE_PREFIX), []
        )
        code, out, err = prefix_query(self.db, UPPER_E_ACUTE_PREFIX)
        self.assertEqual((code, out, err), (0, "[]\n", ""))

    def test_confusable_prefixes_are_not_interchangeable(self):
        # 两个形近前缀的匹配集合必须互不相交，且均不等于基础前缀集合。
        nfc_hit = json.loads(prefix_query(self.db, NFC_NAME)[1])
        nfd_hit = json.loads(prefix_query(self.db, NFD_NAME)[1])
        base_hit = json.loads(prefix_query(self.db, BASE_NAME)[1])
        nfc_versions = {item["version"] for item in nfc_hit}
        nfd_versions = {item["version"] for item in nfd_hit}
        base_versions = {item["version"] for item in base_hit}
        self.assertEqual(nfc_versions, {NFC_NAME})
        self.assertEqual(nfd_versions, {NFD_NAME})
        self.assertEqual(base_versions, {BASE_NAME, NFD_NAME})
        self.assertTrue(nfc_versions.isdisjoint(nfd_versions))


class TestUnicodeQueryReadOnlyAndStable(UnicodeDraftsTestCase):
    """查询始终只读：show 核对四份草稿前后一致，新进程重复查询结果相同。"""

    def expected_show_output(self, version, title, change):
        # show 固定格式同时承载版本名原文、标题原文与变更原文。
        return f"Version: {version}\nTitle: {title}\n- {change}\n"

    def test_queries_leave_all_four_drafts_unchanged(self):
        # 查询前：用 show 按原文精确核对四份草稿的版本名、标题与变更。
        before = {}
        for version, title, change in DRAFTS:
            shown = show(self.db, version)
            self.assertEqual(
                shown,
                (0, self.expected_show_output(version, title, change), ""),
                f"查询前 show {version!r} 基准不符",
            )
            before[version] = shown

        files_before = sorted(os.listdir(self._tmpdir.name))

        # 依次执行无筛选、三个形近前缀与无匹配前缀查询，均为独立进程。
        queries = [
            list_drafts(self.db),
            prefix_query(self.db, NFC_NAME),
            prefix_query(self.db, NFD_NAME),
            prefix_query(self.db, BASE_NAME),
            prefix_query(self.db, UPPER_E_ACUTE_PREFIX),
        ]
        for result in queries:
            self.assertEqual(result[0], 0, "全部查询的退出码应为 0")
            self.assertEqual(result[2], "", "全部查询的标准错误应为空")

        # 查询后：四份草稿的版本名、标题、变更文本与查询前完全一致；
        # 形近名称仍各自精确命中各自的草稿，没有串改或合并。
        for version, title, change in DRAFTS:
            after = show(self.db, version)
            self.assertEqual(
                after,
                before[version],
                f"查询后 show {version!r} 的版本名、标题或变更发生变化",
            )
        # 目录内容与文件集合同样保持原样。
        self.assert_listing_contract(
            list_drafts(self.db), EXPECTED_ALL_ITEMS
        )
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "只读查询不得新增或删除任何文件",
        )

    def test_repeated_queries_in_new_processes_are_identical(self):
        # 同一路径、相同参数，由两个全新进程查询：完整三元组逐字节一致。
        for query in (
            lambda: list_drafts(self.db),
            lambda: prefix_query(self.db, NFC_NAME),
            lambda: prefix_query(self.db, NFD_NAME),
            lambda: prefix_query(self.db, BASE_NAME),
        ):
            first = query()
            second = query()
            self.assertEqual(
                first,
                second,
                "同一路径在新进程中重复查询应得到完全相同的结果",
            )

        # 无筛选重复查询的结果同时等于固定期望。
        self.assert_listing_contract(
            list_drafts(self.db), EXPECTED_ALL_ITEMS
        )


class TestInvalidPrefixArgumentsUnicode(UnicodeDraftsTestCase):
    """空串、纯空白、重复 --prefix 一律 Invalid draft，先于数据库访问。"""

    INVALID_CASES = [
        ("空字符串", ["--prefix", ""]),
        ("仅含空格", ["--prefix", "   "]),
        ("制表符换行等空白", ["--prefix", " \t\n "]),
        ("重复提供且值相同", ["--prefix", NFC_NAME, "--prefix", NFC_NAME]),
        (
            "重复提供且形近值不同",
            ["--prefix", NFC_NAME, "--prefix", NFD_NAME],
        ),
    ]

    def assert_invalid(self, result):
        code, out, err = result
        self.assertEqual(code, 1, "非法前缀退出码应为 1")
        self.assertEqual(out, "", "非法前缀时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加换行"
        )

    def test_invalid_prefixes_against_populated_database(self):
        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                self.assert_invalid(list_drafts(self.db, *extra_args))
                # 非法调用后四份草稿原样保留、顺序不变。
                self.assert_listing_contract(
                    list_drafts(self.db), EXPECTED_ALL_ITEMS
                )

    def test_invalid_prefixes_take_precedence_when_database_missing(self):
        missing_db = os.path.join(self._tmpdir.name, "absent.sqlite")
        self.assertFalse(os.path.exists(missing_db))

        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                # 每次子测试前临时目录内都不应有该数据库文件。
                self.assertFalse(os.path.exists(missing_db))
                self.assert_invalid(list_drafts(missing_db, *extra_args))
                # 参数校验优先：不创建数据库文件，也不留任何其他文件。
                self.assertFalse(
                    os.path.exists(missing_db),
                    f"{label} 后不得创建数据库文件",
                )
                # 临时目录中只有样例库一个文件（由 setUp 创建），不得新增。
                self.assertEqual(
                    sorted(os.listdir(self._tmpdir.name)),
                    [os.path.basename(self.db)],
                    f"{label} 后不得新增任何文件",
                )


if __name__ == "__main__":
    unittest.main()
