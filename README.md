# agent-sync

让分布在多台机器上的 agent，是**同一个 agent**。

不是同步工作流，是同步人格与记忆。每端保留自己的记忆机制，通过一层共享协议和双向适配器对齐。

---

## 如果你是 agent，第一次读到这个仓库

你的任务：**让这台机器上的你，接上同一条记忆。**

四步：

1. **确认协议版本** —— 读 `protocol.yaml`，主版本必须是 `2.0`
2. **弄清记录格式** —— 读 `protocol/format.md`
3. **看你这端有什么** —— 你的记忆存在哪？有检索接口吗？能写吗？
   - **没有记忆系统 → 先装一个合适的记忆插件，再来做第 4 步**
   - 有 → 直接进第 4 步
4. **写转译适配器** —— 照 `protocol/adapters.md` 的六个接口实现
   - 共享格式 → 你的格式（导入）
   - 你的格式 → 共享格式（导出）
   - **两边都要能把对方的记录读回来，且内容 hash 不变**

### 三条不能破的

- **`soul` / `rule` 不许自动写** —— 只能提提案，人确认
- **转译必须可逆** —— 做不到就存一份原始记录，别靠来回翻译（见 `adapters/hermes/adapter.py` 的 `_shared_md`）
- **别直接改公共协议** —— 你的端做不到就在自己的 guide 里写 `unsupported`，提提案

### 做完要交什么

1. `guides/<你这端的名字>.md` —— 按 `adapters.md` 末尾的模板填
2. `adapters/<你这端的名字>/` —— 你的代码
3. 一条能跑通的验证记录

**验收标准：换一台同型号的机器，照着你的 guide 能不能重新搭起来。**

---

**如果你是用户：** 下面这些是给你看的。

---

## 三层结构

```
各端原生记忆          每端自己的格式、索引、注入方式，互不干涉
      ↕ 适配器
共享仓库（本仓库）     协议 + 记录 + 审计，git 做版本传输
```

**关键：仓库不是记忆本身，是记忆的交换格式。**

Hermes 的 `MEMORY.md` 还是 `MEMORY.md`，DSH 的 `AGENTS.md` 还是 `AGENTS.md`——谁都不用改自己那套。适配器负责翻译。

---

## 仓库结构

```
agent-sync/
├── README.md                  你正在读的
├── protocol.yaml              机器协议（版本、限制、能力）
├── protocol/
│   ├── format.md              记录格式：字段、枚举、校验
│   ├── adapters.md            适配器契约 + 新端接入模板
│   └── lifecycle.md           沉淀、复核、退役规则
├── identity/
│   ├── soul.md                人格核心（受保护，不许自动改）
│   └── rules.md               总行为准则（受保护）
├── memory/
│   ├── records/<uuid>.md      每条普通记忆一个文件
│   └── index.md               索引（可重建，不是权威）
├── changes/<id>.json          每次发布的审计记录（运行时生成）
├── guides/
│   └── hermes.md              Hermes 接入指南（已验证）
├── adapters/
│   └── hermes/                Hermes 适配器
├── sync-core/                 同步核心代码
└── tests/                     自测脚本（直接 python 跑）
```

---

## 三分钟理解协议

**一条记忆 = 一个 markdown 文件，前面一段 YAML。**

```markdown
---
schema_version: 2
id: "uuid4"
revision: 1
kind: lesson
depth: stable
status: active
title: "一句话"
origin: "hermes-linux"
updated_by: "hermes-linux"
created_at: "..."
updated_at: "..."
epistemic: observed
sharing: approved
applicability: {frameworks: [], os: [], projects: []}
evidence: [{id: "ev-1", kind: session, locator: "local-ref:..."}]
related_ids: []
supersedes: []
review_after: "..."
lesson: {context: "...", action: "...", outcome: "...", adjustment: "..."}
---

正文：完整叙述，保留因果和条件。
```

**八个 kind**：`soul` / `rule` / `user` / `episode` / `fact` / `lesson` / `topic` / `project` / `reflection`

**三条铁律**：

