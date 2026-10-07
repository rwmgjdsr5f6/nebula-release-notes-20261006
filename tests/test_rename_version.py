"""release_notes.py rename-version 的离线回归测试。

覆盖范围（仅通过命令行与外部可观察行为验收，不依赖内部表结构）：
  - 成功路径：在固定样例草稿（v1，标题"预览版"，三条变更依次为
    新增预览、新增预览、以及一条由"修正提示"和"保留原文"两行组成的
    多行变更）上执行 rename-version v1 --to v2；退出码 0、标准错误为
    空、标准输出恰为 "Renamed version: v1 -> v2" 加换行；随后 show v2
    显示新版本名、原标题与三条原文（重复条目分别保留、多行文本仍是
    一条记录、顺序不变），show v1 报 Version not found。新名称含中文
    与首尾空格时按原文保存、不裁剪，大小写仍按原文区分；同名重命名
    也成功，草稿内容与数量不变。
  - 失败路径：目标名称已被其他版本占用报 Version already exists，两份
    草稿都保留原名、标题与条目；数据库文件不存在或原版本不存在报
    Version not found（原版本不存在而目标已存在时仍报告原版本不存在）；
    原版本名为空 / 仅空白，或 --to 未提供、缺值、为空、仅空白、重复
    提供均报 Invalid draft，且优先于数据库与版本存在性检查。所有失败
    退出码 1、标准输出为空、错误末尾带换行，不创建数据库，已有草稿
    不发生修改。

所有期望文本均按 README 固定格式独立写明，不从实际输出反推；比较完整
UTF-8 文本而非片段。

运行方式（项目根目录）：
    python -m unittest discover -s tests

只依赖 Python 3 标准库；每个测试使用独立的临时目录与 SQLite 路径，
不读取或改写用户数据库，不联网，重复执行结果一致。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PROJECT_ROOT, "release_notes.py")

# 固定样例：版本 v1，标题"预览版"，三条变更依次为两条重复文本和一条
# 由"修正提示"与"保留原文"两行组成的多行变更（多行文本只占一条记录）。
VERSION = "v1"
TITLE = "预览版"
CHANGES = ["新增预览", "新增预览", "修正提示\n保留原文"]

# 重命名为 v2 后的期望 show 输出（按 README 固定格式独立写明）：
# 新版本名、原标题与三条原文，重复条目分别保留，多行文本仍是一条记录，
# 顺序不变。
EXPECTED_SHOW_V2 = (
    "Version: v2\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修正提示\n"
    "保留原文\n"
)

# 冲突样例：v9 与 v1 的标题、条目均不同，用于验证冲突失败时互不影响。
CONFLICT_VERSION = "v9"
CONFLICT_TITLE = "正式版"
CONFLICT_CHANGES = ["首个变更", "修正说明"]

EXPECTED_SHOW_V1 = (
    "Version: v1\n"
    "Title: 预览版\n"
    "- 新增预览\n"
    "- 新增预览\n"
    "- 修正提示\n"
    "保留原文\n"
)

EXPECTED_SHOW_V9 = (
    "Version: v9\n"
    "Title: 正式版\n"
    "- 首个变更\n"
    "- 修正说明\n"
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


def rename_version(db_path, version, *rename_args):
    return run_cli(db_path, "rename-version", version, *rename_args)


class RenameVersionTestCase(unittest.TestCase):
    """每个测试使用独立的临时目录，互不干扰、可重复执行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="release-notes-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.db = os.path.join(self._tmpdir.name, "notes.sqlite")

    def create_fixture(self):
        """按固定样例创建 v1（标题"预览版"，三条变更）。"""
        code, out, err = create(self.db, VERSION, TITLE, CHANGES)
        self.assertEqual(code, 0, "create v1 退出码应为 0")
        self.assertEqual(out, "Created v1\n", "create 标准输出不符")
        self.assertEqual(err, "", "create 标准错误应为空")

    def create_conflict_fixtures(self):
        """创建 v1 与标题、条目均不同的 v9，用于冲突与保留验证。"""
        self.create_fixture()
        code, out, err = create(
            self.db, CONFLICT_VERSION, CONFLICT_TITLE, CONFLICT_CHANGES
        )
        self.assertEqual(code, 0, "create v9 退出码应为 0")
        self.assertEqual(out, "Created v9\n", "create v9 标准输出不符")
        self.assertEqual(err, "", "create v9 标准错误应为空")


