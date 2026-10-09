---
name: everything-file-search
description: Use when locating local files or folders on Windows by filename, extension, directory, or modification date, especially across drives, then reading or operating on the resolved paths. Supports Everything/es.exe with bounded directory-search fallback. 中文触发：找文件、文件在哪、搜索电脑文件、定位路径、找到后修改或整理。
---

# Everything 文件定位与后续操作

优先查询 Everything 索引，取得完整路径，然后完成用户要求的读取、编辑、复制、移动、重命名等操作。用户已经给出准确路径时，直接检查该路径；无需重新搜索。

## 搜索

用本技能目录下的 `scripts/search_files.py`。脚本只搜索，不修改目标文件。需要 Python 3.9+，无需第三方库。Windows x64 随技能附带官方 `bin/es.exe`；其他架构可通过 `--es-path` 或 `EVERYTHING_ES_PATH` 指定适配的 ES。

把示例中的 `$skillDir` 设置为当前读到的技能目录绝对路径，不依赖当前工作目录：

```powershell
python "$skillDir/scripts/search_files.py" --name '报价' --ext xlsx --limit 20
python "$skillDir/scripts/search_files.py" --name '合同' --root 'D:\资料' --after 2026-09-01 --before 2026-10-01
python "$skillDir/scripts/search_files.py" --kind directory --name '项目'
python "$skillDir/scripts/search_files.py" --diagnose
```

- `--name` 是文件名的字面子串，不是通配符；`--ext` 和 `--root` 可以重复。`--after` 包含当天，`--before` 不包含当天。根据用户时区解释“上个月”等相对日期。
- 默认 `--backend auto`：先查 Everything，IPC 不可用或 ES 缺失时改用目录搜索。没有指定目录时，备用搜索仅覆盖当前目录及存在的用户桌面、文档、下载目录。
- 备用搜索默认最多运行 15 秒，默认返回 30 条。需要更广范围时指定目录并逐步扩展，不默认遍历所有磁盘。它会搜索隐藏文件，但不递归进入目录链接或 junction。
- 输出为 JSON：`backend`、`scope`、`results`、`has_more`、`partial`、`warnings`。路径、是否仍存在、修改时间和文件大小有助于判断目标。备用搜索结果不保证按时间排序；需要最近文件时收窄条件或用 Everything。
- `has_more` 表示结果已截断，`partial` 表示时间限制或访问失败造成搜索不完整。零结果只能说明此次范围内没有匹配，不能据此断言电脑上不存在该文件。Everything 也只覆盖其已配置的索引位置。
- 高级 Everything 语法使用 `--query 'file: 报价 ext:xlsx;xls' --backend everything`。原样保留用户明确给出的语法；该模式及 `--instance` 不自动降级，以免把查询语法误当文件名。
- `--diagnose` 可确认 ES 路径及 IPC 状态。ES 报 Error 8 时，检查 Everything 是否运行、实例名称及进程权限是否相容；沙箱也可能隔离 IPC，不要直接判定用户未安装软件。环境允许时使用合规的权限提升验证。

## 判断目标与操作

先用用户描述的文件名、类型和目录收窄搜索。存在多个匹配时，利用路径、修改日期、大小和必要的内容检查判断。仍无法确定目标时，展示少量候选完整路径让用户选择，不按首条结果猜测。

备份目录、WPS addons 内的副本与独立安装可能同时出现；文件数量不能证明冲突，也不构成删除依据。优先使用用户指定版本；工具接入优先考虑独立版。安装路径因电脑而异，应通过用户提供的路径、运行进程或安装信息发现，并检查该路径确实存在。

搜索结果可能已过期。操作前检查路径存在性与目标类型，必要时读取内容确认。按用户请求使用对应文件或文档工具完成后续工作；寻找文件的请求本身不授权移动、删除或上传它。

PowerShell 文件操作使用绝对路径和 `-LiteralPath`。移动、重命名、复制前检查目标位置是否已存在，避免意外覆盖。修改仍受当前会话写入权限约束，skill 和索引不会扩大权限。按操作类型检查最终文件内容或新路径，报告实际结果。

## 缺少 Everything 的电脑

备用目录搜索可以直接使用，无需安装 Everything。经常跨盘搜索时再建议安装。若用户已要求配置快速搜索，使用 [Everything 官方下载页](https://www.voidtools.com/downloads/) 获取适合架构的 Everything 与 ES；避免修改 WPS 的内置组件或重复启动多个实例。不要在每次搜索时下载工具。

[ES 官方使用说明](https://www.voidtools.com/support/everything/command_line_interface/)
