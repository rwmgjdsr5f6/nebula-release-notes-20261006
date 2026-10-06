# 草稿保存与按版本名查看：流程核对说明

本说明只描述当前源码实际实现的流程，证据基准为：

- `README.md`(公开说明)
- `release_notes.py`(唯一源码文件，共 158 行)
- 核对时提交:`f81ec3b`

行号均指 `release_notes.py`。本文不新增功能、不修改任何产品文件，未从源码读到的细节一律标为「未确认」。

---

## 1. 公开入口到代码的映射

README「入口」一节声明命令行入口为 `release_notes.py`，两个操作都通过 `--db` 指定同一个 SQLite 数据库文件。源码对应：

- 参数解析:`build_parser()`(122–144 行)。`--db` 为必填(126 行);子命令 `create`(129–138 行)与 `show`(140–142 行)分别绑定 `cmd_create` / `cmd_show`。
- 入口函数:`main()`(147–153 行),先把 stdout/stderr 重配置为 UTF-8(149–150 行),再解析参数并分派到 `args.func`。

流程总览:

```
命令行 → build_parser 解析 → cmd_create / cmd_show
create: 校验(48–55) → 连接+建表(57–59) → 查重(62–66) → 写入(68–79) → 输出(86)
show:   文件存在性(92–93) → 读标题(97–102) → 读变更(104–111) → 拼输出(115–118)
```

---

## 2. create:输入校验、来源与去向

### 2.1 各字段来源

- 版本名：位置参数 `version`(130 行),进入 `args.version`。
- 标题：选项 `--title`(131 行),进入 `args.title`;未提供时为 `None`。
- 变更条目：选项 `--change`,`action="append"`(132–137 行),按命令行出现顺序累积为列表 `args.change`;未提供时为 `None`,`cmd_create` 在 43 行将其归一为 `[]`。

### 2.2 校验(48–55 行)

在连接数据库**之前**执行，任一成立即 `return fail("Invalid draft")`:

- `is_blank(args.version)` — 版本名为空串或仅空白(`is_blank`,31–33 行：`text == "" or text.isspace()`);
- `args.title is None` — 未提供 `--title`;
- `is_blank(args.title)` — 标题为空/仅空白;
- `not changes` — 一条 `--change` 都没有;
- `any(is_blank(change) ...)` — 任一变更为空/仅空白。

`fail()`(36–39 行)向 stderr 打印消息并返回退出码 1。因为校验在 `sqlite3.connect`(57 行)之前，此路径不会创建数据库文件、不写任何数据——与 README「不会创建文件或留下部分草稿」一致。

注意:`is_blank` 用的是 Python `str.isspace()`,它覆盖 Unicode 空白(如制表符、换行、全角空格 U+3000 等),不止 ASCII 空格。README 只说「仅由空白组成」,实现口径以 `str.isspace()` 为准。

### 2.3 存储去向

- 57 行：`sqlite3.connect(args.db)` 连接 `--db` 指定路径。
- 59 行：`executescript(SCHEMA)` 建表(`SCHEMA`,16–28 行):
  - `drafts(version TEXT PRIMARY KEY, title TEXT NOT NULL)`;
  - `changes(id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL, position INTEGER NOT NULL, content TEXT NOT NULL, FOREIGN KEY(version) REFERENCES drafts(version))`。
- 62–66 行：在事务内先 `SELECT 1 FROM drafts WHERE version = ?` 查重，已存在则 `fail(f"Version already exists: {args.version}")`,退出码 1,不写任何行。
- 68–71 行：版本名、标题原样写入 `drafts`。
- 72–79 行：`enumerate(changes)` 生成从 0 开始的序号，`executemany` 把每条变更写入 `changes(version, position, content)`。**顺序信息只存在 `position` 列**;内容本身原样存入 `content`。
- 80–82 行：兜底捕获 `sqlite3.IntegrityError`(注释说明场景为并发进程抢先创建同名版本),事务回滚后报 `Version already exists`,不留部分数据。
- 86–87 行：成功时 stdout 打印 `Created <版本名>`,返回 0。

版本名匹配方式：两处查询都用 `WHERE version = ?` 参数化精确匹配；`version` 列未声明 collation,SQLite 默认 `BINARY`,即区分大小写、按字节精确比较——与 README「按原文精确匹配，区分大小写」一致。

---

## 3. show:读取与展示、条目顺序的确定

`cmd_show`(90–119 行):

1. 92–93 行：`os.path.exists(args.db)` 为假则直接 `fail(f"Version not found: {args.version}")`。**show 不创建数据库文件**(连接前拦截)。
2. 97–102 行：`SELECT title FROM drafts WHERE version = ?`;查无此行则同样报 `Version not found`,退出码 1。标题取自 `drafts.title`,即 create 时 68–71 行写入的原文。
3. 104–111 行：`SELECT content FROM changes WHERE version = ? ORDER BY position`。**条目顺序完全由 `position` 列升序决定**,而 `position` 在 create 时按 `--change` 传入顺序从 0 编号(76–78 行),因此展示顺序 = 传入顺序。重复内容的条目各自占一行 `changes` 记录，分别保留、分别展示。
4. 115–118 行：拼接输出——
   - 第 1 行 `Version: <版本名>`(用命令行传入的 `args.version`,非库中回读值；因匹配是精确的，两者一致);
   - 第 2 行 `Title: <标题>`;
   - 之后每条变更一行 `f"- {content}"`。`- ` 只加在字符串开头；若 `content` 内部含换行，后续行**不会**加 `- ` 前缀，内部换行原样输出。