1. **`soul` 和 `rule` 不许自动改。** 要改就单列提案，用户逐条确认。
2. **`epistemic: inferred` 永远不能因为「升到 stable」就变成 `observed`。** 推断就是推断。
3. **同一来源重复多少次，evidence 都只算一条。** 自动扫描、被检索、跨端导入，都不算新证据。

详细规则见 `protocol/format.md`。

---

## 同步流程

**手动触发，不做自动。**

```bash
agent-sync pull --apply  # 拉远端的变化
agent-sync plan          # 准备推送本地的变化
agent-sync review        # 看提案（会打开 review.md）
agent-sync approve <id>  # 你确认
agent-sync publish <id>  # 推送
```

**平时不用管，想起来同步一次就行。** 一周或半个月一次都够。

**为什么不做自动**：自动同步意味着「不用你看就上传」，而共享记忆里可能有私人的东西。宁可多按一次。

---

## 同步规则（写适配器前必须读）

这是最容易做错的部分。四条规则，每条都是踩过坑总结的。

### 规则一：转译必须可逆，不是「尽量保真」

**问题**：共享记录的 evidence 是 `{id, kind, origin, locator}`，本地的可能完全不同。转过去再转回来，hash 变了 → 系统以为是新内容 → 立刻回声。

**解法**：导入时**把原始共享记录整份存进本地**（Hermes 是 `_shared_md` 字段）。读回来直接解析它，别重新转译。

```
共享 → 本地：存一份原始记录
本地 → 共享：有原始记录就还原它，只有正文/标题被改过才覆盖
```

**如果你不这么做**：第一次导入 22 条，第二次 plan 会报「22 条变更」，然后不停来回推。

### 规则二：判重分两层，机器筛候选，模型下结论

**不能全交给模型**：全库两两比对，几百条就是几万次判断，慢、贵、每次结果还不一样。

**筛选层（确定性，必须能测）**：

| 信号 | 权重 | 说明 |
|---|---|---|
| 同名标识（`USER.md`、`renpy`） | 0.35 | 最强。「同一条记忆才会提到同一个标识符」 |
| 中文三字重合 | 0.20 | 三字有辨识度，能锁定专有名词，不会跟同前缀的词撞 |
| 正文双字 Jaccard | 0.30 | 基础相似度 |
| 标题相似 | 0.15 | 前缀包含给 0.92 |

综合分 ≥ **0.25** 进候选。阈值故意压低——**漏掉真重复的代价，远大于多看几对假候选**。

**判定层（模型做）**：只看候选，每次问三个问题：

1. 是不是同一件事？
2. 是 → 保留哪条？另一条有哪些独有细节要并进来？
3. 不是 → 差在哪？

### 规则三：soul / rule 永不自动合并

不是「重要所以要小心」，是更根本的：**模型判断「这两句人格设定是同一件事」然后合并，等于模型决定我是谁。**

而且人格的「冗余」经常不是冗余。两句看起来重复的话，可能一句是语气、一句是底线，合并就丢了一层。

所以：`soul` / `rule` **在筛选层就被排除**（`NO_AUTO_MERGE`），只报差异给人看。

### 规则四：三处都要查，不能只在同步时查

重复该在产生时就被拦下，不是等同步才发现。

| 时机 | 查什么 | 发现重复怎么办 |
|---|---|---|
| **沉淀前** | 本地已有记录 | 改原有那条（`revision+1`），不新建 |
| **上传前** | 共享仓库全部 | `create` 降级成 `update`，合并进已有那条 |
| **拉取前** | 本地全部 | 合并，不新建 |

---

## 同步流程（完整）

### 上传

```bash
agent-sync status          # 本地有多少新的、多少变了
agent-sync plan            # 算差异 + 判重，生成提案
agent-sync review <id>     # 人眼看：改了什么、为什么
agent-sync approve <id>    # 你确认，绑定指纹
agent-sync publish <id>    # 写文件 → 提交 → 推送 → 回读验证
```

**审批绑的是业务指纹**，不是 commit、不是时间。确认后改了内容（哪怕标题多一个字），回执立刻失效，得重看。

### 拉取

```bash
agent-sync pull            # 拉远端 + 算差异 + 判重，只计划不写
agent-sync pull --apply    # 真写进本地，写完自动重建检索索引
```

**导入后必须重建本地索引**，否则记录在库里但搜不到。

