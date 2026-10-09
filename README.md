# Everything File Search

一个用于 agent 的本地文件搜索技能。通过自然语言描述目标文件，使用 Everything 快速获取完整路径，再交给 agent 完成读取、编辑或整理等后续操作。

支持 Everything 命令行搜索，也支持没有安装 Everything 时的目录搜索。

## 功能

- 按文件名、扩展名、文件夹和修改日期查找。
- 使用 Everything 索引搜索跨磁盘文件，避免每次遍历磁盘。
- 返回完整路径、文件大小、修改时间和是否仍然存在。
- Everything 不可用时，自动使用有时间限制的目录搜索。
- 支持中文路径、带空格路径，以及多个搜索目录。
- 显示搜索范围、结果截断和未完成搜索的提示。

搜索脚本只定位文件。后续操作由 agent 根据用户请求和当前文件权限执行。

## 工作方式

```text
用户描述目标文件
        ↓
agent 调用技能搜索脚本
        ↓
优先通过 es.exe 查询 Everything 索引
        ↓ 不可用
在指定目录或默认目录内搜索
        ↓
返回完整路径和文件信息
        ↓
确认目标后，执行用户要求的操作
```

Everything 负责维护文件索引，`es.exe` 负责查询索引，技能负责指导 agent 选择搜索方式并使用结果。

## 环境要求

| 组件 | 要求 |
| --- | --- |
| Python | 3.9 或更高版本；脚本无需第三方 Python 库 |
| Everything | 快速搜索需要已安装且正在运行的 Everything |
| ES | 提供 Windows x64 版 `es.exe`，版本 1.1.0.38 |
| agent | 能加载技能，并能执行本地 Python 脚本 |

未安装 Everything 的电脑可以使用备用目录搜索。其他 Windows 架构应从官方获取匹配的 ES，并使用 `--es-path` 或 `EVERYTHING_ES_PATH` 指定位置。普通目录搜索也可以单独运行在其他操作系统上，但主要适配和验证环境为 Windows。

## 安装

将 `everything-file-search` 文件夹放入所用 agent 的技能目录，具体位置以该工具的配置为准。agent 需要支持加载 `SKILL.md` 并执行本地脚本；也可以直接运行下方的 Python 命令。

保持下面的文件结构：

```text
everything-file-search/
├── README.md
├── SKILL.md
├── agents/
│   └── openai.yaml
├── bin/
│   ├── es.exe
│   └── LICENSE-ES.txt
└── scripts/
    └── search_files.py
```

