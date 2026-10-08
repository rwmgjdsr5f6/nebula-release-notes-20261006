"""release_notes.py export-changelog 公开命令行行为的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 汇总成功：退出码 0、标准错误为空；按版本名原文 Unicode 码点升序
    依次输出每份草稿的片段，每份片段逐字等同于对该版本执行
    export-markdown 的成功输出，相邻片段之间恰有一个额外换行，最后
    一份之后不追加任何内容；重复条目分别保留；
  - 选中哪些草稿及排列顺序与同一数据库、相同筛选条件的 list-drafts
    完全一致：前缀只匹配版本名原文，区分大小写、不裁剪空白，% 与 _
    是普通字符，标题与变更不参与匹配；
  - 中文标点、Markdown 字符、首尾空格、版本名/标题/变更内部换行均
    原样保留；多行变更仍是一条记录；
  - 省略 --prefix 时汇总全部草稿；显式提供却缺值、值为空或仅含空白、
    重复提供时退出码 1、空标准输出、标准错误恰为 "Invalid draft" 加
    换行；校验先于数据库访问，文件不存在也返回同一错误且不建库；
  - 合法输入但没有匹配项、数据库文件不存在、库中没有 drafts 表、
    drafts 表为空时退出码 0 且两个输出流都为空，不创建数据库或补表；
  - 汇总为只读操作：不新增任何文件，已有草稿的名称、标题、条目数量
    与顺序保持不变，重复执行与跨进程输出逐字相同。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，重复执行结果一致。
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

# 验收固定样例：v2 标题为“次版”、一条变更；v1 标题为“首版”、两条重复
# 变更。创建顺序故意与码点序相反（先 v2 后 v1）。
V2 = "v2"
V2_TITLE = "次版"
V2_CHANGES = ["修正文案"]
V1 = "v1"
V1_TITLE = "首版"
V1_CHANGES = ["新增查询", "新增查询"]

EXPECTED_V1_FRAGMENT = (
    "# v1\n"
    "\n"
    "首版\n"
    "\n"
    "- 新增查询\n"
    "- 新增查询\n"
)

EXPECTED_V2_FRAGMENT = (
    "# v2\n"
    "\n"
    "次版\n"
    "\n"
    "- 修正文案\n"
)

# 汇总期望：v1 片段 + 一个额外换行 + v2 片段，末尾不追加其他内容。
EXPECTED_CHANGELOG = EXPECTED_V1_FRAGMENT + "\n" + EXPECTED_V2_FRAGMENT

# 原样性样例：版本名首字符为空格且含中文，标题含内部换行，变更含多行
# 文本与首尾空格，并覆盖 Markdown / 中文标点字符。
WEIRD_VERSION = " 示例-v1\n第二行"
WEIRD_TITLE = " 标题：# * `x`\n次行 "
WEIRD_CHANGES = [" 变更（一）：A_B | C > D\n第二行  "]

# --prefix 的各种非法取值：缺值由 argparse 层处理（不在此列表），其余
# 为空串、纯空白（空格/制表/换行混合）与重复提供。
BLANK_PREFIXES = ["", "   ", "\t", " \t\n "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。"""
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


def changelog_headings(text):
    """从汇总输出中按行提取所有一级标题原文（去掉 "# " 前缀）。"""
    return [line[2:] for line in text.splitlines() if line.startswith("# ")]


class ExportChangelogTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_acceptance_drafts(self):
        """按验收约定先创建 v2 再创建 v1，并断言创建本身符合预期。"""
        code, out, err = create(self.db, V2, V2_TITLE, V2_CHANGES)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "Created v2\n")
        code, out, err = create(self.db, V1, V1_TITLE, V1_CHANGES)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "Created v1\n")