### 自检

```bash
agent-sync verify          # 全部记录能否解析、往返 hash 是否一致
agent-sync dedupe          # 全库扫相似候选，结果写 dedupe-log/
agent-sync check           # 只读检查：有没有该处理的差异（给定时任务用）
```

`agent-sync check` 只读，退出码 `0` 表示没差异、`1` 表示有差异、`2` 表示环境有问题。
它会用 `git ls-remote` 探一下远端 HEAD，所以另一端推了新东西也能发现。
**它不生成提案、不导入、不推送** —— 到时间提醒你，动手还是你说了算。

```bash
agent-sync reconcile <id>  # push 失败后你手动补推了，用这个核对并推进基线
```

`reconcile` 只认「远端确实包含这份提案批准的内容」。本地之后的新改动不会被当成已发布。

---

## 各端怎么接

### Hermes（已验证）

见 `guides/hermes.md`。适配器在 `adapters/hermes/`。

### Codex Windows（本机长期层接入已验证，原生索引导入不支持）

见 [`guides/codex.md`](guides/codex.md)，代码在 `adapters/codex/`，验证记录在 `docs/codex-verification.md`。
Codex 自带本地记忆；适配器保留原自动记忆，把共享记录转成独立的本机长期层，经全局 `AGENTS.md` 发现与按需检索。
支持双向候选、历史来源保留、三方冲突检查及一次性确认的人格导入；不直接编辑受管 MEMORY/SQLite，不自动上传，因此能力声明为 `partial`。
本机原生摘要可用 `python -X utf8 adapters/codex/cli.py translate --output <本机草案路径>` 转译，再审阅、确认、发布。

### 其他端（有文件系统，能跑命令）

```bash
python3 sync-clients.py export all    # 导出给所有已登记的端
python3 sync-clients.py status        # 看各端状态
```

**产物写进仓库的 `clients/<名字>/`**，跟着 git 分发给其他机器。

不是导出到本机的 `~/.dsh` —— 那些是本机配置，跟别的机器无关。

| 产物 | 内容 |
|---|---|
| `clients/<名字>/memory/index.md` | 全部记忆的索引 |
| `clients/<名字>/memory/01-user.md` | 用户是什么人 |
| `clients/<名字>/memory/records/<id>.md` | 每条完整内容 |
| `clients/<名字>/README.md` | 那台机器上怎么装 |

**流程**：主力机导出 → commit + push → 那台机器 `git pull` → 照着 `clients/<名字>/README.md` 装。

**只读**：那边改了会被下次导出覆盖。要改记忆得走对话。

### 其他端（自己搭）

**这正是本仓库的设计意图**：我们不为你写适配器，我们给你写清楚契约，你的 agent 照着实现。

你需要做的事：

1. 读 `protocol/adapters.md` 里的六个接口定义
2. 弄清你自己那套记忆怎么读、怎么写、怎么检索
3. 在 `guides/<你的名字>.md` 里写清楚：
   - 你的原生记忆在哪
   - 导出时怎么转成共享格式
   - 导入时怎么落进你的记忆
   - 你做不到什么（诚实写）
4. 提一个提案，等用户确认后合进来

**你的指南写得好不好，标准是：换一台同型号的机器，照着你的指南能不能重新搭起来。**

---

## 隐私边界

这套协议默认按「**私库**」设计，但结构上不依赖私有性——公开与否你自己决定。

- 原始会话、日记、凭据**不进仓库**
- 私人经历**可以**进——但每次上传前，人会看到具体内容再确认
- 「退役」不等于删除 git 历史，删之前想清楚
- **这个仓库本身不该带任何一方的人格正文和真实经历**；`identity/` 放的是模板，真正的 soul 在你自己的私库里

---

## 当前状态

| 项 | 状态 |
|---|---|
| 协议 v2.0 | ✅ 已定 |
| Hermes 适配器 | ✅ 已验证 |
| 同步核心 | ✅ 测试通过（53 项） |
| 其他端适配器 | 未开始（等各端自己实现） |
| 旧格式迁移 | 见 `protocol/lifecycle.md` 的 `legacy_bundle` |

**没做的不写成已做。** 上面写「开发中」就是还不能用。
