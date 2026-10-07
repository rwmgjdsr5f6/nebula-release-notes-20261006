"""release_notes.py list-drafts 目录查询的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 主样例：按 v2、示例、v10、V1、v1 的顺序创建五份草稿，目录结果
    严格按版本名原文逐字符（Unicode 码点）升序为 V1、v1、v10、v2、示例，
    以此区分原文排序、创建顺序与语义版本排序；标题与对应版本一致；
  - 输出契约：退出码 0、标准错误为空，标准输出仅有一个 UTF-8 JSON
    数组及末尾一个换行，每个元素只含 version 和 title 两个字符串字段，
    不出现变更内容；中文直接保留，引号、反斜杠与换行按 JSON 规则转义；
  - 特殊字符样例：版本名与标题的首尾空格、中文、双引号、反斜杠和
    内部换行经解析后与输入原文一致；
  - 不存在的数据库路径与已有空 SQLite 数据库：均返回退出码 0、
    空标准错误与 "[]" 加末尾换行；前者查询后仍不存在，后者不补建
    草稿表，均不新增其他文件；
  - 只读性：含重复文本与多行变更的草稿在查询前后用 show 核对完整
    文本不变；两个独立进程连续查询同一路径结果一致；
  - set-title 后再查询：只有目标版本的标题变化，其他版本及所有
    变更保持原样。

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


def list_drafts(db_path):
    return run_cli(db_path, "list-drafts")


def show(db_path, version):
    return run_cli(db_path, "show", version)


def set_title(db_path, version, title):
    return run_cli(db_path, "set-title", version, "--title", title)


class ListDraftsTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-list-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_draft(self, version, title, changes):
        """创建草稿并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, version, title, changes)
        self.assertEqual(code, 0, f"create {version!r} 退出码应为 0")
        self.assertEqual(out, f"Created {version}\n", f"create {version!r} 标准输出不符")
        self.assertEqual(err, "", f"create {version!r} 标准错误应为空")

    def assert_list_output_contract(self, result, expected_items):
        """核对 list-drafts 的公共输出契约并返回解析后的数组。

        expected_items 为固定输入按公开规则（版本名 Unicode 码点升序）
        给出的 [{"version": ..., "title": ...}, ...] 期望。
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
                "每个元素只应含 version 和 title 两个字段，不出现变更内容",
            )
            self.assertIsInstance(item["version"], str, "version 应为字符串")
            self.assertIsInstance(item["title"], str, "title 应为字符串")

        self.assertEqual(items, expected_items, "目录内容应与固定输入的期望一致")
        return items


class TestMainOrdering(ListDraftsTestCase):
    """主样例：五份草稿的创建顺序与排序结果均不同。"""

    # 固定输入：按 v2、示例、v10、V1、v1 的顺序创建，各自标题不同。
    DRAFTS = [
        ("v2", "第二版标题", ["v2 变更"]),
        ("示例", "示例标题", ["示例变更"]),
        ("v10", "第十版标题", ["v10 变更"]),
        ("V1", "大写首版标题", ["V1 变更"]),
        ("v1", "首版标题", ["v1 变更"]),
    ]

    # 公开排序规则：版本名原文逐字符（Unicode 码点）升序，前缀相同时
    # 较短名称在前，不做语义版本解析，不合并大小写。期望顺序由此固定给出：
    # "V1"(U+0056) < "v1" < "v10"（"v1" 为前缀且更短）< "v2" < "示例"(U+793A)。
    EXPECTED_ITEMS = [
        {"version": "V1", "title": "大写首版标题"},
        {"version": "v1", "title": "首版标题"},
        {"version": "v10", "title": "第十版标题"},
        {"version": "v2", "title": "第二版标题"},
        {"version": "示例", "title": "示例标题"},
    ]

    def test_catalog_order_titles_and_output_contract(self):
        for version, title, changes in self.DRAFTS:
            self.create_draft(version, title, changes)

        code, out, err = list_drafts(self.db)
        items = self.assert_list_output_contract((code, out, err), self.EXPECTED_ITEMS)

        # 解析后的版本顺序严格为 V1、v1、v10、v2、示例：
        # 既不是创建顺序（v2、示例、v10、V1、v1），也不是语义版本排序。
        self.assertEqual(
            [item["version"] for item in items],
            ["V1", "v1", "v10", "v2", "示例"],
            "版本顺序应为原文逐字符升序，而非创建顺序或语义版本排序",
        )

        # 原始输出中的中文直接保留，不使用 \\uXXXX 转义。
        self.assertIn("示例", out, "原始输出中的中文应直接保留")
        self.assertNotIn("\\u", out, "原始输出不应包含 \\u 转义序列")


class TestSpecialCharacters(ListDraftsTestCase):
    """版本名与标题中的特殊字符：解析结果与输入原文一致，转义遵守 JSON 规则。"""

    # 固定输入：覆盖首尾空格、中文、双引号、反斜杠与内部换行。
    DRAFTS = {
        " 空格版本 ": " 首尾空格标题 ",
        '引号"版本': '标题含"双引号',
        "v\\2": "反斜杠 \\ 标题",
        "多\n行": "第一行\n第二行",
        "中文": "中文标题",
    }

    def test_special_characters_round_trip_verbatim(self):
        for version, title in self.DRAFTS.items():
            self.create_draft(version, title, ["一条变更"])

        code, out, err = list_drafts(self.db)
        # 期望由固定输入按公开排序规则（版本名码点升序）给出。
        expected_items = [
            {"version": version, "title": self.DRAFTS[version]}
            for version in sorted(self.DRAFTS)
        ]
        self.assert_list_output_contract((code, out, err), expected_items)

        body = out[:-1]
        # 中文直接保留，不转义。
        self.assertIn("中文标题", body, "原始输出中的中文应直接保留")
        self.assertNotIn("\\u", body, "原始输出不应包含 \\u 转义序列")
        # 双引号、反斜杠与内部换行按 JSON 规则转义。
        self.assertIn('\\"', body, "双引号应转义为 \\\"")
        self.assertIn("v\\\\2", body, "反斜杠应转义为 \\\\")
        self.assertIn("\\n", body, "内部换行应转义为 \\n")


class TestEmptyCatalog(ListDraftsTestCase):
    """不存在的数据库路径与已有空 SQLite 数据库均返回空数组。"""

    def assert_empty_catalog(self, db_path):
        code, out, err = list_drafts(db_path)
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts  标准错误应为空")
        self.assertEqual(out, "[]\n", "空目录标准输出应恰为 [] 加末尾换行")

    def test_missing_database_path_stays_missing(self):
        self.assertEqual(os.listdir(self._tmpdir.name), [], "临时目录初始应为空")

        self.assert_empty_catalog(self.db)

        self.assertFalse(
            os.path.exists(self.db), "查询不存在的路径不得创建数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "查询不得在临时目录新增其他文件"
        )

    def test_existing_empty_sqlite_database_stays_empty(self):
        # 先建立一个不含任何表的合法空 SQLite 数据库（含文件头）。
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE _init (x INTEGER)")
        conn.execute("DROP TABLE _init")
        conn.commit()
        conn.close()
        self.assertTrue(os.path.exists(self.db))

        files_before = sorted(os.listdir(self._tmpdir.name))
        self.assert_empty_catalog(self.db)

        # 不补建 drafts 表，也不新增任何其他表。
        conn = sqlite3.connect(self.db)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(tables, [], "查询不得为空数据库补建任何表")
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "查询不得在临时目录新增其他文件",
        )


class TestReadOnlyAndStable(ListDraftsTestCase):
    """查询为只读且跨进程稳定：草稿内容不变，连续查询结果一致。"""

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

    def test_show_unchanged_around_list_and_repeated_list_is_identical(self):
        self.create_draft(self.VERSION, self.TITLE, self.CHANGES)
        self.create_draft("demo-2", "另一草稿", ["另一条变更"])

        # 查询前用 show 核对完整文本基准。
        before = show(self.db, self.VERSION)
        self.assertEqual(before, (0, self.EXPECTED_SHOW, ""), "查询前 show 基准不符")

        # 两个独立进程连续查询同一路径：完整目录结果一致。
        first = list_drafts(self.db)
        second = list_drafts(self.db)
        self.assertEqual(first, second, "两次独立进程 list-drafts 的结果应完全一致")
        self.assert_list_output_contract(
            first,
            [
                {"version": "demo-1", "title": " 含重复与多行的草稿 "},
                {"version": "demo-2", "title": "另一草稿"},
            ],
        )

        # 查询后用 show 复核：标题、条目数量、原文及顺序未改变。
        after = show(self.db, self.VERSION)
        self.assertEqual(after, before, "list-drafts 不得改变草稿内容")
        self.assertEqual(
            after,
            (0, self.EXPECTED_SHOW, ""),
            "查询后标题、重复条目与多行条目的原文及顺序应保持不变",
        )


class TestSetTitleThenList(ListDraftsTestCase):
    """set-title 后再查询：只有目标版本的标题变化。"""

    DRAFTS = [
        ("alpha", "甲标题", ["甲变更一", "甲变更二"]),
        ("beta", "乙标题", ["乙变更"]),
        ("gamma", "丙标题", ["丙变更"]),
    ]
    NEW_BETA_TITLE = " 乙标题（修订） "

    def test_only_target_title_changes(self):
        for version, title, changes in self.DRAFTS:
            self.create_draft(version, title, changes)

        # 更新前的目录基准。
        self.assert_list_output_contract(
            list_drafts(self.db),
            [
                {"version": "alpha", "title": "甲标题"},
                {"version": "beta", "title": "乙标题"},
                {"version": "gamma", "title": "丙标题"},
            ],
        )

        code, out, err = set_title(self.db, "beta", self.NEW_BETA_TITLE)
        self.assertEqual((code, out, err), (0, "Updated title: beta\n", ""))

        # 再查询：只有 beta 的标题变化，其他版本标题与排列保持原样。
        self.assert_list_output_contract(
            list_drafts(self.db),
            [
                {"version": "alpha", "title": "甲标题"},
                {"version": "beta", "title": " 乙标题（修订） "},
                {"version": "gamma", "title": "丙标题"},
            ],
        )

        # 所有变更的原文、数量与顺序不因标题修订而改变。
        code, out, err = show(self.db, "beta")
        self.assertEqual(
            (code, out, err),
            (0, "Version: beta\nTitle:  乙标题（修订） \n- 乙变更\n", ""),
            "set-title 后 beta 的变更应保持原样",
        )
        code, out, err = show(self.db, "alpha")
        self.assertEqual(
            (code, out, err),
            (0, "Version: alpha\nTitle: 甲标题\n- 甲变更一\n- 甲变更二\n", ""),
            "set-title 不得影响其他版本的标题与变更",
        )


if __name__ == "__main__":
    unittest.main()
