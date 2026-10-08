"""release_notes.py copy-version 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（v1，标题"预览"，三条变更依次为甲、重复、
    重复）上执行 copy-version v1 --to v2；退出码 0、标准错误为空、标准
    输出恰为 "Copied version: v1 -> v2" 加换行；随后另一次命令行调用
    show v2 显示新版本名、原标题与三条原文（重复条目分别保留，顺序不
    变），show v1 仍显示完整原草稿——复制保留原版本。再次在新进程读取
    结果完全一致。新名称含中文与首尾空格时按原文保存、不裁剪，大小写仍
    按原文区分。
  - 当前内容复制：源草稿删除、移动过条目后再复制，新草稿以源草稿当前
    展示顺序为准，position 空洞不带入新草稿。
  - 互相独立：复制完成后用 set-title / set-change 修改任一份草稿，另一
    份的标题与条目不受影响。
  - 目录与导出：新草稿可经 list-drafts（含 --prefix）与 export-markdown /
    export-changelog 读取，沿用原有排序、前缀筛选与输出格式。
  - 冲突路径：新名称已被占用报 Version already exists，两份草稿均保持
    原样；原名与新名完全相同也属于已占用（与 rename-version 的同名成功
    不同），报 Version already exists 且不新增草稿。
  - 未找到路径：数据库文件不存在、库中没有 drafts 表或查无原版本（含仅
    大小写不同）报 Version not found；原版本不存在而新名已存在时仍报告
    原版本不存在。
  - 无效输入：原版本名为空 / 仅空白，或 --to 未提供、缺值、为空、仅
    空白、重复提供，均报 Invalid draft，且优先于数据库或版本存在性
    检查。所有失败退出码 1、标准输出为空、错误末尾带换行，不存在的数据
    库不会被创建，不留下新草稿或部分条目，已有草稿不发生修改。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推；比较完整
UTF-8 文本而非片段。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，重复执行结果一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 v1，标题"预览"，三条变更依次为甲与两条重复文本。
VERSION = "v1"
TITLE = "预览"
CHANGES = ["甲", "重复", "重复"]

# 复制为 v2 后的期望 show 输出（按 README 固定格式独立写明）：新版本名、
# 原标题、三条原文，重复条目分别保留、顺序不变。
EXPECTED_SHOW_V1 = (
    "Version: v1\n"
    "Title: 预览\n"
    "- 甲\n"
    "- 重复\n"
    "- 重复\n"
)

EXPECTED_SHOW_V2 = (
    "Version: v2\n"
    "Title: 预览\n"
    "- 甲\n"
    "- 重复\n"
    "- 重复\n"
)

# 新名样例的导出全文（按 README 固定 Markdown 格式独立写明）。
EXPECTED_EXPORT_V2 = (
    "# v2\n"
    "\n"
    "预览\n"
    "\n"
    "- 甲\n"
    "- 重复\n"
    "- 重复\n"
)

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
        """按固定样例创建 v1。"""
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

        # 另一次命令行调用查看新名称：新版本名、原标题、三条原文按原顺序
        # 输出，重复条目分别保留。
        code, out, err = show(self.db, "v2")
        self.assertEqual(code, 0, "show v2 退出码应为 0")
        self.assertEqual(err, "", "show v2 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_V2,
            "复制后新草稿应显示新版本名、原标题与同序三条变更",
        )

        # 再开一个全新进程读取，结果完全一致。
        code, out, err = show(self.db, "v2")
        self.assertEqual(code, 0, "再次 show v2 退出码应为 0")
        self.assertEqual(err, "", "再次 show v2 标准错误应为空")
        self.assertEqual(out, EXPECTED_SHOW_V2, "新进程读取结果应完全一致")

        # 原草稿仍然完整存在——复制保留原版本。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show v1 退出码应为 0")
        self.assertEqual(err, "", "show v1 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_V1,
            "原草稿在复制后应保持完整：标题、三条原文与顺序均不变",
        )

    def test_copy_to_name_with_cjk_and_surrounding_spaces(self):
        self.create_main_fixture()

        # 新名称含中文与首尾空格：成功消息与草稿名都按原文保留，不裁剪。
        new_name = " V2 副本 "
        code, out, err = copy_version(self.db, VERSION, "--to", new_name)
        self.assertEqual(code, 0, "copy-version 退出码应为 0")
        self.assertEqual(err, "", "copy-version 标准错误应为空")
        self.assertEqual(
            out,
            "Copied version: v1 ->  V2 副本 \n",
            "成功消息中的新名称应保留首尾空格，不裁剪",
        )

        # 按原文精确查看成功：首尾空格是名称的一部分，内容随名称一起复制。
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
            "含中文与首尾空格的新名称应按原文保存，内容为原草稿的副本",
        )

        # 裁剪掉首尾空格的名称不是同一版本。
        code, out, err = show(self.db, "V2 副本")
        self.assertEqual(code, 1, "裁剪后的名称不应命中")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: V2 副本\n")

        # 大小写仍按原文区分：仅大小写不同的名称不命中。
        code, out, err = show(self.db, " v2 副本 ")
        self.assertEqual(code, 1, "仅大小写不同的名称不应命中")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found:  v2 副本 \n")

        # 原草稿仍在。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V1, ""))

    def test_copy_preserves_multiline_and_blank_padding_verbatim(self):
        # 标题首尾空格与内部换行、多行变更、纯空白填充的变更文本均原样复制。
        title = " 标题，首行\n次行 "
        changes = ["第一行\n第二行", "  两侧空格  ", "重复", "重复"]
        code, out, err = create(self.db, "m1", title, changes)
        self.assertEqual((code, err), (0, ""))

        code, out, err = copy_version(self.db, "m1", "--to", "m2")
        self.assertEqual((code, out, err), (0, "Copied version: m1 -> m2\n", ""))

        expected = (
            "Version: m2\n"
            f"Title: {title}\n"
            f"- {changes[0]}\n"
            f"- {changes[1]}\n"
            f"- {changes[2]}\n"
            f"- {changes[3]}\n"
        )
        code, out, err = show(self.db, "m2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            expected,
            "多行标题、多行变更与首尾空格应逐字复制，重复文本分别保留",
        )


class TestCopyUsesCurrentContent(CopyVersionTestCase):
    def test_copy_after_remove_and_move_uses_current_display_order(self):
        self.create_main_fixture()

        # 删除第二条（甲、重复、重复 -> 甲、重复），再把第一条移动到末尾
        # （甲、重复 -> 重复、甲）。
        code, _, err = run_cli(self.db, "remove-change", VERSION, "--index", "2")
        self.assertEqual((code, err), (0, ""))
        code, _, err = run_cli(
            self.db, "move-change", VERSION, "--from", "1", "--to", "2"
        )
        self.assertEqual((code, err), (0, ""))

        expected_current = (
            "Version: v1\n"
            "Title: 预览\n"
            "- 重复\n"
            "- 甲\n"
        )
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, expected_current, ""))

        # 复制以当前展示顺序为准。
        code, out, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, out, err), (0, "Copied version: v1 -> v2\n", ""))

        code, out, err = show(self.db, "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v2\n"
            "Title: 预览\n"
            "- 重复\n"
            "- 甲\n",
            "复制已删除或移动过条目的草稿时，应以当前展示顺序为准",
        )


class TestCopiedDraftsAreIndependent(CopyVersionTestCase):
    def test_retitle_source_does_not_change_copy(self):
        self.create_main_fixture()
        code, _, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 修改原草稿标题，副本标题不变。
        code, out, err = run_cli(
            self.db, "set-title", VERSION, "--title", "修订后的标题"
        )
        self.assertEqual((code, out, err), (0, "Updated title: v1\n", ""))

        code, out, err = show(self.db, "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, EXPECTED_SHOW_V2, "原草稿改标题不得影响副本")

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v1\n"
            "Title: 修订后的标题\n"
            "- 甲\n"
            "- 重复\n"
            "- 重复\n",
            "原草稿标题应已更新，条目不变",
        )

    def test_change_copy_does_not_change_source(self):
        self.create_main_fixture()
        code, _, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 修改副本的一条变更，原草稿对应条目不变。
        code, out, err = run_cli(
            self.db, "set-change", "v2", "--index", "1", "--change", "副本改甲"
        )
        self.assertEqual((code, out, err), (0, "Updated change: v2 #1\n", ""))

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V1, ""))

        code, out, err = show(self.db, "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v2\n"
            "Title: 预览\n"
            "- 副本改甲\n"
            "- 重复\n"
            "- 重复\n",
            "副本条目修改不得影响原草稿",
        )

    def test_appending_to_one_draft_does_not_affect_the_other(self):
        self.create_main_fixture()
        code, _, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 向原草稿追加条目，副本数量不变；再向副本追加，原草稿也不变。
        code, _, err = run_cli(
            self.db, "add-change", VERSION, "--change", "原草稿新增"
        )
        self.assertEqual((code, err), (0, ""))
        code, _, err = run_cli(
            self.db, "add-change", "v2", "--change", "副本新增"
        )
        self.assertEqual((code, err), (0, ""))

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v1\n"
            "Title: 预览\n"
            "- 甲\n"
            "- 重复\n"
            "- 重复\n"
            "- 原草稿新增\n",
        )
        code, out, err = show(self.db, "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "Version: v2\n"
            "Title: 预览\n"
            "- 甲\n"
            "- 重复\n"
            "- 重复\n"
            "- 副本新增\n",
            "两份草稿的条目列表应互相独立",
        )


class TestCopyVisibleInCatalogAndExports(CopyVersionTestCase):
    def test_copy_listed_in_directory_with_sorting_and_prefix(self):
        self.create_main_fixture()
        code, _, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 目录查询可见，按版本名 Unicode 码点升序排列。
        code, out, err = run_cli(self.db, "list-drafts")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(out),
            [
                {"version": "v1", "title": "预览"},
                {"version": "v2", "title": "预览"},
            ],
            "复制出的新草稿应出现在目录中，排序沿用既有规则",
        )
        self.assertTrue(out.endswith("\n") and out.count("\n") == 1)

        # 前缀筛选沿用既有规则。
        code, out, err = run_cli(self.db, "list-drafts", "--prefix", "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(out),
            [{"version": "v2", "title": "预览"}],
            "--prefix 应只返回新草稿",
        )

    def test_copy_exportable_as_markdown_and_in_changelog(self):
        self.create_main_fixture()
        code, _, err = copy_version(self.db, VERSION, "--to", "v2")
        self.assertEqual((code, err), (0, ""))

        # 单份导出沿用固定格式与同一顺序。
        code, out, err = run_cli(self.db, "export-markdown", "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            EXPECTED_EXPORT_V2,
            "新草稿的 Markdown 导出应与 export-markdown 固定格式逐字一致",
        )

        # 汇总导出包含两份片段，按目录顺序排列，片段与单份导出逐字相同。
        code, out, err = run_cli(self.db, "export-changelog")
        self.assertEqual((code, err), (0, ""))
        expected_v1 = (
            "# v1\n"
            "\n"
            "预览\n"
            "\n"
            "- 甲\n"
            "- 重复\n"
            "- 重复\n"
        )
        self.assertEqual(
            out,
            expected_v1 + "\n" + EXPECTED_EXPORT_V2,
            "汇总导出应先输出 v1 片段再输出 v2 片段，间隔恰好一个换行",
        )

        # 前缀筛选只汇总新草稿，与单份导出逐字相同。
        code, out, err = run_cli(self.db, "export-changelog", "--prefix", "v2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out, EXPECTED_EXPORT_V2, "--prefix 汇总应只包含新草稿片段"
        )


class TestCopyVersionConflict(CopyVersionTestCase):
    def test_copy_to_existing_version_keeps_both_drafts(self):
        self.create_conflict_fixtures()

        code, out, err = copy_version(self.db, VERSION, "--to", "v9")
        self.assertEqual(code, 1, "新名称已存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v9\n",
            "标准错误应恰为 Version already exists: v9 加末尾换行",
        )

        # 两份草稿都保持原样。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V1, ""))
        code, out, err = show(self.db, CONFLICT_VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V9, ""))

    def test_copy_to_same_name_is_conflict(self):
        self.create_main_fixture()

        # 原名与新名相同属于新名已占用：与 rename-version 的同名成功不同，
        # copy-version 必须失败且不新增任何草稿。
        code, out, err = copy_version(self.db, VERSION, "--to", VERSION)
        self.assertEqual(code, 1, "同名复制退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v1\n",
            "原名与新名相同时标准错误应恰为 Version already exists: v1",
        )

        code, out, err = run_cli(self.db, "list-drafts")
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out),
            [{"version": "v1", "title": "预览"}],
            "同名复制失败后不得新增草稿，目录中仍只有原草稿",
        )
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V1, ""))

    def test_conflict_message_preserves_new_name_verbatim(self):
        self.create_main_fixture()
        # 先占用一个含中文与首尾空格的名称。
        occupied = " 占用 名 "
        code, _, err = create(self.db, occupied, "他题", ["他条"])
        self.assertEqual((code, err), (0, ""))

        code, out, err = copy_version(self.db, VERSION, "--to", occupied)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(
            err,
            "Version already exists:  占用 名 \n",
            "冲突消息中的新名称应保留中文与首尾空格",
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

    def test_missing_version(self):
        self.create_main_fixture()
        # 仅大小写不同的名称同样视为不存在。
        code, out, err = copy_version(self.db, "V1", "--to", "v2")
        self.assertEqual(code, 1, "原版本不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: V1\n")

        # 已有草稿不发生修改，也没有出现 v2。
        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V1, ""))
        code, out, err = run_cli(self.db, "list-drafts")
        self.assertEqual(
            json.loads(out), [{"version": "v1", "title": "预览"}]
        )

    def test_missing_source_with_existing_target_reports_source(self):
        # 只有 v9：原版本 v1 不存在而目标 v9 已存在时，仍报告原版本不存在。
        code, _, err = create(
            self.db, CONFLICT_VERSION, CONFLICT_TITLE, CONFLICT_CHANGES
        )
        self.assertEqual((code, err), (0, ""))

        code, out, err = copy_version(self.db, VERSION, "--to", "v9")
        self.assertEqual(code, 1, "原版本不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "原版本不存在而新名已存在时仍应报告原版本不存在",
        )

        # v9 草稿保持原样，没有产生副本或部分条目。
        code, out, err = show(self.db, CONFLICT_VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V9, ""))


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
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Invalid draft\n")

        code, out, err = show(self.db, VERSION)
        self.assertEqual((code, out, err), (0, EXPECTED_SHOW_V1, ""))


if __name__ == "__main__":
    unittest.main()
