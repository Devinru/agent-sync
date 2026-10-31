# Codex 接入验证记录

日期：2026-10-27；源仓库快照：`f9964e6f0b96af30e77870e1c795a025278344c5`，与当时 `origin/master` 一致。

## 后续授权与发布准备

首次验收后，用户明确授权开启联网/MCP 聊天参与自动记忆生成、发布适配器代码，并将本机长期记忆转译后推送私库。
本机已只改 `memories.disable_on_external_context: true -> false`，写前备份并用 TOML 对象比较确认其他配置不变。

新增原生摘要转译器 `adapters/codex/translate.py`，以 Task Group 保留历史任务状态、适用条件、偏好和故障教训，略去会话路由元数据，遮盖主机路径、内网端点和样例客户编号。产物在人工审阅和具体授权绑定前均为 `local_candidate`，保持 `legacy` 性质，不因转译新增证据。

新增转译/静态 Codex 接入说明测试，累计 **27 项通过**，独立审阅者重跑同样通过。结果旁注里的视觉核验未完成、端到端仍待验证等状态已保留；HTTPS 链接不受路径脱敏影响，UNC 路径停止人工处理。原来的静态分发入口已转到新指南，避免误覆盖 Codex 受管记忆。后续具体记忆发布内容与指纹由 `changes/` 中的本次发布审计记录，以下“未发布”等描述仅指首次验收当时。

环境：Windows、Codex `0.164.0-alpha.11.1`、Python 3.12.4、PyYAML 6.0.1、适配器 0.1.0。最低要求 Python 3.11。

## 已完成

- 解析共享记录 26 条：24 条普通记忆、1 条 soul、1 条 rule。
- 初次计划扫描原有 Codex 自动记忆 323 个候选块；达到确定性阈值的重复候选 0，三方冲突 0。
- 首次导入：普通记忆进入本机 JSON 长期层；人格和准则正文保留原样，加入全局 AGENTS 的本机检索入口；原全局文件写前备份。
- 全部 26 条共享→本机→共享规范对象 content_hash 一致，原始共享 Markdown 保存，未知字段和 evidence 保留。
- 24 条普通 active 记录均能用本机检索入口找到。
- 原有 86 个 memories 文件哈希全部不变；只增加一份小型 ad_hoc 资料定位说明；config.toml 哈希未变。
- 真实本机连续 apply 同一无差异计划十次：记录数始终 26，写入数始终 0，state 与全部记录字节不变；导出已导入条目产生变更 0。
- `codex debug prompt-input` 显示新会话实际加载云端人格、行为准则和本机检索入口。
- 使用本机正常配置、`codex exec --sandbox read-only --ephemeral` 启动独立新会话：成功读取本机索引和完整记录，回答一个只在共享资料中存在的问题，并正确引用原记录的 `legacy` 性质。事件日志证实读取退出码为 0，未把文件存在当作回忆成功。

隔离测试 **16 项通过**，见 `tests/test_codex_adapter.py`。覆盖往返/十次重跑、protected 单次授权及类型降级拦截、双改、本地删除、过期计划、判重、退役/适用范围、导出防回声、写失败回滚、摘要候选、note 顺序、重复 YAML key、Windows 中文输出、双方收敛时推进映射基线、子集导入不扩大范围。

独立只读审查发现的 protected 类型降级与收敛基线遗漏均先增加测试复现，再修复；真实首次数据不受影响。修复后本机 26 条记录与 24 条普通记忆检索再次通过验证。

独立审阅者复查：上述两项关闭，子集导入修复通过；独立重跑 16 项全部通过，未发现阻塞问题。

## 未宣称完成

- 没有外部写入 Codex 受管索引，没有编辑生成的 MEMORY.md 或 SQLite；因此原生索引导入仍为 unsupported，整体 manifest 为 partial。
- 宿主是否已经把 ad_hoc note 归并进自动记忆，留给宿主后台处理，不声明已完成。
- 去掉用户配置的隔离 CLI 验收曾遇到 shell 执行策略拦截；保留本机正常配置、相同只读模式的验收成功。不声称适配器已在空白配置或别的系统验证。
- 无 push、无私人资料上传、无自动调度。本次适配器与指南尚未提交发布，其他机器不能只拉原仓库就获得新代码。
- 不认证所有历史内容都仍然有效，不把 `legacy` 或 `inferred` 转成 `observed`。
- 新会话启动还报告已有 `web-access/SKILL.md` 缺少 YAML front matter。它未阻断本次本机回忆验证；仅记录既有环境告警，本次没有修改该 skill。

## 证据存放

本机工作目录、仓库之外保留：`codex-original-baseline.json`、`codex-bootstrap-plan.json`、`codex-bootstrap-authorization.json`、`codex-bootstrap-result.json`、`codex-local-verification.json`、`codex-prompt-verification.json`、`codex-idempotence-verification.json`、`codex-export-draft.json`、`codex-fresh-recall-default-events.jsonl` 和新会话最终回答。它们包含本机路径或个人资料，不能随代码自动提交。

恢复旧人格的实际 before 文件在本机 `agent-sync/backups/<timestamp>/AGENTS.md`。不要用 checkout 覆盖不相关本地文件。
