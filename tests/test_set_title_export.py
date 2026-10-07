"""release_notes.py set-title 修订标题后再 export-markdown 发布说明的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，期望文本按 README 固定格式
独立写明，不从实际输出反推；另以 SQLite 只读查询交叉核对草稿原文、条目
数量与顺序等既有数据格式语义）：
  - 成功路径：在固定样例草稿（版本 demo-0.1，标题 " 原始标题 "，三条变更
    依次为 "重复条目"、"重复条目"、" 第一行\\n第二行 "）上，通过
    set-title demo-0.1 --title " 修订#标题\\n第二行 " 在同一数据库路径
    修订标题：退出码 0、标准错误为空、标准输出恰为
    "Updated title: demo-0.1" 加一个换行；随后在新进程执行
    export-markdown demo-0.1，退出码 0、标准错误为空，标准输出按固定
    格式逐字独立写明——先 "# demo-0.1" 与两个换行，再是新标题原文与两个
    换行，最后按原顺序为三条变更各加 "- " 前缀和一个换行：重复记录分别
    保留，多行变更只在首行带前缀，中文、井号、首尾空格与内部换行均不裁剪、
    不转义；再次导出逐字相同，且导出只读、不产生任何发布说明文件。
  - 失败路径：新标题为空字符串或仅由空格、换行、制表符组成时，修订退出码
    为 1、标准输出为空、标准错误恰为 "Invalid draft" 加一个换行；失败后
    在新进程导出的文本与初始标题和三条变更对应的固定 Markdown 完全一致，
    不出现新标题、不丢失条目；失败修订及随后导出前后，草稿标题原文、变更
    条目数量（3 条）与展示顺序保持不变。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，结束后清理临时数据库，重复执行结果一致。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 demo-0.1；标题首尾各一个空格；三条变更依次为两条重复
# 文本，以及由首行带前导空格的 " 第一行" 与末行带尾随空格的 "第二行 "
# 通过恰好一个换行连接的多行文本。
VERSION = "demo-0.1"
ORIGINAL_TITLE = " 原始标题 "
CHANGES = ["重复条目", "重复条目", " 第一行\n第二行 "]

# 新标题：由带前导空格的 " 修订#标题" 与带尾随空格的 "第二行 " 通过恰好
# 一个换行连接；首尾空格、井号与内部换行都是输入的一部分，必须原样保存。
NEW_TITLE = " 修订#标题\n第二行 "

# 修订前的 show 期望文本（按 show 格式独立写明）：
#   "Version: <版本名>"、"Title: <标题原文>"、每条变更 "- " + 原文；
#   标题与第三条变更都自带前导空格，故冒号 / "- " 之后呈现两个空格；
#   多行标题与多行变更的第二行都不带任何前缀。
EXPECTED_SHOW_ORIGINAL = (
    "Version: demo-0.1\n"
    "Title:  原始标题 \n"
    "- 重复条目\n"
    "- 重复条目\n"
    "-  第一行\n"
    "第二行 \n"
)

# 成功修订后的 show 期望文本：仅标题变为新标题原文（两行、首尾空格与井号
# 保留），三条变更的原文、数量与顺序逐字不变。
EXPECTED_SHOW_UPDATED = (
    "Version: demo-0.1\n"
    "Title:  修订#标题\n"
    "第二行 \n"
    "- 重复条目\n"
    "- 重复条目\n"
    "-  第一行\n"
    "第二行 \n"
)

# 修订前的导出文本（按 README 固定格式独立写明）：
#   "# " + 版本名原文 + 两个换行；初始标题原文 + 两个换行；
#   每条变更 "- " + 原文 + 一个换行，多行条目只在首行带前缀。
EXPECTED_EXPORT_ORIGINAL = (
    "# demo-0.1\n"
    "\n"
    " 原始标题 \n"
    "\n"
    "- 重复条目\n"
    "- 重复条目\n"
    "-  第一行\n"
    "第二行 \n"
)

# 成功修订后的导出文本（独立写明，而非由程序输出推导）：
#   头部仍为 "# demo-0.1" 与两个换行；
#   标题段为新标题原文 " 修订#标题\n第二行 " 与两个换行——首行前导空格、
#   井号、内部换行与末行尾随空格全部保留；
#   变更段与修订前完全一致：两条重复记录分别保留，第三条多行文本只在首行
#   带 "- " 前缀（其原文自带前导空格，故前缀后呈现两个空格），末行
#   "第二行 " 的尾随空格后只接格式规定的一个换行。
EXPECTED_EXPORT_UPDATED = (
    "# demo-0.1\n"
    "\n"
    " 修订#标题\n"
    "第二行 \n"
    "\n"
    "- 重复条目\n"
    "- 重复条目\n"
    "-  第一行\n"
    "第二行 \n"
)

# 空字符串，以及仅由空格、换行、制表符组成的若干新标题。
BLANK_TITLES = ["", "   ", "\n", "\t", " \n\t "]


def run_cli(db_path, *args):
    """在独立子进程中执行 release_notes.py，返回 (退出码, stdout, stderr)。

    输出按字节捕获后以 UTF-8 解码，保证中文、井号与首尾空格精确可比。
    每次调用都是一个全新进程。
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


