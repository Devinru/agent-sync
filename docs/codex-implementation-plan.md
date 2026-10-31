# Codex 本机接入实施计划

日期：2026-10-27。依据：本次用户四项指令、README、protocol/adapters.md、现场 Codex 0.164.0-alpha.11.1、官方 Memories 与 AGENTS.md 文档。

目标：保留现有 Codex 原生自动记忆；共享记忆经适配器进入独立的本机长期层，提供全文检索、原始记录保留、双向候选与三方冲突检查。首次按明确授权导入云端人格和规则，不推送，不建后台任务，不编辑生成的 MEMORY.md / memory_summary.md / SQLite。

## 设计及边界

- adapters/codex/adapter.py：六接口，JSON 本地记录保留 `_shared_md`；快照只读扫描自动记忆的条目作为本地导出候选。
- adapters/codex/cli.py：inspect、plan、apply、verify、recall、export-plan、fetch。没有 publish/approve 命令，避免把机器执行等同人的审批。
- 运行目录：`$CODEX_HOME/agent-sync/`，与 Git checkout、受管 memories 目录分开。索引、状态、计划和备份均留本机。
- 首次替换全局 AGENTS.md：云端 soul/rule 正文原样保留，加本机检索说明；只添加一个小型 ad_hoc note 指向完整本地资料，不修改其他记忆文件。
- 首次增量：已有原生条目不删不覆盖；相似候选先检查，再明确记录 separate/linked 的理由；不让算法自动重写或合并人格。
- 后续 protected 变化拒绝自动应用。普通记录三方比较；双改、本地删除、远端缺失均报告。导出候选不伪造 sharing=approved，不增加导入证据，不自动上传。
- 宿主记忆原生索引无公开外部导入 API，因此声明 partial；“本地检索可用”和“宿主后台合并完成”分别记录。

## 执行与验证

1. 先建隔离测试：往返 hash、重复导入十次、本地原生文件不变、protected 单次授权、双改冲突、过期计划、重复候选、退役与适用范围、失败回滚。
2. 运行测试确认缺失实现导致失败，再实现 adapter 和 CLI。
3. 写指南、manifest；只对新增适配器运行测试和协议检查。
4. 只读生成真实导入计划，检查相似候选；冻结批准内容与源 hash 后首次应用。保存旧 AGENTS 和运行数据备份。
5. 比对原生记忆文件哈希；往返读回、检索每条适用记录、重跑十次无重复。用 Codex debug prompt-input 验证新会话实际加载全局人格和检索入口；可行时启动隔离新会话验证回忆。
6. 交付本机证据及仍需人工决定项。代码保留本地未提交，不上传任何私人资料。
