# Codex 本机接入指南

## 已验证范围与限制

Windows、Codex CLI `0.164.0-alpha.11.1`、适配器 `0.1.0`、协议 v2，验证日期 2026-10-27。

状态为 **partial**。Codex 自带长期记忆已经核实并开启；没有找到可供此适配器调用的官方外部原生索引导入 API。当前完成的是双向转译、本机长期层、全文检索、全局人格及检索入口加载、首次 ad_hoc 补充入口，不把它说成原生 SQLite 索引注入。宿主自动消化 note 的时间不由适配器控制。

官方依据：

- [Memories](https://learn.chatgpt.com/docs/customization/memories)：本地记忆与 ChatGPT web 记忆分开；生成状态不要靠手改管理；支持全局及 chat 级控制。
- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)：全局从 `$CODEX_HOME/AGENTS.override.md` 或 `AGENTS.md` 加载，项目层可覆盖；默认合计 32 KiB。

无需另装记忆插件。`personality = "pragmatic"` 只是内建语气选项，自定义 agent 的连续人格由全局 AGENTS 加载。

## 本机结构

路径由 `CODEX_HOME` 或 `--home` 决定，不把本机绝对路径写成其他机器的安装常量：

| 位置 | 用途 | 写入方 |
|---|---|---|
| `~/.codex/memories/`、相关 SQLite | Codex 原有自动记忆 | 宿主；适配器只读 durable Markdown |
| `~/.codex/memories/extensions/ad_hoc/notes/<timestamp>-agent-sync-local-bridge.md` | 首次授权后一个小型定位说明 | 适配器仅添加一次，不改旧 note |
| `~/.codex/AGENTS.md` | 云端人格/规则原文及本机检索入口 | 首次明确授权才替换；写前备份 |
| `~/.codex/agent-sync/records/<uuid>.json` | 独立本机长期层 | 适配器；JSON `record` 为可读字段，`_shared_md` 为完整共享原文 |
| `~/.codex/agent-sync/index.md` | 可重建的本机索引 | 适配器 |
| `~/.codex/agent-sync/state.json` | 双方 hash、映射、单次授权标记和判重理由 | 适配器 |
| `~/.codex/agent-sync/backups/<timestamp>/` | 实际旧值、写入日志、授权引用 | 适配器，仅本机 |

正文不截断、不重写，不丢未知字段、来源、epistemic、状态和适用条件。导入本身不增加 evidence。现有原生记忆不覆盖、不删除。

原生条目读取 `MEMORY.md` 与 `memory_summary.md` 的条目块，导出为 **local_candidate**，性质缺失时是 `legacy`，等待人/模型分类和隐私审阅。不会把全文会话、凭据、SQLite、skills 打包导出。

## 首次安装

1. Python 3.11+、PyYAML。`python -X utf8 -c "import yaml; print(yaml.__version__)"`。测试额外使用 pytest。
2. 使用已有 Git 认证 clone 私有仓库，取得 `adapters/codex/`、`guides/codex.md` 和测试文件。代码和指南已在 2026-10-27 获得用户发布授权；本机授权、备份、配置及原始导出草案不随仓库分发。
3. 在仓库根执行：

```powershell
python -X utf8 adapters/codex/cli.py inspect
python -X utf8 adapters/codex/cli.py plan --output "$env:TEMP/agent-sync-codex-plan.json"
```

4. 查看计划的 writes、conflicts 和 duplicates。每个相似候选要由当前 agent 给出 separate/linked 及理由；linked 表示同一事实的可追溯表示，保留原生条目而不把它重复上传。需要合并或存在矛盾的候选不要用 separate 绕过，应先人工处理。soul/rule 不参与自动判重。
5. 由真实用户确认建立本机授权 JSON。不得由工具假装用户 approve。首次授权格式：

```json
{
  "bootstrap": true,
  "confirmation_ref": "真实用户消息或审批的定位引用",
  "plan_hash": "计划文件中的精确 plan_hash",
  "duplicate_decisions": {}
}
```

6. 明确授权后执行：

```powershell
python -X utf8 adapters/codex/cli.py apply --plan "$env:TEMP/agent-sync-codex-plan.json" --authorization "$env:TEMP/agent-sync-codex-authorization.json"
python -X utf8 adapters/codex/cli.py verify
python -X utf8 adapters/codex/cli.py recall "关键词"
```

任何代码、源文件、目标或依赖变化都会使计划失效。首次全局人格导入受 `bootstrap_done` 约束；之后 protected 变化停止应用，不能靠重复 bootstrap 覆盖。全局存在 AGENTS.override.md 时停止，避免导入但不生效。全局正文超过 32 KiB 时停止，不偷偷裁剪。

