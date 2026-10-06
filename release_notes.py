#!/usr/bin/env python3
"""版本发布说明管理台：基于本地 SQLite 的最小草稿功能。

支持七个操作：
  create          创建版本草稿（标题 + 至少一条变更）
  show            按版本名精确查看草稿
  set-title       只修订已有草稿的标题
  add-change      向已有草稿追加一条变更
  set-change      按展示顺序替换已有草稿的一条变更
  remove-change   按展示顺序删除已有草稿的一条变更
  export-markdown 按固定 Markdown 格式把单个草稿导出到标准输出

不提供版本重命名、发布或 Git 相关功能；Markdown 导出只写到标准输出，
命令本身不接收输出路径，也不创建发布说明文件。
"""

import argparse
import os
import re
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


def read_draft(db_path, version):
    """按版本名原文精确读取草稿，供 show 与 export-markdown 共用。

    只读操作，不创建数据库：数据库文件不存在或库内没有该版本（含仅
    大小写不同）时返回 None；否则返回 (标题原文, 变更原文列表)，变更
    按展示顺序（position 升序）读取，不去重、不裁剪、不转义，多行
    变更仍是一条记录。
    """
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT title FROM drafts WHERE version = ?", (version,)
        ).fetchone()
        if row is None:
            return None
        title = row[0]

        changes = [
            content
            for (content,) in conn.execute(
                "SELECT content FROM changes WHERE version = ?"
                " ORDER BY position",
                (version,),
            )
        ]
    finally:
        conn.close()

    return title, changes


def cmd_show(args):
    draft = read_draft(args.db, args.version)
    if draft is None:
        return fail(f"Version not found: {args.version}")
    title, changes = draft

    # 原样输出；多行变更只在首行前加 "- "，内部换行保留。
    lines = [f"Version: {args.version}", f"Title: {title}"]
    lines.extend(f"- {content}" for content in changes)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_export_markdown(args):
    # 版本名为空或仅由空白时优先报 Invalid draft，即使数据库文件不存在
    # 也是如此。校验发生在访问数据库之前，失败时不会新建数据库文件。
    if is_blank(args.version):
        return fail("Invalid draft")

    draft = read_draft(args.db, args.version)
    if draft is None:
        return fail(f"Version not found: {args.version}")
    title, changes = draft

    # 固定格式逐字拼接，全程不转义、不整理空白：
    # "# " + 版本名原文 + 两个换行；标题原文 + 两个换行；
    # 每条变更按 show 的展示顺序加 "- " 前缀和一个换行，多行条目只在
    # 首行前加前缀，内部换行与首尾空格原样保留。
    parts = [f"# {args.version}\n\n", f"{title}\n\n"]
    parts.extend(f"- {content}\n" for content in changes)
    sys.stdout.write("".join(parts))
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


def parse_change_index(indexes):
    """校验并规范化 --index 参数，供 set-change 与 remove-change 共用。

    仅允许一个 --index：未提供或重复提供（多于一个）都视为无效。序号只
    接受数字 0-9 组成且数值大于 0 的字符串，前导零不影响定位。合法时
    返回去掉前导零的数字串（输出与错误消息中的序号同样不保留前导零）；
    无效时返回 None。保留去零后的数字串以便与条目总数按位数比较，避免
    极大序号触发整数转换或 SQLite 绑定上限。
    """
    if len(indexes) != 1 or not re.fullmatch(r"0*[1-9][0-9]*", indexes[0]):
        return None
    return indexes[0].lstrip("0")


def open_existing_draft(db_path, version):
    """不创建数据库地打开已有草稿，供 set-change 与 remove-change 共用。

    数据库文件不存在或库内没有该版本（含仅大小写不同）时返回 None；
    否则返回已打开的连接，由调用方负责关闭。
    """
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    exists = conn.execute(
        "SELECT 1 FROM drafts WHERE version = ?", (version,)
    ).fetchone()
    if exists is None:
        conn.close()
        return None
    return conn


def locate_change(conn, version, index_text):
    """按展示顺序（position 升序）定位第 N 条记录，多行条目只占一个序号。

    返回 (条目总数, 目标记录的 position)；序号越界时目标为 None。先与
    条目总数按十进制位数比较判定越界，无需把大序号转换成整数。
    """
    count = conn.execute(
        "SELECT COUNT(*) FROM changes WHERE version = ?",
        (version,),
    ).fetchone()[0]
    count_text = str(count)
    in_range = (
        len(index_text) < len(count_text)
        or (
            len(index_text) == len(count_text)
            and index_text <= count_text
        )
    )
    if not in_range:
        return count, None

    row = conn.execute(
        "SELECT position FROM changes WHERE version = ?"
        " ORDER BY position LIMIT 1 OFFSET ?",
        (version, int(index_text) - 1),
    ).fetchone()
    return count, row[0]


