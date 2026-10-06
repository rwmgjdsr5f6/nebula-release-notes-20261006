# 版本发布说明管理台

管理版本发布记录和变更说明。面向本地单机使用，仅依赖 Python 3 标准库与
内置 SQLite，无需安装任何第三方包，也无需联网。

当前实现的是最小草稿功能：创建版本草稿、按版本名查看草稿、修订已有草稿
的标题、向已有草稿追加变更。暂不支持版本重命名、变更编辑或删除、分类、
发布、导出或 Git 相关操作。

## 入口

命令行入口为 `release_notes.py`，所有操作都通过 `--db` 指定同一个
SQLite 数据库文件。首次有效创建时若该文件不存在会自动建立数据库，之后
的进程使用同一路径即可继续读取与追加。

### 创建草稿

```
python release_notes.py --db notes.sqlite create <版本名> --title <标题> --change <变更> [--change <变更> ...]
```

- 每份草稿至少包含一条变更；`--change` 可重复传入，条目按传入顺序保存，
  重复内容分别保留。
- 版本名、标题、变更文本原样保存：中文、标点、首尾空格及文本内部换行都
  保留，不自动裁剪或转义。
- 版本名按原文精确匹配，区分大小写。
- 成功时退出码为 0，标准输出为 `Created <版本名>`。
- 以下情况退出码为 1，标准错误输出 `Invalid draft`，且不会创建文件或
  留下部分草稿：
  - 未提供变更，或版本名、标题、任一变更为空 / 仅由空白组成。
- 版本名已存在时，退出码为 1，标准错误输出
  `Version already exists: <版本名>`，不覆盖旧标题或条目。

### 查看草稿

```
python release_notes.py --db notes.sqlite show <版本名>
```

- 成功时退出码为 0，依次输出版本、标题和每条变更：

  ```
  Version: <版本名>
  Title: <标题>
  - <变更 1>
  - <变更 2>
  ```

- 多行变更仍是一条记录，展示时在首行前加 `- `，内部换行原样保留。
- 版本不存在（或数据库文件尚不存在）时，退出码为 1，标准错误输出
  `Version not found: <版本名>`。

### 修订草稿标题

```
python release_notes.py --db notes.sqlite set-title <版本名> --title <新标题>
```

- 只修改标题：版本名与每条变更的原文、数量、顺序均保持不变。
- 新标题按输入原样保存：中文、标点、首尾空格及内部换行都保留，不自动
  裁剪或转义。版本名按原文精确匹配，区分大小写。
- 成功时退出码为 0，标准输出为 `Updated title: <版本名>`。再次提交同一
  标题也按成功处理，不会新增草稿或变更条目。
- 未提供 `--title`，或版本名、新标题为空 / 仅由空白组成时，退出码为 1，
  标准错误输出 `Invalid draft`；即使目标版本不存在，也优先返回这一结果。
- 输入有效但数据库文件不存在，或库内没有对应版本时，退出码为 1，标准
  错误输出 `Version not found: <版本名>`；不会创建数据库文件或补建草稿。

### 追加变更

```
python release_notes.py --db notes.sqlite add-change <版本名> --change <变更>
```

- 每次调用只允许一个 `--change`，向指定草稿追加**一条**变更：新条目位于
  所有旧条目之后；重复文本分别保留，不去重。
- 版本名、追加文本原样保存：中文、标点、首尾空格及文本内部换行都保留，
  不自动裁剪或转义。多行文本仍只算一条变更，展示时只在首行前加 `- `，
  内部换行原样输出。版本名按原文精确匹配，区分大小写。
- 只改变更列表：标题、版本名以及旧条目的原文、数量、相对顺序均保持不变，
  其他版本不受影响。
- 成功时退出码为 0，标准输出为 `Added change: <版本名>` 加末尾换行，
  标准错误为空。
- 未提供或重复提供 `--change`，或版本名、追加文本为空 / 仅由空白组成时，
  退出码为 1，标准错误输出 `Invalid draft`；即使目标版本不存在也优先
  返回这一结果。
- 输入有效但数据库文件不存在，或库内没有对应版本时，退出码为 1，标准
  错误输出 `Version not found: <版本名>`；不会创建数据库文件或补建草稿。

## 离线复现示例

以下示例不依赖网络，可直接复制到终端执行。

创建草稿：

```sh
python release_notes.py --db notes.sqlite create 0.1.0 \
  --title "首个预览版" \
  --change "新增草稿创建" \
  --change "支持版本查看"
```

输出（退出码 0）：

```
Created 0.1.0
```

查看草稿（也可以结束当前进程后，在新的进程中用同一数据库路径执行）：

```sh
python release_notes.py --db notes.sqlite show 0.1.0
```

输出（退出码 0）：

```
Version: 0.1.0
Title: 首个预览版
- 新增草稿创建
- 支持版本查看
```

只修订标题（版本名与变更保持不变）：

```sh
python release_notes.py --db notes.sqlite set-title 0.1.0 --title "首个预览修订"
```

输出（退出码 0）：

```
Updated title: 0.1.0
```

再次查看，标题已更新，其余内容与之前完全一致：

```sh
python release_notes.py --db notes.sqlite show 0.1.0
```

输出（退出码 0）：

```
Version: 0.1.0
Title: 首个预览修订
- 新增草稿创建
- 支持版本查看
```

向已有草稿追加一条变更（标题与旧条目保持不变）：

```sh
python release_notes.py --db notes.sqlite add-change 0.1.0 --change "修正提示文案"
```

输出（退出码 0）：

```
Added change: 0.1.0
```

再次查看，新条目排在所有旧条目之后：

```sh
python release_notes.py --db notes.sqlite show 0.1.0
```

输出（退出码 0）：

```
Version: 0.1.0
Title: 首个预览修订
- 新增草稿创建
- 支持版本查看
- 修正提示文案
```

再次创建同名版本会失败，原有内容保持不变：

```sh
python release_notes.py --db notes.sqlite create 0.1.0 \
  --title "重复版本" --change "不应写入"
# 退出码 1，标准错误：Version already exists: 0.1.0
```

传入空白变更同样失败：

```sh
python release_notes.py --db notes.sqlite create 0.2.0 \
  --title "空白变更" --change "   "
# 退出码 1，标准错误：Invalid draft
```

失败后再次查看 `0.1.0`，仍得到与首次创建完全一致、顺序不变的内容。

> 提示：`notes.sqlite` 等数据库文件已在 `.gitignore` 中忽略。需要重置
> 示例数据时，删除该文件后重新执行创建命令即可。