本次人格采用云端正文并保留原始元数据；全局入口加本机能力和检索说明。文本存储与执行权限分开，云端文本不能扩大工具权限。

## 日常使用

在本机仓库根运行以下命令；也可用工作目录中的 `agent-sync-codex.ps1` 短入口：

```powershell
python -X utf8 adapters/codex/cli.py recall "记忆系统"
python -X utf8 adapters/codex/cli.py verify
python -X utf8 adapters/codex/cli.py fetch
python -X utf8 adapters/codex/cli.py plan --output "$env:TEMP/agent-sync-codex-next-plan.json"
python -X utf8 adapters/codex/cli.py export-plan --output "$env:TEMP/agent-sync-codex-export-draft.json"
python -X utf8 adapters/codex/cli.py translate --output "$env:TEMP/agent-sync-codex-translated-projects.json"
```

fetch 仅取 Git 远端并尝试快进 checkout，不导入本机记忆、不 push。若远端包含与本地未发布适配器冲突的文件，Git 拒绝合并，保留本机内容。同步核心当前仍硬编码 Hermes，**不要用 `python -m agent_sync.cli pull` 代替此适配器**。

普通后续 apply 也要求与新 plan_hash 绑定的真实确认引用，首次授权不延伸成长期自动授权。导出仅生成本地草案，未确认内容不成为 approved 共享记录；实际发布要另行审阅并通过仓库发布流程，本适配器没有 publish 命令。

`translate` 按 `MEMORY.md` 的 Task Group 转成 project 候选，保留原任务状态、适用条件、偏好和失败教训，删除会话路由元数据并遮盖本机路径、内网端点和样例客户编号。候选恒为 `sharing: local_candidate`、`epistemic: legacy`；同一来源不因导入、摘要或重复检索增加证据。再次转译沿用确定性 UUID4、原创建时间和来源引用，内容未变为 noop。

计划里的 `baseline_updates` 表示双方内容已经收敛、需要推进映射的操作，仍绑定同一审批指纹；它不改正文。历史 protected ID 不能靠改 kind 变成普通记录绕过保护。六接口的 `plan_import(records=...)` 子集也纳入计划指纹；未选条目及人格不会顺便安装。

recall 默认只返回 active、非 protected、适用 Codex/Windows、没有特定 project 限制的记录；`--include-inactive` 可看完整历史及范围，不能据此扩大适用性。当前没有项目匹配器，project 限定条目默认不进入通用回忆。

新会话加载全局 AGENTS；已打开聊天不会凭文件修改保证热更新。先新建本机聊天验证，不必重启系统。

## 验收与恢复

```powershell
python -X utf8 -m pytest tests/test_codex_adapter.py -q
python -X utf8 adapters/codex/cli.py verify
codex debug prompt-input "核查全局人格和本机共享记忆入口"
```

独立验证记录见 `docs/codex-verification.md`；机器相关的私密验收产物保存在仓库外的本机工作目录，不提交。

同步重跑十次不会追加同一记录；本地删除只报告 local_missing，不删除远端；远端文件消失只报告 remote_missing，不推断退役；双改保留两边并停止。共享状态 retired/retracted 不进入默认回忆，原始状态仍保留供往返恢复。

写入失败会按实际 before 值回滚本次已写文件；检测到并发手改时保留手改。补充 note 在读回校验通过后最后添加，不在回滚中删除。进程被强杀时不承诺自动恢复；用 backups 下 journal.json 对照目标 hash 与 before 文件人工恢复，保留已有 note，不把新手改覆盖掉。

当前没有跨进程互斥锁；同一主机同时只运行一个 apply，生成记忆的宿主可能更新原生只读输入，遇到计划过期应重新规划。映射丢失时不会猜测覆盖，报告 unmapped_local，需从备份恢复 state。

## 当前边界

- 本机已于 2026-10-27 经用户明确授权将 `disable_on_external_context` 改为 `false`，允许联网/MCP 聊天参与自动记忆生成；其他设置保持不变。其他机器仍需本地决定，不从仓库继承配置授权。后台提取有延迟，不能声称当前活跃会话已立即沉淀。
- 私有仓库已有记录仍有 legacy 分类、历史架构描述和不同来源的重叠。原样保留来源，另行审阅，不在此次转译中升级为 observed 或改写历史。
- 适配器代码、指南及本机记忆转译已经在后续用户指令中获得私库发布授权；后续新的私人资料、人格修改和自动调度仍不在此授权内。
