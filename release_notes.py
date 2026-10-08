#!/usr/bin/env python3
"""版本发布说明管理台：基于本地 SQLite 的最小草稿功能。

支持十个操作：
  create          创建版本草稿（标题 + 至少一条变更）
  show            按版本名精确查看草稿
  list-drafts     列出全部已保存草稿的版本名与标题
  set-title       只修订已有草稿的标题
  add-change      向已有草稿追加一条变更
  set-change      按展示顺序替换已有草稿的一条变更
  remove-change   按展示顺序删除已有草稿的一条变更
  move-change     按展示顺序移动已有草稿的一条变更
  rename-version  重命名已有草稿的版本名（标题与变更随版本名一起保留）
  export-markdown 按固定 Markdown 格式把单个草稿导出到标准输出

不提供发布或 Git 相关功能；Markdown 导出只写到标准输出，
命令本身不接收输出路径，也不创建发布说明文件。
"""

import argparse
import json
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


def drafts_table_exists(conn):
    """连接内是否存在 drafts 表。

    show / export-markdown / list-drafts 三条只读入口共用的唯一前置表
    检查：只读 sqlite_master，不执行任何建表语句、不开启写事务；文件
    存在但没有 drafts 表（完全空库或只有无关表）时返回 False，由调用方
    按空目录或版本不存在处理，不补建 drafts/changes。
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master"
        " WHERE type = 'table' AND name = 'drafts'"
    ).fetchone() is not None


def open_draft_reader(db_path):
    """打开只读草稿访问连接，集中三条只读入口共用的前置检查。

    路径不存在，或文件存在但没有 drafts 表（完全空库或只有无关表）时
    返回 None：目录查询据此输出空数组，单版本查询据此报版本不存在。
    成功时返回已打开的连接，由调用方负责关闭。全程只读：不创建数据库
    文件、不补建 drafts/changes、不改动任何已有表与数据；此流程的查询
    只触及 sqlite_master 与 drafts/changes 的 SELECT，不会产生写事务。
    """
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    if not drafts_table_exists(conn):
        conn.close()
        return None
    return conn


def read_draft(db_path, version):
    """按版本名原文精确读取单个草稿，供 show 与 export-markdown 共用。

    路径存在性、连接与 drafts 表存在性等前置检查统一走
    open_draft_reader：任一不满足即返回 None；库内没有该版本（含仅大小
    写不同）时同样返回 None。成功时返回 (标题原文, 变更原文列表)，变更
    按展示顺序（position 升序）读取，不去重、不裁剪、不转义，多行变更
    仍是一条记录。
    """
    conn = open_draft_reader(db_path)
    if conn is None:
        return None
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


def list_draft_items(conn, prefix=None):
    """在已确认含 drafts 表的连接上读取目录，仅供 list-drafts 使用。

    只查 drafts 的 version 与 title，不读取也不要求 changes 表。prefix
    非 None 时在 Python 侧按版本名原文逐字符筛选：区分大小写、不裁剪
    空白、不解析语义版本，中文与内部换行按原文比较，% 与 _ 是普通字符
    （不经 SQL LIKE 通配），标题与变更内容不参与匹配。结果按版本名原文
    逐字符（Unicode 码点）升序排列，前缀相同时较短名称在前；元素仅含
    version 与 title 两个字段。
    """
    rows = conn.execute("SELECT version, title FROM drafts").fetchall()
    if prefix is not None:
        rows = [row for row in rows if row[0].startswith(prefix)]
    rows.sort(key=lambda row: row[0])
    return [
        {"version": version, "title": title}
        for version, title in rows
    ]


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


def cmd_list_drafts(args):
    # --prefix 仅允许一次且值为非空白文本：省略（None）时保留全部目录
    # 行为；显式提供但缺值（nargs='?' 落空时取空串常量）、值为空或仅含
    # 空白、重复提供都视为无效草稿。校验先于任何数据库访问，即使数据库
    # 文件不存在也优先报 Invalid draft，且不创建数据库或补建表。
    prefixes = args.prefix
    if prefixes is not None:
        if len(prefixes) != 1 or is_blank(prefixes[0]):
            return fail("Invalid draft")
        prefix = prefixes[0]
    else:
        prefix = None

    # 只读目录查询：路径不存在或文件存在但没有 drafts 表时，
    # open_draft_reader 统一返回 None，按空目录输出 []；整个过程不连接
    # 建库、不补建表，也不读取或要求 changes 表。
    items = []
    conn = open_draft_reader(args.db)
    if conn is not None:
        try:
            items = list_draft_items(conn, prefix)
        finally:
            conn.close()

    # ensure_ascii=False：中文等非 ASCII 字符直接输出；引号、反斜杠与
    # 控制字符仍由 json 按规则转义。末尾恰有一个换行，无其他提示语。
    sys.stdout.write(json.dumps(items, ensure_ascii=False) + "\n")
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

    # 文件存在性、drafts 表存在性与版本存在性检查复用 set-title /
    # add-change / set-change / remove-change / move-change 共用的草稿
    # 访问流程：不创建数据库、不补建表、不改动原有表与数据，失败信息已
    # 写入 stderr。
    conn, error = open_draft_for_update(args.db, args.version)
    if conn is None:
        return error
    try:
        # 单条 UPDATE 天然幂等：标题相同则匹配但不改变任何内容，
        # 不会新增草稿或变更条目。版本存在性已由共用流程确认，这里的
        # rowcount == 0 只作为并发删除等极端情形的兜底。本命令不访问
        # changes 表：库中只有合法 drafts 表而没有 changes 表时修订同样
        # 成功，也不会补建 changes 表。
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


def validate_change_index(indexes):
    """校验 set-change / remove-change 共用的 --index 列表。

    仅允许一个 --index：未提供（None）或重复提供（多于一个）都视为无效。
    序号只接受数字 0-9 组成且数值大于 0 的字符串，前导零不影响定位。
    有效时返回去前导零后的数字串（输出与错误消息中的序号同样不保留
    前导零）；无效时返回 None，由调用方报 Invalid draft。保留去零后的
    数字串以便与条目总数按位数比较，避免极大序号触发整数转换或
    SQLite 绑定上限。
    """
    if len(indexes) != 1:
        return None
    if not re.fullmatch(r"0*[1-9][0-9]*", indexes[0]):
        return None
    return indexes[0].lstrip("0")


def open_draft_for_update(db_path, version):
    """为 set-title / add-change / set-change / remove-change / move-change /
    rename-version 打开已有数据库并确认版本存在。

    不创建数据库、不补建表：文件不存在，或文件存在但没有 drafts 表
    （完全空库或只有无关表）即视为版本不存在。成功时返回
    (连接, None)，调用方负责关闭连接；失败时返回 (None, 退出码)，
    错误信息已写入标准错误。
    """
    if not os.path.exists(db_path):
        return None, fail(f"Version not found: {version}")

    conn = sqlite3.connect(db_path)
    # 不执行任何建表语句：文件存在但没有 drafts 表时（完全空库或只有
    # 无关表）与路径不存在等价，统一按版本不存在处理，不补建
    # drafts/changes、不插入草稿或变更，也不改动原有表结构与数据。此
    # 查询只读 sqlite_master，不会开启写事务或产生 journal。
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master"
        " WHERE type = 'table' AND name = 'drafts'"
    ).fetchone()
    if has_table is None:
        conn.close()
        return None, fail(f"Version not found: {version}")

    # 先确认草稿存在，再交由调用方定位条目或计算追加位置，避免给不
    # 存在的版本补建条目或误报、改动其他版本的数据。
    exists = conn.execute(
        "SELECT 1 FROM drafts WHERE version = ?", (version,)
    ).fetchone()
    if exists is None:
        conn.close()
        return None, fail(f"Version not found: {version}")
    return conn, None


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

    # 文件存在性、drafts 表存在性与版本存在性检查复用 set-title /
    # add-change / set-change / remove-change / move-change 共用的草稿
    # 访问流程：不创建数据库、不补建表、不改动原有表与数据，失败信息已
    # 写入 stderr。
    conn, error = open_draft_for_update(args.db, args.version)
    if conn is None:
        return error
    try:
        with conn:
            # 新条目排在全部现存条目之后：position 取该版本现存记录的
            # MAX(position) + 1（无现存记录时为 0）。删除中间或末尾条目
            # 会留下 position 空洞，但 MAX 只随现存记录变化，追加结果仍
            # 是末尾；展示顺序一律由读取处的 ORDER BY position 决定。
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM changes"
                " WHERE version = ?",
                (args.version,),
            ).fetchone()
            next_position = row[0] + 1
            # 只插入一条独立记录：内容按原文保存，不去重、不裁剪空白、
            # 不转义，相同文本再次提交仍产生独立的新记录；标题、旧条目
            # 的原文与相对顺序、其他版本均不受影响。
            conn.execute(
                "INSERT INTO changes(version, position, content)"
                " VALUES (?, ?, ?)",
                (args.version, next_position, change),
            )
    finally:
        conn.close()

    print(f"Added change: {args.version}")
    return 0


def locate_change(conn, version, index_text):
    """按展示顺序（position 升序）定位第 N 条记录，多行条目只占一个序号。

    返回 (条目总数, 目标记录的 position)；序号越界时返回 (条目总数, None)，
    由调用方报 Change not found。先与条目总数按十进制位数比较判定越界，
    无需把大序号转换成整数。
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
    index_text = validate_change_index(args.index or [])
    if (
        is_blank(args.version)
        or len(changes) != 1
        or is_blank(changes[0])
        or index_text is None
    ):
        return fail("Invalid draft")
    change = changes[0]

    conn, error = open_draft_for_update(args.db, args.version)
    if conn is None:
        return error
    try:
        with conn:
            _, target_position = locate_change(conn, args.version, index_text)
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
    # 仅允许一个 --index：未提供（None）或重复提供（多于一个）都视为无效草稿。
    # 版本名非空白。校验全部发生在访问数据库之前，即使版本不存在也优先报
    # Invalid draft，失败时不会新建数据库文件。
    index_text = validate_change_index(args.index or [])
    if is_blank(args.version) or index_text is None:
        return fail("Invalid draft")

    conn, error = open_draft_for_update(args.db, args.version)
    if conn is None:
        return error
    try:
        with conn:
            count, target_position = locate_change(conn, args.version, index_text)
            if target_position is None:
                return fail(
                    f"Change not found: {args.version} #{index_text}"
                )

            # 草稿至少保留一条变更：只剩一条且请求删除这一条时拒绝，
            # 该记录原样保留。越界已在上面优先报条目不存在。
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