5. 输出用 `"\n".join(lines) + "\n"` 写入 stdout，返回 0。

---

## 4. 文本原样性(中文、首尾空格、Markdown 符号)

源码可证明的结论：

- **写入路径无任何裁剪/转义**:`cmd_create` 全函数没有 `strip()`、替换、转义或编码转换；版本名、标题、变更文本以参数化 SQL 原值入库(62–79 行)。
- **读取路径无任何加工**:`cmd_show` 只加 `Version: `/`Title: `/`- ` 前缀(116–117 行),不改写内容本身。
- **中文与 Unicode**:`main()` 把 stdout/stderr 重配置为 UTF-8(149–150 行);SQLite `TEXT` 按 UTF-8 存储。中文原样保存与输出，源码层面成立。
- **首尾空格**:create 的校验只拒绝「整体为空/仅空白」(31–33 行),不裁剪首尾空格；入库与展示均为原值。故首尾空格原样保留，源码层面成立。
- **Markdown 符号**(如 `#`、`*`、`-`):源码中不存在任何 Markdown 解析或转义逻辑，变更文本被当作纯字符串处理；以 `- ` 开头的变更在展示时会变成 `- - ...`,这只是前缀拼接，不是转义。Markdown 符号原样保存，源码层面成立。
- **文本内部换行**:作为普通字符存入 `content`;展示时只在首行前加 `- `(115 行注释与 117 行代码一致),内部换行原样保留。

未确认事项(源码无法证明，需运行验证):

- 终端/重定向环境下 UTF-8 输出的实际字节表现(取决于运行环境)。
- 含 NUL 字符等极端输入在 SQLite 与终端链路中的行为。
- `sqlite3.connect` 在「文件不存在但校验通过」时创建文件的确切时机(懒创建 vs 连接即建);README 的「首次有效创建时自动建立数据库」与此不冲突，但精确时机未确认。

---

## 5. 公开说明与实现对照

一致的部分(逐条核对过):

| README 声明 | 实现位置 | 结论 |
|---|---|---|
| 至少一条变更，`--change` 可重复，按传入顺序保存，重复内容分别保留 | 43、52–53、72–79 行 | 一致 |
| 版本名/标题/变更原样保存，不裁剪不转义 | 全函数无加工；参数化写入 68–79 行 | 一致 |
| 版本名精确匹配、区分大小写 | 62、97 行 `WHERE version = ?` + 默认 BINARY collation | 一致 |
| create 成功：退出码 0,stdout `Created <版本名>` | 86–87 行 | 一致 |
| 无效草稿：退出码 1,stderr `Invalid draft`,不建文件不留数据 | 48–55 行（校验在 57 行连接之前） | 一致 |
| 版本名已存在：退出码 1,stderr `Version already exists: <版本名>`,不覆盖 | 62–66 行；兜底 80–82 行 | 一致 |
| show 成功输出格式 `Version:`/`Title:`/`- <变更>` | 116–118 行 | 一致 |
| 多行变更首行加 `- `,内部换行保留 | 117 行 | 一致 |
| 版本不存在或库文件不存在：退出码 1,stderr `Version not found: <版本名>` | 92–93、100–101 行 | 一致 |

公开说明未覆盖、但实现实际存在的行为(记录差异，不修改文档):

1. **未提供 `--title`**:README 的无效草稿清单只列「为空/仅空白」,未提「未提供」;实现中 `args.title is None` 同样报 `Invalid draft`(50 行)。
2. **argparse 层错误**:缺 `--db`、缺位置参数、未知子命令等由 argparse 处理，退出码为 2、stderr 为用法信息。README 未声明这类失败。
3. **数据库文件存在但缺少表**(例如预先 `touch` 出的空文件):`cmd_show` 不建表也不捕获 `sqlite3.OperationalError`,97 行的查询会抛出未捕获异常，进程以 Python 异常路径退出（退出码 1,stderr 为 traceback),**不是** README 所述的 `Version not found`。同理，`cmd_create` 在文件存在但表缺失时会先由 59 行建表再继续，属正常路径。
4. **「空白」的口径**:实现用 `str.isspace()`(31–33 行),含 Unicode 空白；README 只写「仅由空白组成」,未界定字符集。

---

## 6. 持久化：关闭后重新打开如何读到同一草稿

