#!/usr/bin/env python3
"""版本发布说明管理台：基于本地 SQLite 的最小草稿功能。

仅支持四个操作：
  create     创建版本草稿（标题 + 至少一条变更）
  show       按版本名精确查看草稿
  set-title  只修订已有草稿的标题
  add-change 向已有草稿追加一条变更
  set-change 按序号替换已有草稿的一条变更

不提供版本重命名、发布、导出或 Git 相关功能。
"""

import argparse
import os
import sqlite3
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    version TEXT PRIMARY KEY,
    title   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    version  TEXT NOT NULL,
    position INTEGER NOT NULL,
    content  TEXT NOT NULL,
    FOREIGN KEY (version) REFERENCES drafts(version)
);
"""


def is_blank(text):
    """空白判定：空字符串或仅由空白字符（含换行）组成。"""
    return text == "" or text.isspace()


def fail(message):
    """向标准错误输出失败信息，返回退出码 1。"""
    print(message, file=sys.stderr)
    return 1


def cmd_create(args):
    changes = args.change or []

    # 名称、标题非空白；至少一条变更，且每条变更都非空白。
    # 标题或变更未提供（None）同样视为无效草稿。
    # 校验发生在连接数据库之前，失败时不会新建数据库文件。
    if (
        is_blank(args.version)
        or args.title is None
        or is_blank(args.title)
        or not changes
        or any(is_blank(change) for change in changes)
    ):
        return fail("Invalid draft")

    conn = sqlite3.connect(args.db)
    try:
        conn.executescript(SCHEMA)
        try:
            with conn:
                exists = conn.execute(
                    "SELECT 1 FROM drafts WHERE version = ?", (args.version,)
                ).fetchone()
                if exists is not None:
                    return fail(f"Version already exists: {args.version}")

                conn.execute(
                    "INSERT INTO drafts(version, title) VALUES (?, ?)",
                    (args.version, args.title),
                )
                conn.executemany(
                    "INSERT INTO changes(version, position, content)"
                    " VALUES (?, ?, ?)",
                    [
                        (args.version, position, content)
                        for position, content in enumerate(changes)
                    ],
                )
        except sqlite3.IntegrityError:
            # 兜底：并发进程抢先创建同名版本时，事务回滚不留部分数据。
            return fail(f"Version already exists: {args.version}")
    finally:
        conn.close()

    print(f"Created {args.version}")
    return 0


def cmd_show(args):
    # show 不创建数据库：文件不存在即视为版本不存在。
    if not os.path.exists(args.db):
        return fail(f"Version not found: {args.version}")

    conn = sqlite3.connect(args.db)
    try:
        row = conn.execute(
            "SELECT title FROM drafts WHERE version = ?", (args.version,)
        ).fetchone()
        if row is None:
            return fail(f"Version not found: {args.version}")
        title = row[0]

        changes = [
            content
            for (content,) in conn.execute(
                "SELECT content FROM changes WHERE version = ?"
                " ORDER BY position",
                (args.version,),
            )
        ]
    finally:
        conn.close()

    # 原样输出；多行变更只在首行前加 "- "，内部换行保留。
    lines = [f"Version: {args.version}", f"Title: {title}"]
    lines.extend(f"- {content}" for content in changes)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_set_title(args):
    # 名称、标题非空白；标题未提供（None）同样视为无效草稿。
    # 校验发生在访问数据库之前，即使版本不存在也优先报 Invalid draft，
    # 失败时不会新建数据库文件。
    if is_blank(args.version) or args.title is None or is_blank(args.title):
        return fail("Invalid draft")

    # set-title 不创建数据库：文件不存在即视为版本不存在。
    if not os.path.exists(args.db):
        return fail(f"Version not found: {args.version}")

    conn = sqlite3.connect(args.db)
    try:
        # 单条 UPDATE 天然幂等：标题相同则匹配但不改变任何内容，
        # 不会新增草稿或变更条目；版本不存在时 rowcount 为 0。
        with conn:
            cursor = conn.execute(
                "UPDATE drafts SET title = ? WHERE version = ?",
                (args.title, args.version),
            )
            if cursor.rowcount == 0:
                return fail(f"Version not found: {args.version}")
    finally:
        conn.close()

    print(f"Updated title: {args.version}")
    return 0


def cmd_add_change(args):
    # 仅允许一条 --change：未提供（None）或重复提供（多于一条）都视为无效草稿。
    # 名称、追加文本非空白。校验发生在访问数据库之前，即使版本不存在也优先
    # 报 Invalid draft，失败时不会新建数据库文件。
    changes = args.change or []
    if (
        is_blank(args.version)
        or len(changes) != 1
        or is_blank(changes[0])
    ):
        return fail("Invalid draft")
    change = changes[0]

    # add-change 不创建数据库：文件不存在即视为版本不存在。
    if not os.path.exists(args.db):
        return fail(f"Version not found: {args.version}")

    conn = sqlite3.connect(args.db)
    try:
        with conn:
            # 先确认草稿存在，再计算新条目位置，避免给不存在的版本补建条目。
            exists = conn.execute(
                "SELECT 1 FROM drafts WHERE version = ?", (args.version,)
            ).fetchone()
            if exists is None:
                return fail(f"Version not found: {args.version}")

            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM changes"
                " WHERE version = ?",
                (args.version,),
            ).fetchone()
            next_position = row[0] + 1
            conn.execute(
                "INSERT INTO changes(version, position, content)"
                " VALUES (?, ?, ?)",
                (args.version, next_position, change),
            )
    finally:
        conn.close()

    print(f"Added change: {args.version}")
    return 0


def cmd_set_change(args):
    # --index、--change 各仅允许一条：未提供或重复提供都视为无效草稿。
    # 名称、新文本非空白；序号必须是纯数字（0-9）且数值大于 0，前导零允许。
    # 校验发生在访问数据库之前，即使版本不存在也优先报 Invalid draft，
    # 失败时不会新建数据库文件。
    indices = args.index or []
    changes = args.change or []
    if (
        is_blank(args.version)
        or len(indices) != 1
        or len(changes) != 1
        or is_blank(changes[0])
    ):
        return fail("Invalid draft")
    raw_index = indices[0]
    change = changes[0]
    if not raw_index.isdigit() or int(raw_index) <= 0:
        return fail("Invalid draft")
    index = int(raw_index)

    # set-change 不创建数据库：文件不存在即视为版本不存在。
    if not os.path.exists(args.db):
        return fail(f"Version not found: {args.version}")

    conn = sqlite3.connect(args.db)
    try:
        with conn:
            exists = conn.execute(
                "SELECT 1 FROM drafts WHERE version = ?", (args.version,)
            ).fetchone()
            if exists is None:
                return fail(f"Version not found: {args.version}")

            # 按展示顺序（position 升序）定位第 index 条，从 1 开始计数；
            # 多行条目仍只占一个序号。按行 id 更新，与 position 取值无关。
            ids = [
                row_id
                for (row_id,) in conn.execute(
                    "SELECT id FROM changes WHERE version = ?"
                    " ORDER BY position",
                    (args.version,),
                )
            ]
            if index > len(ids):
                return fail(f"Change not found: {args.version} #{index}")

            # 只替换目标条目的文本：条目数量、排列、版本名、标题及其他版本不变。
            conn.execute(
                "UPDATE changes SET content = ? WHERE id = ?",
                (change, ids[index - 1]),
            )
    finally:
        conn.close()

    print(f"Updated change: {args.version} #{index}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="版本发布说明管理台（本地 SQLite 草稿）"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_create = subparsers.add_parser("create", help="创建版本草稿")
    parser_create.add_argument("version", help="版本名，按原文精确匹配，区分大小写")
    parser_create.add_argument("--title", help="版本标题（必填）")
    parser_create.add_argument(
        "--change",
        action="append",
        metavar="TEXT",
        help="变更条目，可重复传入；按传入顺序保存",
    )
    parser_create.set_defaults(func=cmd_create)

    parser_show = subparsers.add_parser("show", help="按版本名查看草稿")
    parser_show.add_argument("version", help="要查看的版本名")
    parser_show.set_defaults(func=cmd_show)

    parser_set_title = subparsers.add_parser(
        "set-title", help="只修订已有草稿的标题"
    )
    parser_set_title.add_argument(
        "version", help="版本名，按原文精确匹配，区分大小写"
    )
    parser_set_title.add_argument("--title", help="新标题（必填），按原文原样保存")
    parser_set_title.set_defaults(func=cmd_set_title)

    parser_add_change = subparsers.add_parser(
        "add-change", help="向已有草稿追加一条变更"
    )
    parser_add_change.add_argument(
        "version", help="版本名，按原文精确匹配，区分大小写"
    )
    parser_add_change.add_argument(
        "--change",
        action="append",
        metavar="TEXT",
        help="要追加的变更文本（必填且仅允许一条），按原文原样保存",
    )
    parser_add_change.set_defaults(func=cmd_add_change)

    parser_set_change = subparsers.add_parser(
        "set-change", help="按序号替换已有草稿的一条变更"
    )
    parser_set_change.add_argument(
        "version", help="版本名，按原文精确匹配，区分大小写"
    )
    parser_set_change.add_argument(
        "--index",
        action="append",
        metavar="N",
        help="要替换的变更序号（必填且仅允许一条），从 1 开始按展示顺序计数",
    )
    parser_set_change.add_argument(
        "--change",
        action="append",
        metavar="TEXT",
        help="替换后的变更文本（必填且仅允许一条），按原文原样保存",
    )
    parser_set_change.set_defaults(func=cmd_set_change)

    return parser


def main(argv=None):
    # 中文、标点与首尾空格按 UTF-8 原样输出，不依赖运行环境的区域设置。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
