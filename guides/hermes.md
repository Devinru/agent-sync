# Hermes 接入指南

**状态**：`verified`（2026-10-27 实测往返）

Hermes 是第一个接入的端。这份文档有两个用途：给 Hermes 自己看，以及**给其他端当样板**——照着这个结构写你自己的，就能接进来。

---

## 已验证范围

| 项 | 值 |
|---|---|
| 框架 | Hermes Agent |
| 操作系统 | Linux 6.8.0 |
| Profile | `default` |
| 验证日期 | 2026-10-27 |
| 验证方式 | 22 条 legacy 记录迁移 + 往返解析 + 三方判定 11 项 |

---

## 原生记忆结构

### 存哪

| 层 | 路径 | 大小 | 注入 |
|---|---|---|---|
| 人格 | `~/.hermes/SOUL.md` | ~5 KB | 每轮 |
| 长期记忆 | `~/.hermes/memories/MEMORY.md` | 上限 12,000 字符 | 每轮 |
| 用户档案 | `~/.hermes/memories/用户档案` | 上限 3,000 字符 | 每轮 |
| 经历库 | `~/.hermes/memory/records/` | 无上限 | 按需检索 |
| 会话原文 | `~/.hermes/state.db` | SQLite | 按需检索 |
| 技能 | `~/.hermes/skills/` | — | 相关时加载 |

### 谁写

| 写入方 | 写什么 | 什么时候 |
|---|---|---|
| `memory` 工具 | MEMORY / USER | agent 主动判断时 |
| `records 脚本` | records | 有过程的经历 |
| dream cron（02:30） | MEMORY 整理 + records | 每晚 |
| 用户手改 | 任何文件 | 随时 |

### 怎么检索

- **MEMORY / USER / SOUL**：不用检索，每轮都在上下文里
- **records**：`python3 ~/.hermes/scripts/records 脚本 recall <词>`——同时查 records 的 FTS5 和 sessions 的 FTS5
- **sessions**：`session_search` 工具，FTS5 + BM25 + lineage 去重

---

## 导出映射

| Hermes 里是什么 | 共享 kind | depth | 怎么转 |
|---|---|---|---|
| `SOUL.md` 全文 | `soul` | `core` | 逐字节搬，加 front matter |
| `MEMORY.md` 里的一句话 | `fact` / `user` / `rule` | `recent` | 原文搬，补 evidence |
| `records/*.json` 的 episode | `episode` | `recent` | 正文 + 元数据映射 |
| `records/*.json` 的 lesson | `lesson` | `stable` | 四段结构已在，直接映射 |
| 技能里的操作规则 | **不导出** | — | 技能是本机行为，不是共享记忆 |
| 会话原文 | **不导出** | — | 太大，且含私人对话 |

**不导出的东西**：会话原文、日记、凭据、本机路径细节（路径写 `local-ref:` 引用即可）。

---

## 导入映射

| 共享 kind | 落到 Hermes 哪 | 怎么保留来源 |
|---|---|---|
| `soul` | **不改** `SOUL.md`，只做差异提示 | 绝不自动写 |
| `rule` | MEMORY 里加一条，注明「来自共享」 | `evidence.locator` 保留 |
| `user` / `fact` | MEMORY 或 USER | 同上 |
| `episode` / `lesson` | `~/.hermes/memory/records/` | 新 JSON，`origin` 记来源端 |
| `topic` / `project` | records | 同上 |
| `legacy_bundle` | records，标记 `legacy` | 同上 |

**导入后不更新 MEMORY 常驻摘要**，除非这条明确要求常驻。records 是「想起来才查」的那层。

---

## 人格核心的特殊规则

`SOUL.md` **以本地为准，同步到 git**：

```yaml
source_of_truth: "local:hermes-linux:~/.hermes/SOUL.md"
sync_policy: "local-wins"
```

意思：

1. **本地改了 → 覆盖 git 上的 `identity/soul.md`**，不问
2. **git 上改了 → 不自动写回本地**，只报告「远端有差异，要不要看」
3. **别的端要改人格 → 只能提案**，永远不能直接写

为什么单向：人格是「我这个人是谁」，不该被别的端悄悄改。但本地改了要留档，所以同步到 git。

---

## 首次搭建

```bash
# 1. 依赖
pip install pyyaml

# 2. 克隆
git clone https://github.com/your-org/agent-sync.git ~/agent-sync

# 3. 验证解析
cd ~/agent-sync && python3 -c "
import sys; sys.path.insert(0,'sync-core')
from pathlib import Path
from agent_sync.protocol import Record
for p in Path('memory/records').glob('*.md'): Record.load(p)
print('解析正常')"

# 4. 跑测试
python3 tests/test_compare.py
```

---

## 日常使用

```bash
# 看本地和远端差了什么（不写任何东西）
python3 -m agent_sync.cli status

# 生成提案，写到 changes/<id>.draft.json
python3 -m agent_sync.cli plan

# 看提案（人眼可读）
python3 -m agent_sync.cli review <id>

# 确认后发布
python3 -m agent_sync.cli publish <id>
```

**频率**：手动拉起，想起来跑一次。**不做自动。**

---

## 验收清单

- [x] 22 条 legacy 记录能解析、能往返（hash 一致）
- [x] 三方判定 11 项全过（noop/pull/push/conflict/retire/restore）
- [x] `identity/soul.md` 正文与本地 `SOUL.md` 逐字节一致
- [ ] 导入一条新记录后，`recall` 能搜到
- [ ] 重复同步 10 次，记录数不增加
- [ ] 本地删一条，不会把远端也删掉

**没勾的还没验证。** 空白不等于通过。

---

## 恢复

- **写前备份**：每次改 MEMORY / USER 前存 `~/.hermes/backups/`
- **映射丢了**：映射表在 `~/.hermes/memory/sync-state.json`，丢了可以按 `evidence.locator` 重建
- **版本不兼容**：`protocol.yaml` 的 `protocol_version` 主版本变了就停，不硬跑

---

## 当前限制

1. **只支持 Linux 上的 Hermes**，没在 Windows / macOS 测过
2. **不会自动同步**——设计如此，不是缺陷
3. **导入不改 MEMORY 常驻层**——需要常驻的得人工加
4. **records 和共享仓库是两套存储**，靠映射表对应，不是同一个目录
5. **`recall` 的短词（<3 字符）走 LIKE 兜底**，比 FTS5 慢，但结果正确