如需快速跨盘搜索，从 [Everything 官方下载页](https://www.voidtools.com/downloads/) 安装 Everything 并启动。`es.exe` 是独立命令行工具，需要配合运行中的 Everything 使用。

安装后在 agent 中查看技能列表。如果没有显示，重新打开应用或新建聊天后再检查。

## 在 agent 中使用

在聊天中指定技能和目标：

```text
使用 $everything-file-search 帮我找黑马程序员的学习资料。
```

也可以给出更具体的条件：

```text
使用 $everything-file-search 找到 D 盘资料目录里上个月修改过的 Excel 报价表。
```

```text
使用 $everything-file-search 找到项目的 README，读取它并总结启动步骤。
```

当文件名相同且无法明确判断目标时，技能会指导 agent 展示候选路径。明确目标后，再执行相应操作。

## 直接运行脚本

下面的命令在技能目录中运行：

```powershell
# 按文件名查找，默认只返回文件。
python ./scripts/search_files.py --name '报价'

# 在指定目录中查找 Excel 文件。
python ./scripts/search_files.py --name '报价' --ext xlsx --root 'D:\资料'

# 按修改日期筛选：包含 9 月 1 日，不包含 10 月 1 日。
python ./scripts/search_files.py --name '合同' --after 2026-09-01 --before 2026-10-01

# 查找文件夹。
python ./scripts/search_files.py --name '课程' --kind directory

# 同时查找文件和文件夹。
python ./scripts/search_files.py --name '黑马' --kind any

# 强制使用备用目录搜索。
python ./scripts/search_files.py --name '讲义' --root 'D:\学习资料' --backend scan

# 高级 Everything 查询。
python ./scripts/search_files.py --query 'file: 报价 ext:xlsx;xls' --backend everything

# 检查 ES 路径及 Everything 连接情况。
python ./scripts/search_files.py --diagnose
```

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--name` | 文件名的字面子串，不是通配符或正则表达式 |
| `--root` | 搜索目录；可重复指定 |
| `--ext` | 扩展名，例如 `xlsx`；可重复指定 |
| `--kind` | `file`、`directory` 或 `any`，默认 `file` |
| `--after` | 修改日期的起点，包含当天，格式为 `YYYY-MM-DD` |
| `--before` | 修改日期的终点，不包含当天 |
| `--limit` | 最大返回数量，默认 30，范围 1–1000 |
| `--timeout` | ES 等待或备用搜索时间预算，默认 15 秒，最大 120 秒 |
| `--backend` | `auto`、`everything` 或 `scan`，默认 `auto` |
| `--query` | 高级 Everything 查询，不会自动降级为目录搜索 |
| `--es-path` | 指定 ES 可执行文件位置 |
| `--instance` | 指定 Everything 命名实例，不能降级为目录搜索 |
| `--diagnose` | 输出 ES 路径和连接诊断结果 |

时间预算用于限制等待和搜索；进程启动或系统文件访问可能带来额外耗时。

### 返回结果

脚本输出 JSON。例如：

```json
{
  "backend": "everything",
  "scope": ["D:\\资料"],
  "query": "regex:\"报价\" file: ext:xlsx <path:\"D:\\资料\\\">",
  "has_more": false,
  "partial": false,
  "results": [
    {
      "path": "D:\\资料\\报价表.xlsx",
      "name": "报价表.xlsx",
      "type": "file",
      "size_bytes": 14280,
      "modified": "2026-09-23T14:30:00+08:00",
      "exists": true
    }
  ],
  "warnings": [],
  "returned": 1
}
```

- `backend`：实际采用的搜索方式。
- `scope`：此次搜索覆盖的位置。
- `has_more`：匹配数量超过返回上限，应收窄条件或提高上限。
- `partial`：因时间限制或无法访问某些目录，搜索未完整完成。
- `warnings`：降级原因、搜索超时或跳过目录等信息。
- `exists`：返回信息时能否访问该路径；操作前仍需重新确认。

## 搜索范围与常见问题

### 没有 Everything，能用吗？

可以。`auto` 模式会改用目录搜索，`scan` 模式可以直接使用。未指定 `--root` 时，备用搜索范围是当前目录，以及存在的用户桌面、文档和下载目录。它不会默认扫描所有磁盘。

### 明明安装了 Everything，为什么连接失败？

先运行 `--diagnose`。Error 8 表示 ES 没有连接到 Everything IPC，可能涉及程序未运行、命名实例、进程权限或沙箱隔离。它并不能单独证明 Everything 未安装。

在 agent 沙箱隔离 IPC 的环境中，可能需要允许该只读查询在沙箱外执行。应遵循当前环境的权限流程。

### 返回零结果，表示文件不存在吗？

只能说明当前搜索范围和条件下没有返回匹配。Everything 只覆盖已配置的索引位置；备用搜索可能有目录范围、访问权限或时间限制。需要时调整关键词、扩大指定目录或检查索引配置。

### 能按文件内容搜索吗？

普通参数用于文件名、目录、扩展名和修改日期搜索。若要找文件中的某句话，先定位候选目录，再使用内容搜索工具或对应文档工具。高级 `--query` 的能力取决于 Everything 的版本和配置。

### 会自动修改或删除文件吗？

搜索脚本不会修改或删除目标文件。后续操作由 agent 按用户明确请求执行，并受当前会话权限限制。目录链接和 junction 不会被备用搜索递归遍历。

## 验证情况

在 Windows 环境中，使用 Everything 1.4.1.1026 和 ES 1.1.0.38 验证了：

- 中文、空格及含特殊字符的文件路径。
- 文件名、扩展名、日期和文件夹筛选。
- 多个目录范围和高级查询。
- 结果数量上限、零结果和无效参数处理。
- Everything 不可用时的自动降级。

这不等同于所有系统、架构和 Everything 版本均已验证。

## 官方资源

- [Everything 下载](https://www.voidtools.com/downloads/)
- [ES 命令行说明](https://www.voidtools.com/support/everything/command_line_interface/)
- [ES 官方源代码](https://github.com/voidtools/ES)
- [OpenAI 技能说明](https://developers.openai.com/plugins/concepts/skills)

`es.exe` 是 voidtools 提供的第三方工具，随附的 MIT 许可见 [bin/LICENSE-ES.txt](bin/LICENSE-ES.txt)。该许可适用于 ES，与本项目编写的技能说明及脚本分别适用。