def cmd_move_change(args):
    # --from 与 --to 各只允许一个：未提供（None）或重复提供（多于一个）都
    # 视为无效草稿。版本名非空白，两个序号均只接受数字且数值大于 0，前导零
    # 不影响定位。校验全部发生在访问数据库之前，即使版本不存在也优先报
    # Invalid draft，失败时不会新建数据库文件。
    from_text = validate_change_index(args.from_index or [])
    to_text = validate_change_index(args.to_index or [])
    if is_blank(args.version) or from_text is None or to_text is None:
        return fail("Invalid draft")

    conn, error = open_draft_for_update(args.db, args.version)
    if conn is None:
        return error
    try:
        with conn:
            # 版本存在后先检查来源再检查目标：两者都越界时报告来源。
            _, from_position = locate_change(conn, args.version, from_text)
            if from_position is None:
                return fail(
                    f"Change not found: {args.version} #{from_text}"
                )
            _, to_position = locate_change(conn, args.version, to_text)
            if to_position is None:
                return fail(
                    f"Change not found: {args.version} #{to_text}"
                )

            # 按展示顺序取出全部记录，在 Python 侧重排：来源条目取出后插入
            # 目标序号处，目标表示最终位置（不因来源在前而减一），其余条目
            # 的相对顺序不变；来源与目标相同则顺序保持不变。条目原文、数量
            # 与其他版本均不受影响，重复文本的各条记录仍分别保留。
            ids = [
                row_id
                for (row_id,) in conn.execute(
                    "SELECT id FROM changes WHERE version = ?"
                    " ORDER BY position",
                    (args.version,),
                )
            ]
            moved_id = ids.pop(int(from_text) - 1)
            ids.insert(int(to_text) - 1, moved_id)
            conn.executemany(
                "UPDATE changes SET position = ? WHERE id = ?",
                list(enumerate(ids)),
            )
    finally:
        conn.close()

    print(f"Moved change: {args.version} #{from_text} -> #{to_text}")
    return 0


