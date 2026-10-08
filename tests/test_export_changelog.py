"""release_notes.py export-changelog 汇总导出公开命令行行为的离线回归测试。

只通过 release_notes.py 的公开命令行在独立子进程中验收，不依赖内部表
结构，不访问用户数据库，不联网。覆盖范围：
  - 验收主样例：v2（标题 次版，变更 修正文案）、v1（标题 首版，变更
    依次为两条“新增查询”）；不带筛选时先输出 v1 的完整片段再输出 v2，
    重复条目保留两次；带 --prefix v2 时只输出 v2 的片段，且与对 v2
    执行 export-markdown 的成功输出逐字节相同；
  - 片段与拼接契约：每份草稿片段逐字等于该版本的单份导出，相邻片段
    之间恰好额外插入一个换行（片段本身已以一个换行结尾），最后一份
    之后不追加任何内容；中文、标点、Markdown 字符、首尾空格与内部
    换行原样保留，多行变更仍是一条记录；
  - 选取与排序一致性：选中的草稿及顺序与同库同筛选条件的 list-drafts
    完全一致（版本名原文 Unicode 码点升序，不解析语义版本）；前缀只
    匹配版本名，区分大小写、不裁剪空白，% 与 _ 为普通字符，标题与
    变更不参与匹配；
  - 非法参数：显式提供 --prefix 却缺值、值为空或仅含空白、重复提供时
    退出码 1、标准输出为空、标准错误恰为 “Invalid draft” 加一个换行；
    校验先于数据库读取，数据库文件不存在时也返回同一错误且不创建文件；
  - 空结果：有效输入但没有匹配项、数据库文件不存在、库中没有 drafts
    表、drafts 表为空时，统一退出码 0 且标准输出与标准错误均为零字节；
  - 只读与稳定：汇总不创建数据库、不补建表、不生成输出文件、不改动
    其他表与已有草稿（名称、标题、条目数量与顺序不变）；两个独立进程
    读取同一路径得到完全一致的结果。

运行方式（项目根目录）：
    python -m unittest discover -s tests
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


def run_cli_bytes(db_path, *args):
    """同 run_cli 但保留原始字节，用于核对“零字节输出”等字节级契约。"""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", db_path, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode, result.stdout, result.stderr


def create(db_path, version, title, changes):
    args = ["create", version, "--title", title]
    for change in changes:
        args += ["--change", change]
    return run_cli(db_path, *args)


def show(db_path, version):
    return run_cli(db_path, "show", version)


def list_drafts(db_path, *extra_args):
    return run_cli(db_path, "list-drafts", *extra_args)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


def export_changelog(db_path, *extra_args):
    return run_cli(db_path, "export-changelog", *extra_args)


# 验收主样例的固定输入与独立写明的期望片段（按 export-markdown 固定格式
# 规则直接写出，而非由程序输出推导）。
V1_FRAGMENT = (
    "# v1\n"
    "\n"
    "首版\n"
    "\n"
    "- 新增查询\n"
    "- 新增查询\n"
)
V2_FRAGMENT = (
    "# v2\n"
    "\n"
    "次版\n"
    "\n"
    "- 修正文案\n"
)
# 相邻片段之间恰好额外插入一个换行：v1 片段末尾自带换行，故视觉上
# 两份片段之间隔着一个空行；v2 片段之后不追加任何内容。
EXPECTED_ALL = V1_FRAGMENT + "\n" + V2_FRAGMENT


class ChangelogTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(
            prefix="release-notes-changelog-"
        )
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_acceptance_drafts(self):
        """按验收样例创建 v2、v1（创建顺序故意与汇总顺序相反）。"""
        code, out, err = create(
            self.db, "v2", "次版", ["修正文案"]
        )
        self.assertEqual((code, out, err), (0, "Created v2\n", ""))
        code, out, err = create(
            self.db, "v1", "首版", ["新增查询", "新增查询"]
        )
        self.assertEqual((code, out, err), (0, "Created v1\n", ""))


class TestExportChangelogAcceptance(ChangelogTestCase):
    """验收主样例：完整汇总、前缀汇总与单份导出逐字一致。"""

    def test_no_prefix_outputs_v1_then_v2_with_duplicate_kept(self):
        self.create_acceptance_drafts()

        code, out, err = export_changelog(self.db)
        self.assertEqual(code, 0, "汇总导出退出码应为 0")
        self.assertEqual(err, "", "标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_ALL,
            "应先输出 v1 完整片段再输出 v2；重复条目保留两次；"
            "相邻片段之间恰好多一个换行，末份之后无追加内容",
        )
        # “新增查询”在汇总中出现两次，证明不去重、按条目逐条输出。
        self.assertEqual(out.count("- 新增查询\n"), 2)

    def test_each_fragment_identical_to_single_export_and_join_rule(self):
        self.create_acceptance_drafts()

        v1 = export_markdown(self.db, "v1")
        v2 = export_markdown(self.db, "v2")
        self.assertEqual((v1[0], v1[2]), (0, ""))
        self.assertEqual((v2[0], v2[2]), (0, ""))
        self.assertEqual(v1[1], V1_FRAGMENT)
        self.assertEqual(v2[1], V2_FRAGMENT)

        code, out, err = export_changelog(self.db)
        self.assertEqual((code, err), (0, ""))
        # 拼接规则独立复算：单份导出片段以一个换行连接，且与汇总逐字相同。
        self.assertEqual(out, "\n".join([v1[1], v2[1]]))

        # 最后一份之后不追加其他内容：输出恰好以 v2 片段的末字节（换行）
        # 结束，其后没有第二个换行。
        self.assertTrue(out.endswith("- 修正文案\n"))
        self.assertFalse(out.endswith("- 修正文案\n\n"))

    def test_prefix_v2_outputs_only_v2_identical_to_single_export(self):
        self.create_acceptance_drafts()

        single = export_markdown(self.db, "v2")
        self.assertEqual((single[0], single[2]), (0, ""))

        code, out, err = export_changelog(self.db, "--prefix", "v2")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            single[1],
            "只命中一份草稿时，汇总输出应与单份导出完全相同，不多也不少",
        )
        self.assertNotIn("# v1", out)

    def test_prefix_selection_matches_list_drafts_exactly(self):
        self.create_acceptance_drafts()

        # 选中范围与排列顺序必须与相同筛选条件下的 list-drafts 完全一致。
        for extra in ([], ["--prefix", "v2"], ["--prefix", "v1"],
                      ["--prefix", "v"]):
            with self.subTest(extra=extra):
                code, listing, err = list_drafts(self.db, *extra)
                self.assertEqual((code, err), (0, ""))
                versions = [item["version"] for item in json.loads(listing)]

                fragments = []
                for version in versions:
                    result = export_markdown(self.db, version)
                    self.assertEqual((result[0], result[2]), (0, ""))
                    fragments.append(result[1])
                expected = "\n".join(fragments)

                code, out, err = export_changelog(self.db, *extra)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(out, expected)


class TestSelectionAndOrderingParity(ChangelogTestCase):
    """选中范围/顺序与 list-drafts 一致：码点序、大小写、特殊字符、中文。"""

    def setUp(self):
        super().setUp()
        # 故意打乱创建顺序，且标题/变更中混入前缀文本，证明不参与匹配。
        drafts = [
            ("v2", "含 v1 的第二版标题", ["变更也含 v1"]),
            ("v10", "第十版标题", ["第十版变更"]),
            ("V1", "大写首版标题", ["大写变更"]),
            ("v1", "首版标题", ["首版变更"]),
            ("v%1", "百分号版本", ["百分号变更"]),
            ("v_1", "下划线版本", ["下划线变更"]),
        ]
        for version, title, changes in drafts:
            code, out, err = create(self.db, version, title, changes)
            self.assertEqual(code, 0, version)
            self.assertEqual(err, "")

    def aggregate_versions(self, *extra):
        """以 list-drafts 为基准返回应选中的版本名顺序。"""
        code, listing, err = list_drafts(self.db, *extra)
        self.assertEqual((code, err), (0, ""))
        return [item["version"] for item in json.loads(listing)]

    def assert_aggregate_matches_fragments(self, *extra):
        versions = self.aggregate_versions(*extra)
        fragments = []
        for version in versions:
            result = export_markdown(self.db, version)
            self.assertEqual((result[0], result[2]), (0, ""))
            fragments.append(result[1])

        code, out, err = export_changelog(self.db, *extra)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "\n".join(fragments))
        return versions, out

    def test_codepoint_order_not_semver(self):
        versions, _ = self.assert_aggregate_matches_fragments()
        # 码点升序：V1(U+0056) < v...；v1 是 v10 的前缀故 v1 在 v10 前；
        # v10 与 v2 比较第二位 '1'(U+0031) < '2'(U+0032)。
        self.assertEqual(
            versions, ["V1", "v%1", "v1", "v10", "v2", "v_1"]
        )
        # 汇总正文中标题出现顺序同样遵守该顺序。
        code, out, err = export_changelog(self.db)
        self.assertEqual(err, "")
        positions = [out.index(f"# {v}\n") for v in versions]
        self.assertEqual(positions, sorted(positions))

    def test_prefix_is_case_sensitive(self):
        versions, _ = self.assert_aggregate_matches_fragments("--prefix", "V1")
        self.assertEqual(versions, ["V1"])
        versions, _ = self.assert_aggregate_matches_fragments("--prefix", "v1")
        self.assertEqual(versions, ["v1", "v10"])

    def test_percent_and_underscore_are_literal(self):
        versions, _ = self.assert_aggregate_matches_fragments("--prefix", "v%")
        self.assertEqual(versions, ["v%1"])
        versions, _ = self.assert_aggregate_matches_fragments("--prefix", "v_")
        self.assertEqual(versions, ["v_1"])

    def test_titles_and_changes_do_not_participate_in_matching(self):
        # v2 的标题与变更都含 “v1”，但前缀 v1 不得把 v2 带入汇总。
        code, out, err = export_changelog(self.db, "--prefix", "v1")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("# v1\n", out)
        self.assertIn("# v10\n", out)
        self.assertNotIn("# v2\n", out)

    def test_chinese_prefix_and_internal_newline_in_version_name(self):
        create(self.db, "中文1", "中文标题一", ["变更一"])
        create(self.db, "中文 1", "带空格的中文标题", ["变更二"])
        create(self.db, "a\nb", "换行标题", ["多行\n变更"])

        versions, _ = self.assert_aggregate_matches_fragments(
            "--prefix", "中文"
        )
        # 码点序中空格(U+0020)排在数字 1(U+0031)之前。
        self.assertEqual(versions, ["中文 1", "中文1"])

        # 版本名内部换行按原文逐字符参与前缀匹配，且片段逐字保留。
        versions, out = self.assert_aggregate_matches_fragments(
            "--prefix", "a\n"
        )
        self.assertEqual(versions, ["a\nb"])
        single = export_markdown(self.db, "a\nb")
        self.assertEqual(out, single[1])


class TestVerbatimContent(ChangelogTestCase):
    """中文、标点、Markdown 字符、首尾空格、多行变更逐字保留。"""

    VERSION = "示例-0.2\n第二行版本名"
    TITLE = " 预览，标题：#标记 *重点* `代码`\n第二行 "
    CHANGES = [" 变更（一）：[链接](url)、A_B | C > D\n第二行  "]
    EXPECTED_SHOW = (
        "Version: 示例-0.2\n"
        "第二行版本名\n"
        "Title:  预览，标题：#标记 *重点* `代码`\n"
        "第二行 \n"
        "-  变更（一）：[链接](url)、A_B | C > D\n"
        "第二行  \n"
    )

    def test_special_content_preserved_verbatim_via_single_export(self):
        code, out, err = create(
            self.db, self.VERSION, self.TITLE, self.CHANGES
        )
        self.assertEqual((code, err), (0, ""))
        # 再建一份普通草稿，确保汇总时两份片段都逐字拼接。
        create(self.db, "zzz", "末尾版", ["末尾变更"])

        single = export_markdown(self.db, self.VERSION)
        self.assertEqual((single[0], single[2]), (0, ""))

        code, out, err = export_changelog(self.db)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(single[1], out)
        # 特殊片段作为整体出现：其版本名内部换行、标题首尾空格、变更
        # 首尾空格与内部换行都不被整理。
        self.assertIn(
            "# 示例-0.2\n第二行版本名\n\n"
            " 预览，标题：#标记 *重点* `代码`\n第二行 \n\n"
            "-  变更（一）：[链接](url)、A_B | C > D\n第二行  \n",
            out,
        )

        # show 结果证明多行变更仍是一条记录（只有一个 “- ” 前缀）。
        code, shown, err = show(self.db, self.VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(shown, self.EXPECTED_SHOW)


class TestInvalidPrefixArguments(ChangelogTestCase):
    """缺值、空串、纯空白或重复 --prefix 一律 Invalid draft，先于读库。"""

    INVALID_CASES = [
        ("缺值（--prefix 位于末尾）", ["--prefix"]),
        ("空字符串", ["--prefix", ""]),
        ("纯空格", ["--prefix", "   "]),
        ("制表符与换行组成的空白", ["--prefix", " \t\n "]),
        ("重复提供且值相同", ["--prefix", "v1", "--prefix", "v1"]),
        ("重复提供且值不同", ["--prefix", "v1", "--prefix", "v2"]),
    ]

    def assert_invalid(self, db_path, extra_args):
        code, out, err = run_cli_bytes(db_path, "export-changelog", *extra_args)
        self.assertEqual(code, 1, "非法前缀退出码应为 1")
        self.assertEqual(out, b"", "非法前缀时标准输出应为零字节")
        self.assertEqual(err, "Invalid draft\n".encode("utf-8"))

    def test_invalid_arguments_against_populated_database(self):
        create(self.db, "v1", "首版", ["变更"])
        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                self.assert_invalid(self.db, extra_args)
                # 非法调用不得改动已有草稿。
                code, out, err = export_markdown(self.db, "v1")
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(out, "# v1\n\n首版\n\n- 变更\n")

    def test_invalid_arguments_take_precedence_over_missing_database(self):
        missing_db = os.path.join(self._tmpdir.name, "absent.sqlite")
        for label, extra_args in self.INVALID_CASES:
            with self.subTest(case=label):
                self.assertEqual(os.listdir(self._tmpdir.name), [])
                self.assert_invalid(missing_db, extra_args)
                self.assertFalse(
                    os.path.exists(missing_db),
                    f"{label} 后不得创建数据库文件",
                )
                self.assertEqual(
                    os.listdir(self._tmpdir.name), [], f"{label} 后不得新增文件"
                )


class TestExportChangelogEmptyResults(ChangelogTestCase):
    """无匹配、缺库、无 drafts 表、空表：退出 0，两个流均为零字节。"""

    def assert_silent_success(self, db_path, *extra_args):
        code, out, err = run_cli_bytes(
            db_path, "export-changelog", *extra_args
        )
        self.assertEqual(code, 0, "有效输入的空结果退出码应为 0")
        self.assertEqual(out, b"", "空结果标准输出应为零字节（连换行也没有）")
        self.assertEqual(err, b"", "空结果标准错误应为零字节")

    def test_valid_prefix_with_no_match(self):
        create(self.db, "v2", "第二版", ["v2 变更"])
        self.assert_silent_success(self.db, "--prefix", "不存在")
        self.assert_silent_success(self.db, "--prefix", "v1")

    def test_missing_database_is_not_created(self):
        missing_db = os.path.join(self._tmpdir.name, "absent.sqlite")
        self.assert_silent_success(missing_db)
        self.assert_silent_success(missing_db, "--prefix", "v1")
        self.assertFalse(os.path.exists(missing_db), "汇总不得创建数据库文件")
        self.assertEqual(os.listdir(self._tmpdir.name), [])

    def test_sqlite_database_without_drafts_table_is_not_altered(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.execute("INSERT INTO unrelated VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        files_before = sorted(os.listdir(self._tmpdir.name))

        self.assert_silent_success(self.db)
        self.assert_silent_success(self.db, "--prefix", "v1")

        conn = sqlite3.connect(self.db)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
            untouched = conn.execute(
                "SELECT x FROM unrelated"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(tables, ["unrelated"], "汇总不得补建 drafts 表")
        self.assertEqual(untouched, [(1,)], "汇总不得改动其他表")
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)), files_before, "不得新增文件"
        )

    def test_drafts_table_without_rows(self):
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

        self.assert_silent_success(self.db)
        self.assert_silent_success(self.db, "--prefix", "v1")

        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0, "汇总不得向空草稿表写入任何行")


class TestExportChangelogReadOnlyAndStable(ChangelogTestCase):
    """汇总只读不改：草稿原样、无新文件，两个独立进程结果完全一致。"""

    def test_drafts_unchanged_files_unchanged_two_processes_identical(self):
        create(self.db, "v1", "首版", ["新增查询", "新增查询"])
        create(self.db, "v2", "次版", ["修正文案"])
        create(self.db, "other", "不匹配版本", ["其他变更"])

        files_before = sorted(os.listdir(self._tmpdir.name))
        v1_show_before = show(self.db, "v1")
        v2_show_before = show(self.db, "v2")

        # 省略 --prefix 与带 --prefix 各跑两个独立进程，结果逐字节一致。
        runs = [
            tuple(run_cli_bytes(self.db, "export-changelog")),
            tuple(run_cli_bytes(self.db, "export-changelog")),
            tuple(run_cli_bytes(self.db, "export-changelog", "--prefix", "v")),
            tuple(run_cli_bytes(self.db, "export-changelog", "--prefix", "v")),
        ]
        self.assertEqual(runs[0], runs[1], "两次全量汇总应逐字节相同")
        self.assertEqual(runs[2], runs[3], "两次前缀汇总应逐字节相同")
        for code, out, err in runs:
            self.assertEqual(code, 0)
            self.assertEqual(err, b"")
            self.assertTrue(out)

        # 汇总后已有草稿的名称、标题、条目数量与顺序完全不变。
        self.assertEqual(show(self.db, "v1"), v1_show_before)
        self.assertEqual(show(self.db, "v2"), v2_show_before)
        code, listing, err = list_drafts(self.db)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(listing),
            [
                {"version": "other", "title": "不匹配版本"},
                {"version": "v1", "title": "首版"},
                {"version": "v2", "title": "次版"},
            ],
        )
        # 不生成任何输出文件或数据库附属文件。
        self.assertEqual(
            sorted(os.listdir(self._tmpdir.name)),
            files_before,
            "汇总导出不得新增任何文件",
        )


if __name__ == "__main__":
    unittest.main()