- 全部状态只落在 `--db` 指向的 SQLite 文件：草稿在 `drafts` 表，变更在 `changes` 表，两表靠 `version` 列关联(16–28 行)。
- create 的写入在 `with conn:`(61 行)事务内，正常结束时提交；进程退出即持久化。
- 之后任意新进程用同一 `--db` 路径执行 `show <版本名>`:92 行确认文件存在 → 97 行按版本名取标题 → 104–111 行按 `position` 取全部变更 → 原样输出。进程间不共享任何内存状态，重开读取的内容与首次创建时写入的完全一致、顺序不变。
- 范围说明：本说明不涉及发布状态、模板、比较、迁移或备份；源码中也不存在这些功能(文件头注释 4–8 行与 README「暂不支持」一致)。

---

## 7. 固定样例（用于逐项核对）

输入数据：版本名 `0.1.0`，标题 `示例发布`，两条变更按序为 `修正文案`、`调整提示`。

### 7.1 创建

```sh
python release_notes.py --db notes.sqlite create 0.1.0 \
  --title "示例发布" \
  --change "修正文案" \
  --change "调整提示"
```

预期（按源码推导，未执行）:

- 退出码 0(87 行 `return 0`)。
- stdout 恰好一行：`Created 0.1.0`(86 行)。
- stderr 无输出。
- 入库结果：`drafts` 一行 `("0.1.0", "示例发布")`;`changes` 两行，`position=0, content="修正文案"` 与 `position=1, content="调整提示"`(72–79 行)。

该数据不触发任何限制：版本名、标题、两条变更均非空非纯空白(48–55 行全部通过)。

### 7.2 查看

```sh
python release_notes.py --db notes.sqlite show 0.1.0
```

预期输出（退出码 0，逐行对应读取逻辑）:

```
Version: 0.1.0
Title: 示例发布
- 修正文案
- 调整提示
```

- `Version: 0.1.0` — 116 行，取自命令行参数 `args.version`。
- `Title: 示例发布` — 116 行，值来自 97–102 行对 `drafts.title` 的查询。
- `- 修正文案`、`- 调整提示` — 117 行逐条加 `- ` 前缀；条目来自 104–111 行 `ORDER BY position` 的查询，顺序即 create 时 `--change` 的传入顺序（`修正文案` position 0 在前，`调整提示` position 1 在后）。
- 末行之后有一个换行符（118 行 `+ "\n"`)。

---

## 8. 三个边界的现有结果（围绕同一样例数据）

前置状态：7.1 已成功创建 `0.1.0`。

### 8.1 重复版本名

```sh
python release_notes.py --db notes.sqlite create 0.1.0 \
  --title "任意标题" --change "任意变更"
```

- 校验通过（输入非空白）→ 连接并确保建表（57–59 行）→ 62–66 行查到已存在 → `fail("Version already exists: 0.1.0")`。
- 结果：退出码 1;stderr `Version already exists: 0.1.0`;stdout 无输出。
- 数据变化：无。`INSERT` 未执行；`with conn:` 事务内只做过 `SELECT`。旧标题与旧条目保持不变。
- 另有兜底：若并发下查重后另一进程抢先插入，80–82 行捕获 `IntegrityError`,事务回滚，同样报 `Version already exists: 0.1.0`,不留部分数据。

### 8.2 空变更条目

两种触发方式，结果相同：

- 完全不传 `--change` → 43 行归一为 `[]` → 52 行 `not changes` 成立；
- 传 `--change ""` 或 `--change "   "`(纯空白)→ 53 行 `any(is_blank(...))` 成立。

- 结果：退出码 1;stderr `Invalid draft`;stdout 无输出。
- 数据变化：无。校验在 48–55 行，位于 `sqlite3.connect`(57 行）之前，连数据库文件都不会被创建或触碰；已存在的 `0.1.0` 草稿不受任何影响。

### 8.3 查询不存在的版本

```sh
python release_notes.py --db notes.sqlite show 9.9.9
```

- 库文件存在 → 92 行通过 → 97–100 行查 `drafts` 无此版本 → `fail("Version not found: 9.9.9")`。
- 结果：退出码 1;stderr `Version not found: 9.9.9`;stdout 无输出。
- 数据变化：无（show 全程只读)。
- 若库文件本身不存在：92–93 行在连接前拦截，同样报 `Version not found`,且不会创建文件。
- 无对应检查的路径：库文件存在但缺少 `drafts`/`changes` 表时（如预先创建的空文件）,show 没有建表逻辑也没有异常捕获，97 行查询会抛出未捕获的 `sqlite3.OperationalError`，进程走 Python 异常退出（退出码 1,stderr 为 traceback)，而非 `Version not found`。README 未覆盖此情形。

---

## 9. 核对路径速查

沿同一条路径可自行复核：

1. 入口与参数：`build_parser` 122–144 行；`main` 147–153 行。
2. create 校验：`is_blank` 31–33 行；48–55 行。
3. create 存储：`SCHEMA` 16–28 行；57–84 行。
4. show 读取与输出：90–119 行。
5. 顺序依据：写入 76–78 行（`enumerate` → `position`);读取 107–108 行（`ORDER BY position`)。
