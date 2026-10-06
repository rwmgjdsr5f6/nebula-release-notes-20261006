# 版本发布说明管理台

管理版本发布记录和变更说明。面向本地单机使用，仅依赖 Python 3 标准库与 SQLite，无需联网或安装第三方包。

当前实现最小草稿功能：创建版本草稿、按版本名查看标题与变更条目。编辑、分类、发布、导出与 Git 操作暂未加入。

## 使用方式

两个操作都通过 `--db` 指定 SQLite 数据库文件。首次有效创建时若文件不存在会自动建立数据库，后续进程使用同一路径即可继续读取。

### 创建草稿

```sh
python release_notes.py --db notes.sqlite create 0.1.0 \
    --title "首个预览版" \
    --change "新增草稿创建" \
    --change "支持版本查看"
```

- 成功：退出码 0，标准输出 `Created 0.1.0`。
- `--change` 可重复，至少一条；条目按输入顺序保存，重复内容分别保留。
- 版本名、标题或任一变更为空或仅空白，或未提供变更：退出码 1，标准错误 `Invalid draft`，不留下部分草稿。
- 版本名已存在：退出码 1，标准错误 `Version already exists: <版本名>`，不覆盖旧内容。

### 查看草稿

```sh
python release_notes.py --db notes.sqlite show 0.1.0
```

- 成功：退出码 0，依次输出：

  ```
  Version: 0.1.0
  Title: 首个预览版
  - 新增草稿创建
  - 支持版本查看
  ```

- 版本不存在：退出码 1，标准错误 `Version not found: <版本名>`。
- 版本名按原文精确匹配，区分大小写。

名称、标题与变更文本均原样保存：中文、标点、首尾空格及文本内部换行都保留，不裁剪、不转义；多行变更仍是一条记录，展示时保留内部换行。

## 离线复现示例

```sh
# 1. 创建（首次运行会建立 notes.sqlite）
python release_notes.py --db notes.sqlite create 0.1.0 \
    --title "首个预览版" \
    --change "新增草稿创建" \
    --change "支持版本查看"
# 输出: Created 0.1.0   退出码: 0

# 2. 结束进程后重新查看，内容完整且顺序不变
python release_notes.py --db notes.sqlite show 0.1.0

# 3. 重复创建同名版本 → 退出码 1，标准错误: Version already exists: 0.1.0
python release_notes.py --db notes.sqlite create 0.1.0 --title "x" --change "y"

# 4. 空白变更 → 退出码 1，标准错误: Invalid draft
python release_notes.py --db notes.sqlite create 0.2.0 --title "x" --change "  "

# 5. 上述失败不影响已有数据，再次查看 0.1.0 仍为原始内容
python release_notes.py --db notes.sqlite show 0.1.0
```
