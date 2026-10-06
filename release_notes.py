#!/usr/bin/env python3
"""版本发布说明管理台：创建并查看版本草稿。

仅使用 Python 3 标准库与本地 SQLite，支持两个子命令：

    python release_notes.py --db notes.sqlite create <版本名> \
        --title <标题> --change <变更> [--change <变更> ...]
    python release_notes.py --db notes.sqlite show <版本名>
"""

import argparse
import sqlite3
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    version TEXT PRIMARY KEY,
    title   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    version  TEXT NOT NULL REFERENCES versions(version),
    position INTEGER NOT NULL,
    text     TEXT NOT NULL,
    PRIMARY KEY (version, position)
);
"""


def fail(message):
    print(message, file=sys.stderr)
    return 1


def cmd_create(db_path, version, title, changes):
    # 先校验，再触碰数据库：无效输入不应建立文件或留下部分草稿。
    if not version.strip() or not title.strip():
        return fail("Invalid draft")
    if not changes or any(not c.strip() for c in changes):
        return fail("Invalid draft")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        with conn:  # 事务：任一失败整体回滚，不留部分草稿
            exists = conn.execute(
                "SELECT 1 FROM versions WHERE version = ?", (version,)
            ).fetchone()
            if exists:
                return fail(f"Version already exists: {version}")
            conn.execute(
                "INSERT INTO versions (version, title) VALUES (?, ?)",
                (version, title),
            )
            conn.executemany(
                "INSERT INTO changes (version, position, text) VALUES (?, ?, ?)",
                [(version, i, text) for i, text in enumerate(changes)],
            )
    finally:
        conn.close()

    print(f"Created {version}")
    return 0


def cmd_show(db_path, version):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT title FROM versions WHERE version = ?", (version,)
        ).fetchone()
        if row is None:
            return fail(f"Version not found: {version}")
        changes = conn.execute(
            "SELECT text FROM changes WHERE version = ? ORDER BY position",
            (version,),
        ).fetchall()
    finally:
        conn.close()

    print(f"Version: {version}")
    print(f"Title: {row[0]}")
    for (text,) in changes:
        print(f"- {text}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="release_notes.py", description="版本发布说明管理台"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="创建版本草稿")
    p_create.add_argument("version", help="版本名（精确匹配，区分大小写）")
    p_create.add_argument("--title", required=True, help="草稿标题")
    p_create.add_argument(
        "--change",
        action="append",
        default=[],
        help="变更条目，可重复，按输入顺序保存",
    )

    p_show = sub.add_parser("show", help="查看版本草稿")
    p_show.add_argument("version", help="要查看的版本名")

    args = parser.parse_args(argv)

    if args.command == "create":
        return cmd_create(args.db, args.version, args.title, args.change)
    return cmd_show(args.db, args.version)


if __name__ == "__main__":
    sys.exit(main())