def set_title(db_path, version, title):
    return run_cli(db_path, "set-title", version, "--title", title)


def show(db_path, version):
    return run_cli(db_path, "show", version)


def export_markdown(db_path, version):
    return run_cli(db_path, "export-markdown", version)


def read_draft_raw(db_path, version):
    """只读核对既有 SQLite 数据格式。

    返回 (标题原文, 按 position 升序的变更原文列表, 变更记录行数)。
    记录行数独立计数，用于证明两条相同文本是不同的数据库记录而非去重
    后的一行；本函数不写库、不改变任何数据。
    """
    conn = sqlite3.connect(db_path)
    try:
        title_row = conn.execute(
            "SELECT title FROM drafts WHERE version = ?", (version,)
        ).fetchone()
        contents = [
            content
            for (content,) in conn.execute(
                "SELECT content FROM changes WHERE version = ?"
                " ORDER BY position",
                (version,),
            )
        ]
        row_count = conn.execute(
            "SELECT COUNT(*) FROM changes WHERE version = ?", (version,)
        ).fetchone()[0]
    finally:
        conn.close()
    return title_row[0], contents, row_count


class SetTitleExportTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_demo_01(self):
        """按固定样例创建 demo-0.1 并断言创建过程本身符合预期。"""
        code, out, err = create(self.db, VERSION, ORIGINAL_TITLE, CHANGES)
        self.assertEqual(code, 0, "create demo-0.1 退出码应为 0")
        self.assertEqual(out, "Created demo-0.1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def assert_draft_raw_unchanged(self, expected_title):
        """标题原文、变更条目数量（3 条）与顺序均与固定样例一致。"""
        title, contents, row_count = read_draft_raw(self.db, VERSION)
        self.assertEqual(title, expected_title, "草稿标题原文不符")
        self.assertEqual(row_count, 3, "变更记录行数应为 3")
        self.assertEqual(len(contents), 3, "变更条目数量应为 3")
        self.assertEqual(contents, CHANGES, "变更原文与顺序必须保持不变")
        # 显式核对两条重复记录分别保留（库内是两行而非去重后的一行）。
        self.assertEqual(contents[0], contents[1], "前两条文本应相同")
        self.assertEqual(contents.count("重复条目"), 2, "两条重复条目均应保留")


class TestSetTitleThenExportSuccess(SetTitleExportTestCase):
    def test_retitle_then_export_matches_fixed_format_and_is_read_only(self):
        self.create_demo_01()

        # 修订前先固定初始状态：show 文本与库内原文均符合独立写明的期望。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_ORIGINAL, ""))
        self.assert_draft_raw_unchanged(ORIGINAL_TITLE)

        # 即 python release_notes.py --db notes.sqlite \
        #   set-title demo-0.1 --title " 修订#标题\n第二行 "
        # 在同一数据库路径上修订；首尾空格、井号与内部换行都是输入的一部分。
        code, out, err = set_title(self.db, VERSION, NEW_TITLE)
        self.assertEqual(code, 0, "set-title 退出码应为 0")
        self.assertEqual(err, "", "set-title 标准错误应为空")
        self.assertEqual(
            out,
            "Updated title: demo-0.1\n",
            "set-title 标准输出应恰为 Updated title: demo-0.1 加一个换行",
        )

        # 修订只改标题：新标题原文（含两行、首尾空格与井号）已落库，
        # 变更原文、数量与顺序不变。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_SHOW_UPDATED,
            "新标题应原样保存，变更的原文、数量与顺序不变",
        )
        title, contents, row_count = read_draft_raw(self.db, VERSION)
        self.assertEqual(title, NEW_TITLE, "新标题应按输入原文保存")
        self.assertEqual(row_count, 3, "修订标题不得改变变更记录行数")
        self.assertEqual(contents, CHANGES, "修订标题不得改动任何变更")

        # 记录导出前的目录文件集合；导出不得新增发布说明文件或附属文件。
        files_before = set(os.listdir(self._tmpdir.name))

        # 随后在新进程导出：退出码 0、标准错误为空，输出与独立写明的固定
        # Markdown 逐字一致。
        code, out, err = export_markdown(self.db, VERSION)
        self.assertEqual(code, 0, "export-markdown 退出码应为 0")
        self.assertEqual(err, "", "export-markdown 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_EXPORT_UPDATED,
            "导出文本应精确匹配固定格式：新标题的首尾空格、井号与内部换行"
            "原样保留；两条重复条目分别出现；多行变更只在首行带前缀；"
            "中文、空格与内部换行不裁剪、不转义",
        )

        # 再次在另一个新进程导出，逐字相同（含末尾换行）。
        code2, out2, err2 = export_markdown(self.db, VERSION)
        self.assertEqual(code2, 0, "再次导出退出码应为 0")
        self.assertEqual(err2, "", "再次导出标准错误应为空")
        self.assertEqual(out2, out, "两次独立进程导出结果应逐字相同")
        self.assertEqual(out2, EXPECTED_EXPORT_UPDATED, "再次导出仍应匹配固定文本")

        # 导出为只读操作：目录内不新增任何文件（不产生发布说明文件），
        # 草稿标题原文、条目数量与顺序在导出前后保持一致。
        self.assertEqual(
            set(os.listdir(self._tmpdir.name)),
            files_before,
            "导出不得新增发布说明文件或其他附属文件",
        )
        self.assertEqual(
            read_draft_raw(self.db, VERSION),
            (NEW_TITLE, CHANGES, 3),
            "导出前后草稿原文、条目数量与顺序应保持一致",
        )