class TestRenameVersionSuccess(RenameVersionTestCase):
    def test_rename_then_show_new_name_and_old_name(self):
        self.create_fixture()

        # 即 python release_notes.py --db notes.sqlite rename-version v1 --to v2
        code, out, err = rename_version(self.db, VERSION, "--to", "v2")
        self.assertEqual(code, 0, "rename-version 退出码应为 0")
        self.assertEqual(err, "", "rename-version 标准错误应为空")
        self.assertEqual(
            out,
            "Renamed version: v1 -> v2\n",
            "rename-version 标准输出应恰为 Renamed version: v1 -> v2"
            " 加末尾换行",
        )

        # 两个相互独立的进程查看新名称，结果应逐字相同（含末尾换行）。
        first = show(self.db, "v2")
        second = show(self.db, "v2")
        self.assertEqual(first, second, "两次独立进程查看结果应逐字相同")
        code, out, err = first
        self.assertEqual(code, 0, "show v2 退出码应为 0")
        self.assertEqual(err, "", "show v2 标准错误应为空")
        self.assertEqual(
            out,
            EXPECTED_SHOW_V2,
            "show v2 应显示新版本名、原标题与三条原文：重复条目分别保留，"
            "多行文本仍是一条记录，顺序不变",
        )

        # 原名称不再存在。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 1, "show v1 退出码应为 1")
        self.assertEqual(out, "", "show v1 标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "show v1 标准错误应恰为 Version not found: v1 加末尾换行",
        )

    def test_rename_to_name_with_cjk_and_surrounding_spaces(self):
        self.create_fixture()

        # 新名称含中文与首尾空格：按原文原样保存，不裁剪、不转义。
        new_name = "  预览版 v2 "
        code, out, err = rename_version(self.db, VERSION, "--to", new_name)
        self.assertEqual(code, 0, "rename-version 退出码应为 0")
        self.assertEqual(err, "", "rename-version 标准错误应为空")
        self.assertEqual(
            out,
            "Renamed version: v1 ->   预览版 v2 \n",
            "成功消息中的新名称应按原文输出，首尾空格不裁剪",
        )

        code, out, err = show(self.db, new_name)
        self.assertEqual(code, 0, "show 新名称退出码应为 0")
        self.assertEqual(err, "", "show 新名称标准错误应为空")
        self.assertEqual(
            out,
            "Version:   预览版 v2 \n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 修正提示\n"
            "保留原文\n",
            "新名称应含首尾空格按原文保存，标题与三条变更原样保留",
        )

        # 裁剪后的名称不是同一版本：首尾空格参与精确匹配。
        code, out, err = show(self.db, "预览版 v2")
        self.assertEqual(code, 1, "裁剪后的名称不应命中")
        self.assertEqual(out, "")
        self.assertEqual(err, "Version not found: 预览版 v2\n")

    def test_rename_is_case_sensitive(self):
        self.create_fixture()

        # 大小写按原文区分：V1 与 v1 是不同名称，改名后两者各归各位。
        code, out, err = rename_version(self.db, VERSION, "--to", "V2")
        self.assertEqual(code, 0, "rename-version 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(out, "Renamed version: v1 -> V2\n")

        code, out, err = show(self.db, "v2")
        self.assertEqual(code, 1, "仅大小写不同的名称不应命中")
        self.assertEqual(out, "")
        self.assertEqual(err, "Version not found: v2\n")

        code, out, err = show(self.db, "V2")
        self.assertEqual(code, 0, "show V2 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "Version: V2\n"
            "Title: 预览版\n"
            "- 新增预览\n"
            "- 新增预览\n"
            "- 修正提示\n"
            "保留原文\n",
            "V2 应显示原标题与三条原文",
        )

    def test_rename_to_same_name_succeeds_and_keeps_content(self):
        self.create_fixture()

        code, out, err = rename_version(self.db, VERSION, "--to", "v1")
        self.assertEqual(code, 0, "同名重命名退出码应为 0")
        self.assertEqual(err, "", "同名重命名标准错误应为空")
        self.assertEqual(
            out,
            "Renamed version: v1 -> v1\n",
            "同名重命名应输出对应的成功消息",
        )

        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show v1 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            EXPECTED_SHOW_V1,
            "同名重命名后草稿内容与数量应保持不变",
        )


