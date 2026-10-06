#!/usr/bin/env python3
"""版本发布说明管理台：基于本地 SQLite 的最小草稿功能。

仅支持三个操作：
  create    创建版本草稿（标题 + 至少一条变更）
  set-title 修改已有草稿的标题
  show      按版本名精确查看草稿

除此之外不提供编辑、分类、发布、导出或 Git 相关功能。
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


def cmd_set_title(args):
    # 校验先于数据库访问：版本名或新标题为空白（含未提供）时，
    # 即使目标版本不存在也优先报 Invalid draft，且不新建数据库文件。
    if (
        is_blank(args.version)
        or args.title is None
        or is_blank(args.title)
    ):
        return fail("Invalid draft")

    # set-title 不创建数据库：文件不存在即视为版本不存在。
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

            # 标题按输入原样写入；与原标题相同也视为成功，不触碰变更条目。
            conn.execute(
                "UPDATE drafts SET title = ? WHERE version = ?",
                (args.title, args.version),
            )
    finally:
        conn.close()

    print(f"Updated title: {args.version}")
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

    parser_set_title = subparsers.add_parser("set-title", help="修改已有草稿的标题")
    parser_set_title.add_argument("version", help="要修改标题的版本名")
    parser_set_title.add_argument("--title", help="新标题（必填）")
    parser_set_title.set_defaults(func=cmd_set_title)

    parser_show = subparsers.add_parser("show", help="按版本名查看草稿")
    parser_show.add_argument("version", help="要查看的版本名")
    parser_show.set_defaults(func=cmd_show)

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
