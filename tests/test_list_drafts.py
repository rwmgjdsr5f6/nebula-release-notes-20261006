"""release_notes.py 公开命令 list-drafts 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 主样例：按 v2、示例、v10、V1、v1 的顺序创建五份草稿，目录数组的
    版本顺序严格为 V1、v1、v10、v2、示例（版本名原文逐字符比较，而非
    创建顺序或语义版本排序），标题与对应版本一致；
  - 输出契约：退出码 0、标准错误为空、标准输出仅为一个 UTF-8 JSON
    数组加一个末尾换行，每个元素只含 version 与 title 两个字符串字段，
    变更内容不出现在目录中；
  - 特殊字符样例：版本名与标题中的首尾空格、中文、双引号、反斜杠与
    内部换行按原文返回；原始输出中文直接保留，需转义字符遵守 JSON 规则；
  - 不存在的数据库路径与已有空 SQLite 数据库：均返回退出码 0、空标准
    错误与 "[]" 加末尾换行；前者查询后仍不存在，后者不补建草稿表，
    二者均不新增其他文件；
  - 目录查询为只读：含重复文本与多行变更的草稿在查询前后用 show 核对
    完整文本逐字不变；两个独立进程连续查询同一路径结果完全一致；
  - set-title 后再查询：仅目标版本的标题变化，其他版本与所有变更
    保持原样。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，重复执行结果一致。所有期望值均由固定输入与
公开格式直接给出，不取自被测查询结果。
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

# 主样例：按此顺序创建；每个版本的变更文本带有不出现在标题中的标记，
# 用于确认目录输出不含变更内容。
MAIN_DRAFTS = [
    ("v2", "第二版", ["变更内容-甲"]),
    ("示例", "示例标题", ["变更内容-乙"]),
    ("v10", "第十版", ["变更内容-丙"]),
    ("V1", "大写首版", ["变更内容-丁"]),
    ("v1", "小写首版", ["变更内容-戊"]),
]

# 目录数组的版本顺序：按版本名原文逐字符（Unicode 码点）升序，
# 前缀相同时较短名称在前；既不是创建顺序，也不是语义版本排序。
MAIN_EXPECTED = [
    {"version": "V1", "title": "大写首版"},
    {"version": "v1", "title": "小写首版"},
    {"version": "v10", "title": "第十版"},
    {"version": "v2", "title": "第二版"},
    {"version": "示例", "title": "示例标题"},
]

# 特殊字符样例：覆盖首尾空格、中文、双引号、反斜杠与内部换行。
SPECIAL_DRAFTS = [
    (" 首尾空格 ", " 标题两侧有空格 ", ["变更一"]),
    ("ascii", "引号\"与反斜\\杠", ["变更二"]),
    ("反斜\\杠", "反斜杠\\标题", ["变更三"]),
    ("引号\"内", "双引号\"标题\"", ["变更四"]),
    ("换行\n中间", "标题首行\n标题次行", ["变更五"]),
]

# 同样按版本名原文码点升序排列的期望目录。
SPECIAL_EXPECTED = [
    {"version": " 首尾空格 ", "title": " 标题两侧有空格 "},
    {"version": "ascii", "title": "引号\"与反斜\\杠"},
    {"version": "反斜\\杠", "title": "反斜杠\\标题"},
    {"version": "引号\"内", "title": "双引号\"标题\""},
    {"version": "换行\n中间", "title": "标题首行\n标题次行"},
]


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


def show(db_path, version):
    return run_cli(db_path, "show", version)


def list_drafts(db_path):
    return run_cli(db_path, "list-drafts")


def set_title(db_path, version, title):
    return run_cli(db_path, "set-title", version, "--title", title)


class ListDraftsTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_drafts(self, drafts):
        """按给定顺序创建草稿，并断言创建过程本身符合预期。"""
        for version, title, changes in drafts:
            code, out, err = create(self.db, version, title, changes)
            self.assertEqual(code, 0, f"create {version!r} 退出码应为 0")
            self.assertEqual(out, f"Created {version}\n", "create 标准输出不符")
            self.assertEqual(err, "", "create 标准错误应为空")

    def assert_list_output_contract(self, result, expected_items):
        """核对 list-drafts 的公共输出契约，返回解析后的数组。

        契约：退出码 0；标准错误为空；标准输出仅为一个 JSON 数组加一个
        末尾换行；每个元素只含 version 与 title 两个字符串字段；解析
        结果与期望逐项一致（不限定对象键序与无意义空格）。
        """
        code, out, err = result
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts 标准错误应为空")
        self.assertTrue(out.startswith("["), "标准输出应以 JSON 数组开头")
        self.assertTrue(out.endswith("]\n"), "标准输出应以 JSON 数组加换行结尾")
        self.assertFalse(
            out.endswith("]\n\n"), "标准输出末尾应恰有一个换行"
        )

        items = json.loads(out)
        self.assertIsInstance(items, list, "标准输出应解析为 JSON 数组")
        for item in items:
            self.assertEqual(
                set(item.keys()),
                {"version", "title"},
                "每个元素应只含 version 与 title 两个字段",
            )
            self.assertIsInstance(item["version"], str)
            self.assertIsInstance(item["title"], str)
        self.assertEqual(items, expected_items, "目录内容应与固定期望一致")
        return items


class TestListDraftsOrdering(ListDraftsTestCase):
    def test_versions_sorted_by_literal_name_not_creation_or_semver(self):
        self.create_drafts(MAIN_DRAFTS)

        result = list_drafts(self.db)
        items = self.assert_list_output_contract(result, MAIN_EXPECTED)

        # 显式核对版本顺序：与创建顺序（v2、示例、v10、V1、v1）不同，
        # 也不是语义版本排序（v10 排在 v2 之前）。
        self.assertEqual(
            [item["version"] for item in items],
            ["V1", "v1", "v10", "v2", "示例"],
        )

    def test_listing_does_not_include_change_content(self):
        self.create_drafts(MAIN_DRAFTS)

        code, out, err = list_drafts(self.db)
        self.assertEqual((code, err), (0, ""))
        for _, _, changes in MAIN_DRAFTS:
            for change in changes:
                self.assertNotIn(
                    change, out, "目录输出不应包含任何变更内容"
                )


class TestListDraftsSpecialCharacters(ListDraftsTestCase):
    def test_special_characters_roundtrip_verbatim(self):
        self.create_drafts(SPECIAL_DRAFTS)

        code, out, err = list_drafts(self.db)
        self.assertEqual((code, err), (0, ""))

        # 解析结果与输入原文逐项一致。
        items = json.loads(out)
        self.assertEqual(items, SPECIAL_EXPECTED)

        # 原始输出中的中文直接保留（不按 \\uXXXX 转义）。
        for text in ("首尾空格", "反斜", "引号", "换行", "标题"):
            self.assertIn(text, out, f"原始输出应直接保留中文：{text}")

        # 需转义的字符遵守 JSON 规则：双引号、反斜杠与内部换行均以
        # 转义形式出现，整个数组输出为单行（唯一的换行在末尾）。
        self.assertIn('\\"', out, "双引号应按 JSON 规则转义")
        self.assertIn("\\\\", out, "反斜杠应按 JSON 规则转义")
        self.assertIn("\\n", out, "内部换行应按 JSON 规则转义")
        self.assertEqual(
            out.count("\n"), 1, "原始输出应只有末尾一个换行"
        )
        self.assertTrue(out.endswith("]\n"))


class TestListDraftsEmptyDatabase(ListDraftsTestCase):
    def test_missing_database_path_returns_empty_array_without_creating_file(self):
        self.assertFalse(os.path.exists(self.db))

        code, out, err = list_drafts(self.db)
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts 标准错误应为空")
        self.assertEqual(out, "[]\n", "不存在的路径应返回空数组")

        # 查询后该路径仍不存在，临时目录内不新增任何文件。
        self.assertFalse(
            os.path.exists(self.db), "查询不得在尚不存在的路径产生数据库文件"
        )
        self.assertEqual(
            os.listdir(self._tmpdir.name), [], "查询不得新增其他文件"
        )

    def test_existing_empty_sqlite_database_returns_empty_array(self):
        # 先建立一个不含 drafts 表的空 SQLite 数据库文件。
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE placeholder (id INTEGER)")
        conn.execute("DROP TABLE placeholder")
        conn.commit()
        conn.close()
        self.assertTrue(os.path.exists(self.db))

        code, out, err = list_drafts(self.db)
        self.assertEqual(code, 0, "list-drafts 退出码应为 0")
        self.assertEqual(err, "", "list-drafts 标准错误应为空")
        self.assertEqual(out, "[]\n", "空数据库应返回空数组")

        # 查询不补建 drafts 表，也不新增其他文件。
        conn = sqlite3.connect(self.db)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            conn.close()
        self.assertNotIn("drafts", tables, "查询不得补建草稿表")
        self.assertEqual(
            os.listdir(self._tmpdir.name),
            ["notes.sqlite"],
            "查询不得新增其他文件",
        )


class TestListDraftsReadOnly(ListDraftsTestCase):
    VERSION = "demo-1"
    TITLE = "只读核对"
    CHANGES = ["重复文本", "重复文本", "第一行\n第二行"]
    EXPECTED_SHOW = (
        "Version: demo-1\n"
        "Title: 只读核对\n"
        "- 重复文本\n"
        "- 重复文本\n"
        "- 第一行\n"
        "第二行\n"
    )

    def test_listing_preserves_draft_with_duplicates_and_multiline_change(self):
        self.create_drafts([(self.VERSION, self.TITLE, self.CHANGES)])

        # 查询前用 show 记录完整文本。
        before = show(self.db, self.VERSION)
        self.assertEqual(before, (0, self.EXPECTED_SHOW, ""))

        code, out, err = list_drafts(self.db)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(out),
            [{"version": self.VERSION, "title": self.TITLE}],
        )

        # 查询后再次 show：标题、条目数量、原文及顺序逐字不变。
        after = show(self.db, self.VERSION)
        self.assertEqual(after, before, "目录查询不得改变草稿内容")
        self.assertEqual(after, (0, self.EXPECTED_SHOW, ""))

    def test_two_independent_processes_return_identical_listing(self):
        self.create_drafts(MAIN_DRAFTS)

        first = list_drafts(self.db)
        second = list_drafts(self.db)
        self.assertEqual(
            first, second, "两个独立进程查询同一路径的结果应完全一致"
        )
        self.assert_list_output_contract(first, MAIN_EXPECTED)


class TestListDraftsAfterSetTitle(ListDraftsTestCase):
    NEW_TITLE = " 修订标题\n第二行 "

    def test_set_title_changes_only_that_version_in_listing(self):
        self.create_drafts(MAIN_DRAFTS)

        # 修订前的目录基准。
        before = list_drafts(self.db)
        self.assert_list_output_contract(before, MAIN_EXPECTED)

        code, out, err = set_title(self.db, "v10", self.NEW_TITLE)
        self.assertEqual((code, out, err), (0, "Updated title: v10\n", ""))

        # 修订后：仅 v10 的标题变化，其他版本的版本名与标题保持原样。
        expected_after = [
            {"version": "V1", "title": "大写首版"},
            {"version": "v1", "title": "小写首版"},
            {"version": "v10", "title": self.NEW_TITLE},
            {"version": "v2", "title": "第二版"},
            {"version": "示例", "title": "示例标题"},
        ]
        self.assert_list_output_contract(list_drafts(self.db), expected_after)

        # v10 的变更内容保持原样（标题已更新，变更逐字不变）。
        code, out, err = show(self.db, "v10")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            f"Version: v10\nTitle: {self.NEW_TITLE}\n- 变更内容-丙\n",
            "set-title 后 v10 的变更应保持原样",
        )

        # 其他版本的标题与变更均不受影响的抽查。
        code, out, err = show(self.db, "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v2\nTitle: 第二版\n- 变更内容-甲\n",
            "set-title 不得影响其他版本",
        )


if __name__ == "__main__":
    unittest.main()
