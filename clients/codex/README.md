# Codex 接入说明

Codex 已有本地长期记忆。双向转译及本机安装请读 [guides/codex.md](../../guides/codex.md)。

本目录仅是旧版静态兼容包，不是 Codex 受管记忆；不要用它覆盖 `~/.codex/memories/` 或 SQLite。

在仓库根先运行 `python -X utf8 adapters/codex/cli.py inspect`，再按指南生成计划、取得本机授权并导入。
导入后用 `verify` 和新会话实际回忆验收；首次人格授权不能从其他机器继承。

更新走 `adapters/codex/cli.py fetch`、`plan`、经确认后的 `apply`。导出使用 `translate` 生成本地候选，人工审阅后再发布。