class TestRenameVersionConflict(RenameVersionTestCase):
    def test_rename_to_existing_version_keeps_both_drafts(self):
        self.create_conflict_fixtures()

        code, out, err = rename_version(self.db, VERSION, "--to", "v9")
        self.assertEqual(code, 1, "目标已存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version already exists: v9\n",
            "标准错误应恰为 Version already exists: v9 加末尾换行",
        )

        # 两份草稿都保留原名、标题与条目。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0, "show v1 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持原样")

        code, out, err = show(self.db, CONFLICT_VERSION)
        self.assertEqual(code, 0, "show v9 退出码应为 0")
        self.assertEqual(err, "")
        self.assertEqual(out, EXPECTED_SHOW_V9, "v9 草稿应保持原样")

    def test_missing_source_with_existing_target_reports_source(self):
        self.create_conflict_fixtures()

        # 原版本不存在而目标已存在时，仍报告原版本不存在。
        code, out, err = rename_version(self.db, "v8", "--to", "v9")
        self.assertEqual(code, 1, "原版本不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v8\n",
            "原版本不存在而目标已存在时仍应报告原版本不存在",
        )

        # 已有两份草稿均不发生修改。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持原样")
        code, out, err = show(self.db, CONFLICT_VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V9, "v9 草稿应保持原样")


class TestRenameVersionNotFound(RenameVersionTestCase):
    def test_missing_database_file(self):
        code, out, err = rename_version(self.db, VERSION, "--to", "v2")
        self.assertEqual(code, 1, "数据库文件不存在时退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err,
            "Version not found: v1\n",
            "标准错误应恰为 Version not found: v1 加末尾换行",
        )
        self.assertFalse(
            os.path.exists(self.db), "rename-version 不得创建数据库文件"
        )

    def test_missing_version(self):
        self.create_fixture()
        # 仅大小写不同的名称同样视为不存在。
        code, out, err = rename_version(self.db, "V1", "--to", "v2")
        self.assertEqual(code, 1, "版本不存在（含仅大小写不同）退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Version not found: V1\n")

        # 已有草稿不发生修改。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持原样")


class TestRenameVersionInvalidInput(RenameVersionTestCase):
    """输入校验优先于数据库与版本存在性检查：无效输入一律 Invalid draft。"""

    def assert_invalid(self, *rename_args, version=VERSION):
        code, out, err = rename_version(self.db, version, *rename_args)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(
            err, "Invalid draft\n", "标准错误应恰为 Invalid draft 加末尾换行"
        )
        self.assertFalse(
            os.path.exists(self.db), "无效输入不得创建数据库文件"
        )

    def test_missing_to_option(self):
        self.assert_invalid()

    def test_to_without_value(self):
        # 显式给出 --to 却缺值（nargs='?' 落空取空串常量）。
        self.assert_invalid("--to")

    def test_blank_to_values(self):
        self.assert_invalid("--to", "")
        self.assert_invalid("--to", "  \n\t ")

    def test_duplicate_to_options(self):
        self.assert_invalid("--to", "v2", "--to", "v3")

    def test_blank_version(self):
        self.assert_invalid("--to", "v2", version="")
        self.assert_invalid("--to", "v2", version="  \n\t ")

    def test_invalid_input_precedes_existing_database(self):
        # 即使数据库与版本都存在，无效输入仍优先报 Invalid draft。
        self.create_fixture()
        code, out, err = rename_version(self.db, VERSION)
        self.assertEqual(code, 1, "无效输入退出码应为 1")
        self.assertEqual(out, "", "失败时标准输出应为空")
        self.assertEqual(err, "Invalid draft\n")

        # 已有草稿不发生修改。
        code, out, err = show(self.db, VERSION)
        self.assertEqual(code, 0)
        self.assertEqual(out, EXPECTED_SHOW_V1, "v1 草稿应保持原样")


if __name__ == "__main__":
    unittest.main()