def cmd_set_change(args):
    # 仅允许一条 --change 与一个 --index：未提供（None）或重复提供
    # （多于一条）都视为无效草稿。版本名、新文本非空白。校验全部发生在
    # 访问数据库之前，即使版本不存在也优先报 Invalid draft，失败时不会
    # 新建数据库文件。
    changes = args.change or []
    index_text = parse_change_index(args.index or [])
    if (
        is_blank(args.version)
        or len(changes) != 1
        or is_blank(changes[0])
        or index_text is None
    ):
        return fail("Invalid draft")
    change = changes[0]

    # set-change 不创建数据库：文件或版本不存在即报版本不存在。
    conn = open_existing_draft(args.db, args.version)
    if conn is None:
        return fail(f"Version not found: {args.version}")
    try:
        with conn:
            count, target_position = locate_change(conn, args.version, index_text)
            if target_position is None:
                return fail(
                    f"Change not found: {args.version} #{index_text}"
                )

            # 单条 UPDATE 天然幂等：新文本与原文相同也成功，条目的数量、
            # 排列与其他字段均不改变。
            conn.execute(
                "UPDATE changes SET content = ?"
                " WHERE version = ? AND position = ?",
                (change, args.version, target_position),
            )
    finally:
        conn.close()

    print(f"Updated change: {args.version} #{index_text}")
    return 0


def cmd_remove_change(args):
    # 仅允许一个 --index。版本名非空白。校验全部发生在访问数据库之前，
    # 即使版本不存在也优先报 Invalid draft，失败时不会新建数据库文件。
    index_text = parse_change_index(args.index or [])
    if is_blank(args.version) or index_text is None:
        return fail("Invalid draft")

    # remove-change 不创建数据库：文件或版本不存在即报版本不存在。
    conn = open_existing_draft(args.db, args.version)
    if conn is None:
        return fail(f"Version not found: {args.version}")
    try:
        with conn:
            count, target_position = locate_change(conn, args.version, index_text)
            if target_position is None:
                return fail(
                    f"Change not found: {args.version} #{index_text}"
                )

            # 草稿至少保留一条变更：只剩一条且请求删除这一条时拒绝，
            # 该记录原样保留。
            if count == 1:
                return fail(f"Cannot remove last change: {args.version}")

            # 只删除指定的一条记录；其余条目的原文与相对顺序不变，相同
            # 文本的其他记录仍保留。剩余条目的 position 不重排：展示顺序
            # 由 ORDER BY position 决定，删除后的序号按剩余记录重新计算，
            # 追加仍落在末尾（MAX(position) + 1）。
            conn.execute(
                "DELETE FROM changes WHERE version = ? AND position = ?",
                (args.version, target_position),
            )
    finally:
        conn.close()

    print(f"Removed change: {args.version} #{index_text}")
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

    parser_export_markdown = subparsers.add_parser(
        "export-markdown", help="按固定 Markdown 格式导出单个草稿到标准输出"
    )
    parser_export_markdown.add_argument(
        "version", help="要导出的版本名，按原文精确匹配，区分大小写"
    )
    parser_export_markdown.set_defaults(func=cmd_export_markdown)

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
        "set-change", help="按展示顺序替换已有草稿的一条变更"
    )
    parser_set_change.add_argument(
        "version", help="版本名，按原文精确匹配，区分大小写"
    )
    parser_set_change.add_argument(
        "--index",
        action="append",
        metavar="N",
        help="变更序号（必填且仅允许一个），从 1 开始按记录计数；"
        "只接受数字且数值大于 0，前导零不影响定位",
    )
    parser_set_change.add_argument(
        "--change",
        action="append",
        metavar="TEXT",
        help="新变更文本（必填且仅允许一条），按原文原样保存",
    )
    parser_set_change.set_defaults(func=cmd_set_change)

    parser_remove_change = subparsers.add_parser(
        "remove-change", help="按展示顺序删除已有草稿的一条变更"
    )
    parser_remove_change.add_argument(
        "version", help="版本名，按原文精确匹配，区分大小写"
    )
    parser_remove_change.add_argument(
        "--index",
        action="append",
        metavar="N",
        help="变更序号（必填且仅允许一个），从 1 开始按记录计数；"
        "只接受数字且数值大于 0，前导零不影响定位",
    )
    parser_remove_change.set_defaults(func=cmd_remove_change)

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