class TestBlankTitleRejectedLeavesExportUnchanged(SetTitleExportTestCase):
    """失败路径：空白新标题被拒，固定样例草稿与导出文本逐字不变。"""

    def assert_invalid_draft(self, result, label):
        code, out, err = result
        self.assertEqual(code, 1, f"[{label}] set-title 退出码应为 1")
        self.assertEqual(out, "", f"[{label}] 失败时标准输出应为空")
        self.assertEqual(
            err,
            "Invalid draft\n",
            f"[{label}] 标准错误应恰为 Invalid draft 加一个换行",
        )

    def test_blank_titles_rejected_and_export_stays_original(self):
        self.create_demo_01()

        # 失败路径开始前先固定初始状态。
        code, initial_out, initial_err = export_markdown(self.db, VERSION)
        self.assertEqual((code, initial_err), (0, ""))
        self.assertEqual(
            initial_out,
            EXPECTED_EXPORT_ORIGINAL,
            "样例草稿的初始导出应与独立写明的固定 Markdown 一致",
        )

        for index, blank_title in enumerate(BLANK_TITLES):
            with self.subTest(blank_title=repr(blank_title)):
                label = f"blank-{index}"
                self.assert_draft_raw_unchanged(ORIGINAL_TITLE)

                # 空白新标题必须被拒绝，且不写入任何内容。
                self.assert_invalid_draft(
                    set_title(self.db, VERSION, blank_title), label
                )

                # 失败后在新进程导出：退出码 0、标准错误为空，输出与初始
                # 标题和三条变更对应的固定 Markdown 完全一致——不出现新
                # 标题、不丢失条目，而不是与某次实际输出做比较。
                code, out, err = export_markdown(self.db, VERSION)
                self.assertEqual(code, 0, f"[{label}] 失败后导出退出码应为 0")
                self.assertEqual(err, "", f"[{label}] 失败后导出标准错误应为空")
                self.assertEqual(
                    out,
                    EXPECTED_EXPORT_ORIGINAL,
                    f"[{label}] 失败修订不得改变导出文本",
                )

                # show 与库内原文同样保持初始状态：标题仍是初始标题原文，
                # 变更仍为 3 条且顺序、重复记录不变。
                code, out, err = show(self.db, VERSION)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(
                    out,
                    EXPECTED_SHOW_ORIGINAL,
                    f"[{label}] 失败修订后 show 结果应与初始状态逐字一致",
                )
                self.assert_draft_raw_unchanged(ORIGINAL_TITLE)

                # 导出不得产生任何发布说明文件或附属文件。
                self.assertEqual(
                    sorted(os.listdir(self._tmpdir.name)),
                    [os.path.basename(self.db)],
                    f"[{label}] 整个流程结束后目录内应只有数据库文件",
                )


if __name__ == "__main__":
    unittest.main()