class TestExportChangelogSuccess(ExportChangelogTestCase):
    def test_full_changelog_order_fragments_separator_and_tail(self):
        self.create_acceptance_drafts()

        code, out, err = run_cli(self.db, "export-changelog")
        self.assertEqual(code, 0, "汇总导出退出码应为 0")
        self.assertEqual(err, "", "成功时标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_CHANGELOG,
            "应先输出 v1 完整片段再输出 v2；片段逐字与单份导出相同，"
            "相邻片段间恰多一个换行，末尾不追加其他内容，重复条目保留两次",
        )

    def test_each_fragment_verbatim_equals_single_export(self):
        self.create_acceptance_drafts()

        code, out, err = run_cli(self.db, "export-changelog")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_V1_FRAGMENT + "\n" + EXPECTED_V2_FRAGMENT,
        )

        # 与实际单份导出进程的输出拼接逐字节比较（使用字节避免解码歧义）。
        singles = []
        for version in (V1, V2):
            r = subprocess.run(
                [sys.executable, SCRIPT, "--db", self.db,
                 "export-markdown", version],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, b"")
            singles.append(r.stdout)
        r = subprocess.run(
            [sys.executable, SCRIPT, "--db", self.db, "export-changelog"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(r.stderr, b"")
        self.assertEqual(r.stdout, singles[0] + b"\n" + singles[1])

    def test_prefix_selects_only_matching_and_equals_single_export(self):
        self.create_acceptance_drafts()

        code, out, err = run_cli(self.db, "export-changelog", "--prefix", "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_V2_FRAGMENT,
            "带 --prefix v2 时只输出 v2 片段，且与单份导出完全相同，"
            "首尾均不追加额外内容",
        )

        code, single, err = run_cli(self.db, "export-markdown", "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, single)

    def test_selection_and_order_match_list_drafts(self):
        self.create_acceptance_drafts()
        # 额外草稿：用于验证大小写、短前缀序、标题不参与匹配与通配符字面量。
        for version, title in [
            ("v10", "后续"),
            ("other", "v2"),
            ("V2", "大写"),
            ("v%1", "百分号"),
            ("v_1", "下划线"),
        ]:
            code, out, err = create(self.db, version, title, ["x"])
            self.assertEqual((code, err), (0, ""), out)

        for prefix in [None, "v", "v2", "V2", "v1", "v%", "v_"]:
            with self.subTest(prefix=prefix):
                extra = ["--prefix", prefix] if prefix is not None else []
                code, listed_json, err = run_cli(
                    self.db, "list-drafts", *extra
                )
                self.assertEqual((code, err), (0, ""))
                code, changelog, err = run_cli(
                    self.db, "export-changelog", *extra
                )
                self.assertEqual((code, err), (0, ""))

                listed = [item["version"] for item in json.loads(listed_json)]
                self.assertEqual(changelog_headings(changelog), listed)

        # other 的标题恰为 "v2"，但标题不参与匹配；V2 因区分大小写被排除。
        code, out, err = run_cli(self.db, "export-changelog", "--prefix", "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(changelog_headings(out), ["v2"])
        self.assertNotIn("other", out)
        self.assertNotIn("V2", out)

    def test_verbatim_spaces_newlines_punctuation_markdown(self):
        code, out, err = create(
            self.db, WEIRD_VERSION, WEIRD_TITLE, WEIRD_CHANGES
        )
        self.assertEqual((code, err), (0, ""), out)

        code, single, err = run_cli(self.db, "export-markdown", WEIRD_VERSION)
        self.assertEqual((code, err), (0, ""))
        code, aggregated, err = run_cli(self.db, "export-changelog")
        self.assertEqual((code, err), (0, ""))
        # 只有一份草稿时汇总输出与单份导出逐字相同（无额外首尾字符）。
        self.assertEqual(aggregated, single)
        expected = (
            "#  示例-v1\n"
            "第二行\n"
            "\n"
            " 标题：# * `x`\n"
            "次行 \n"
            "\n"
            "-  变更（一）：A_B | C > D\n"
            "第二行  \n"
        )
        self.assertEqual(aggregated, expected)


class TestExportChangelogInvalidPrefix(ExportChangelogTestCase):
    def test_invalid_prefix_rejected_before_database_access(self):
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

        # 缺值：--prefix 位于末尾且不带值，argparse 以空串收入。
        code, out, err = run_cli(
            missing_db, "export-changelog", "--prefix"
        )
        self.assertEqual((code, out, err), (1, "", "Invalid draft\n"))

        for index, value in enumerate(BLANK_PREFIXES):
            with self.subTest(value=value):
                code, out, err = run_cli(
                    missing_db, "export-changelog", "--prefix", value
                )
                self.assertEqual((code, out, err), (1, "", "Invalid draft\n"))

        # 重复提供：无论两个值本身是否合法，一律拒绝。
        code, out, err = run_cli(
            missing_db, "export-changelog",
            "--prefix", "v1", "--prefix", "v2",
        )
        self.assertEqual((code, out, err), (1, "", "Invalid draft\n"))

        # 校验先于数据库访问：路径不被创建，目录内无任何文件。
        self.assertFalse(os.path.exists(missing_db))
        self.assertEqual(os.listdir(self._tmpdir.name), [])

    def test_invalid_prefix_leaves_existing_drafts_untouched(self):
        self.create_acceptance_drafts()
        files_before = set(os.listdir(self._tmpdir.name))

        code, out, err = run_cli(
            self.db, "export-changelog", "--prefix", "v1", "--prefix", "v2"
        )
        self.assertEqual((code, out, err), (1, "", "Invalid draft\n"))

        # 已有草稿的名称、标题、条目数量与顺序均不变。
        code, listed, err = run_cli(self.db, "list-drafts")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(listed),
            [
                {"version": "v1", "title": V1_TITLE},
                {"version": "v2", "title": V2_TITLE},
            ],
        )
        code, show_v1, err = run_cli(self.db, "show", "v1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            show_v1,
            "Version: v1\nTitle: 首版\n- 新增查询\n- 新增查询\n",
        )
        self.assertEqual(set(os.listdir(self._tmpdir.name)), files_before)


class TestExportChangelogEmptySuccess(ExportChangelogTestCase):
    def run_changelog(self, db_path, *extra):
        return run_cli(db_path, "export-changelog", *extra)

    def test_valid_prefix_without_match_is_silent_success(self):
        self.create_acceptance_drafts()
        code, out, err = self.run_changelog(self.db, "--prefix", "zzz")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_missing_database_is_silent_success_without_creating_file(self):
        missing_db = os.path.join(self._tmpdir.name, "missing.sqlite")
        code, out, err = self.run_changelog(missing_db)
        self.assertEqual((code, out, err), (0, "", ""))
        code, out, err = self.run_changelog(missing_db, "--prefix", "v1")
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertFalse(os.path.exists(missing_db))
        self.assertEqual(os.listdir(self._tmpdir.name), [])

    def test_database_without_drafts_table_is_silent_success(self):
        other_db = os.path.join(self._tmpdir.name, "other.sqlite")
        conn = sqlite3.connect(other_db)
        try:
            conn.execute("CREATE TABLE unrelated(x)")
            conn.execute("INSERT INTO unrelated VALUES (1)")
            conn.commit()
        finally:
            conn.close()

        code, out, err = self.run_changelog(other_db)
        self.assertEqual((code, out, err), (0, "", ""))
        code, out, err = self.run_changelog(other_db, "--prefix", "v1")
        self.assertEqual((code, out, err), (0, "", ""))

        # 不补建 drafts / changes 表。
        conn = sqlite3.connect(other_db)
        try:
            names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        finally:
            conn.close()
        self.assertEqual(names, ["unrelated"])

    def test_empty_drafts_table_is_silent_success(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(
                "CREATE TABLE drafts(version TEXT PRIMARY KEY,"
                " title TEXT NOT NULL);"
                "CREATE TABLE changes(id INTEGER PRIMARY KEY,"
                " version TEXT, position INTEGER, content TEXT);"
            )
            conn.commit()
        finally:
            conn.close()

        code, out, err = self.run_changelog(self.db)
        self.assertEqual((code, out, err), (0, "", ""))


class TestExportChangelogReadOnly(ExportChangelogTestCase):
    def test_export_does_not_modify_data_or_files_and_is_stable(self):
        self.create_acceptance_drafts()
        files_before = set(os.listdir(self._tmpdir.name))

        def snapshot():
            code, listed, err = run_cli(self.db, "list-drafts")
            assert (code, err) == (0, "")
            return listed

        listed_before = snapshot()
        results = []
        for _ in range(3):
            for extra in ([], ["--prefix", "v1"], ["--prefix", "v2"]):
                results.append(run_cli(self.db, "export-changelog", *extra))
        for code, out, err in results:
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
        # 重复执行结果稳定（相同参数逐字相同）。
        self.assertEqual(results[0], results[3])
        self.assertEqual(results[3], results[6])

        self.assertEqual(snapshot(), listed_before)
        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            files_before,
            "汇总导出不得新增数据库附属文件或发布说明文件",
        )


if __name__ == "__main__":
    unittest.main()