def cmd_rename_version(args):
    # 仅允许一个 --to：未提供（None）、显式给出却缺值（nargs='?' 落空时
    # 取空串常量）或重复提供（多于一个）都视为无效草稿。原版本名与新
    # 版本名均非空白。校验全部发生在访问数据库之前，即使数据库文件或
    # 原版本不存在也优先报 Invalid draft，失败时不会新建数据库文件。
    to_names = args.to
    if (
        is_blank(args.version)
        or to_names is None
        or len(to_names) != 1
        or is_blank(to_names[0])
    ):
        return fail("Invalid draft")
    new_version = to_names[0]

    # 文件存在性、drafts 表存在性与原版本存在性检查复用 set-title /
    # add-change / set-change / remove-change / move-change / rename-version
    # 共用的草稿访问流程：不创建数据库、不补建表、不改动原有表与数据，
    # 失败信息已写入 stderr。原版本存在性在此确认，因此下面只需检查新名
    # 称是否被另一草稿占用。
    conn, error = open_draft_for_update(args.db, args.version)
    if conn is None:
        return error
    try:
        try:
            with conn:
                # 原名与新名完全相同（按原文逐字符比较，区分大小写）时
                # 按成功处理：不更新、不新增任何记录。
                if new_version != args.version:
                    conflict = conn.execute(
                        "SELECT 1 FROM drafts WHERE version = ?",
                        (new_version,),
                    ).fetchone()
                    if conflict is not None:
                        return fail(f"Version already exists: {new_version}")

                    # 同一事务内更新草稿行与全部变更行的版本名：标题、
                    # 变更原文、条目数量与展示顺序（position）原样保留，
                    # 重复文本的各条记录仍分别保留，其他版本不受影响。
                    # 新名称按原文原样保存，不裁剪空白、不转义。
                    conn.execute(
                        "UPDATE drafts SET version = ? WHERE version = ?",
                        (new_version, args.version),
                    )
                    conn.execute(
                        "UPDATE changes SET version = ? WHERE version = ?",
                        (new_version, args.version),
                    )
        except sqlite3.IntegrityError:
            # 兜底：并发进程抢先创建同名版本时，事务回滚不留部分重命名。
            return fail(f"Version already exists: {new_version}")
    finally:
        conn.close()

    print(f"Renamed version: {args.version} -> {new_version}")
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

    parser_list_drafts = subparsers.add_parser(
        "list-drafts", help="列出全部已保存草稿的版本名与标题"
    )
    # action="append" 使重复提供可被计数（多于一条即无效）；nargs='?' 配合
    # const="" 让显式给出却缺值的 --prefix 以空串进入处理函数而非触发
    # argparse 自带报错，从而统一报 Invalid draft（退出码 1）。值不设
    # default，省略时为 None，表示不筛选、保留全部目录行为。
    parser_list_drafts.add_argument(
        "--prefix",
        action="append",
        nargs="?",
        const="",
        metavar="TEXT",
        help="可选版本名前缀：仅返回版本名原文以该文本开头的草稿，区分"
        "大小写、不裁剪空白，%% 与 _ 为普通字符；省略则列出全部草稿",
    )
    parser_list_drafts.set_defaults(func=cmd_list_drafts)

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

    parser_move_change = subparsers.add_parser(
        "move-change", help="按展示顺序移动已有草稿的一条变更"
    )
    parser_move_change.add_argument(
        "version", help="版本名，按原文精确匹配，区分大小写"
    )
    parser_move_change.add_argument(
        "--from",
        dest="from_index",
        action="append",
        metavar="N",
        help="来源序号（必填且仅允许一个），从 1 开始按记录计数；"
        "只接受数字且数值大于 0，前导零不影响定位",
    )
    parser_move_change.add_argument(
        "--to",
        dest="to_index",
        action="append",
        metavar="N",
        help="目标序号（必填且仅允许一个），表示移动后的最终位置，"
        "计数规则与来源序号相同",
    )
    parser_move_change.set_defaults(func=cmd_move_change)

    parser_rename_version = subparsers.add_parser(
        "rename-version", help="重命名已有草稿的版本名，标题与变更随版本名保留"
    )
    parser_rename_version.add_argument(
        "version", help="原版本名，按原文精确匹配，区分大小写"
    )
    # action="append" 使重复提供可被计数（多于一个即无效）；nargs='?' 配合
    # const="" 让显式给出却缺值的 --to 以空串进入处理函数而非触发 argparse
    # 自带报错，从而统一报 Invalid draft（退出码 1）。值不设 default，省略
    # 时为 None，同样视为无效。
    parser_rename_version.add_argument(
        "--to",
        action="append",
        nargs="?",
        const="",
        metavar="NAME",
        help="新版本名（必填且仅允许一个），按原文原样保存，不裁剪空白",
    )
    parser_rename_version.set_defaults(func=cmd_rename_version)

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
