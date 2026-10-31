# 记录格式

每条共享记忆 = 一个文件，`memory/records/<uuid>.md`。
人格核心 = `identity/soul.md`、`identity/rules.md`，同样格式但受保护。

## 基本结构

```markdown
---
schema_version: 2
id: "e38ca060-4b8f-4e07-a12b-26e638374f09"
revision: 1
kind: lesson
depth: recent
status: active
title: "一句话标题"
origin: "hermes-linux"
updated_by: "hermes-linux"
created_at: "2026-10-27T12:00:00+08:00"
updated_at: "2026-10-27T12:00:00+08:00"
epistemic: observed
sharing: approved
applicability:
  frameworks: []
  os: []
  projects: []
evidence:
  - id: "ev-001"
    kind: session
    origin: "hermes-linux"
    occurred_at: "2026-10-27T11:50:00+08:00"
    locator: "local-ref:session-xxx/message-12"
related_ids: []
supersedes: []
review_after: "2026-12-26T12:00:00+08:00"
lesson:
  context: "什么情境下"
  action: "我做了什么"
  outcome: "结果如何"
  adjustment: "下次怎么改"
---

正文：完整叙述，保留因果、限定、时间和当时的情境。

正文不能只写标题，不能压缩掉「在什么条件下成立」。
```

## 字段

### 必填

| 字段 | 说明 |
|---|---|
| `schema_version` | 固定 `2` |
| `id` | uuid4，创建一次，永不变 |
| `revision` | 从 1 开始；内容实质变化 +1 |
| `kind` | 见下表 |
| `depth` | `recent` / `stable` / `core` |
| `status` | `active` / `dormant` / `superseded` / `retracted` |
| `title` | 一句话，不超过 60 字 |
| `origin` | 首次来源端，永不改 |
| `updated_by` | 最后改动端 |
| `created_at` / `updated_at` | 带时区 ISO 8601 |
| `epistemic` | `user_stated` / `observed` / `inferred` / `creative` / `legacy` |
| `sharing` | 远端恒为 `approved`；本地候选不能伪造此值 |
| `applicability` | 四个数组，空表示不限制 |
| `evidence` | 至少一条 |
| 正文 | 见上 |

### kind

| kind | 用途 | 额外必填 |
|---|---|---|
| `soul` | 人格核心 | —（只在 identity/） |
| `rule` | 总行为准则 | —（只在 identity/） |
| `user` | 用户本人 | — |
| `episode` | 一次经历 | — |
| `fact` | 原子事实 | — |
| `lesson` | 教训与纠正 | `lesson.context/action/outcome/adjustment` |
| `topic` | 进行中的事 | `goal`、`topic_state` |
| `project` | 项目上下文 | — |
| `reflection` | 反思 | — |
| `legacy_bundle` | 旧格式整包 | `migration` |

### depth

- `recent` — 新沉淀，未经验证
- `stable` — 反复出现或被确认
- `core` — 只允许 `soul` / `rule`

### status

- `active` — 正常
- `dormant` — 不常驻，仍可检索
- `superseded` — 被新记录取代（`supersedes` 指向它）
- `retracted` — 撤回，普通回忆不返回

**退役保留 ID**，防止其他端重新复活旧条目。

### evidence

```yaml
evidence:
  - id: "ev-001"          # 同一原始事件稳定不变
    kind: session          # session / tool_result / user_stated / file / legacy
    origin: "hermes-linux"
    occurred_at: "..."     # 未知可省
    locator: "local-ref:..."  # 本地引用，不上传原文
```

**同一来源重复 10 次，evidence 仍算 1 条。** 自动扫描、被检索、跨端导入，都不新增 evidence。

## 内容 hash

用于判断「是不是真的改了」：

1. 把 YAML 元数据解析成对象 + 正文
2. `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`
3. UTF-8 编码后 SHA-256

比较时**排除** `revision` / `updated_at` / `updated_by` 三个管理字段，其余全参与。

**不要用 git blob hash 代替**——那是文件字节，这是规范对象。

## 解析限制

- 只允许一个 YAML front matter + 正文
- 安全 YAML 解析器，**禁止**自定义 tag、重复键、锚点、别名
- 头部 ≤ 4 KB，正文 ≤ 64 KB（超了拆成关联记录，不截断）
- 路径由 id 生成，**不接受** `../` 或任意输出路径
- 未知可选字段**原样保留**，不能解析后丢掉

## 校验清单

写入前必须过：

1. `id` 是 uuid4，且在整个仓库内唯一
2. `kind` / `depth` / `status` / `epistemic` 在枚举内
3. `lesson` 类四个字段都非空
4. `topic` 类有 `goal` 和 `topic_state`
5. `supersedes` 指向存在的 ID，无环
6. 时间带时区
7. 正文 ≥ 10 字符
8. 文件大小在限内
9. `depth: core` 只出现在 identity/

## 一个完整的 lesson 示例

```markdown
---
schema_version: 2
id: "78ad7086-6c28-400f-b0ca-d576127c2021"
revision: 2
kind: lesson
depth: stable
status: active
title: "别把人住哪搞反"
origin: "hermes-linux"
updated_by: "hermes-linux"
created_at: "2026-10-27T12:44:00+08:00"
updated_at: "2026-10-27T13:10:00+08:00"
epistemic: observed
sharing: approved
applicability:
  frameworks: []
  os: []
  projects: []
evidence:
  - id: "ev-78ad7086"
    kind: session
    origin: "hermes-linux"
    occurred_at: "2026-10-27T12:45:00+08:00"
    locator: "local-ref:hermes/memories/MEMORY.md:L41"
related_ids: []
supersedes: []
review_after: "2027-02-24T12:44:00+08:00"
lesson:
  context: "整理记忆系统、举例说明三层结构时"
  action: "直接引用了 MEMORY 里那条错误的位置信息，没有先去查 用户档案"
  outcome: "用户当场指出：他在城市 A，城市 B 是 agent 自己待的地方"
  adjustment: "人的基本信息一律以 用户档案 为准，不以自己的记忆条目为准"
---

我在记忆里写了他在城市 B，实际他在城市 A。

原因不是笔误——是那条错误本来就在 MEMORY 里躺着，我照着它放大了一遍，
没有回头去查权威源。档案里明明写着「现城市 A」。

这一条的教训不是「记住他在城市 A」，是「人的基本信息有唯一权威源，别拿二手记忆当事实」。
