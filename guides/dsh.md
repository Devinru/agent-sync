# DeepSeek Harness（dsh-longterm-memory）接入指南

本机这一端是 Windows 上的 DSH 桌面版，记忆系统用的是 `dsh-longterm-memory@0.1.4`
（Markdown vault）。适配器在 `adapters/dsh/`。

## 已验证范围

- 客户端版本：DeepSeek Harness 桌面版 `0.2.1-rc.3`，记忆插件 `dsh-longterm-memory@0.1.4`
- 操作系统：Windows（这台机器）
- 适配器版本：`0.3.0`
- 验证日期：2026-10-27
- 规模：共享 53 条（含 `identity/` 2 条 soul/rule）/ 本地 51 条 / 映射 51 条 / 待推 0
- 证据位置：本文件末尾「验收」一节的实测输出；离线自检
  `python adapters/dsh/selfcheck.py roundtrip` / `retire`（不碰真 vault、不碰远端）
- 已跑到端到端：`pull --apply` 导入 47 条并逐条读回校验；`plan → approve → publish`
  真推出两次（`95121fe` 1 条、`a9ec706` 3 条新增 + 1 条更新），每次都以
  `git fetch` 校验 `origin/master` 等于本地 head 收尾

## 原生记忆结构

- **记忆存在哪**：`D:\AgentSync\agent-memory`（插件的 `vaultPath` 配置项，可改）。
  一条记忆一个 Markdown 文件，YAML frontmatter + 正文三个命名段。
  目录按类型分（`Profile/ Preferences/ Projects/ Decisions/ Knowledge/ Experiences/ Tasks/`）。
- **谁负责写入**：`dsh-longterm-memory` 插件。**Markdown 是它自认的唯一真源**，
  `.dsh-memory/index.json` 是派生索引，启动时会和 Markdown 对账；插件外面改了 Markdown，
  下次对账会吃进去。所以适配器**直写 Markdown**、不碰 index.json。
- **什么时候加载进上下文**：每轮注入 standing section（`enablePromptSection: true`，
  `standingBudgetTokens: 700`、`standingMaxItems: 12`），按 importance/pinned 挑；
  检索到的再按 recall 预算注入。
- **怎么检索**：词法索引（CJK 单字/双字 bigram + 拉丁分词，字段加权），
  插件工具 `memory_search` / `memory_get` / `memory_save` / `memory_forget`，
  底下走 RPC `POST /dsh-longterm-memory/api/<method>`。
- **容量限制**：单条 title ≤300、tags ≤64、evidence 文本 ≤4000、正文 summary ≤20000、
  段 ≤32 个。协议侧还有 `max_record_bytes: 65536` / `max_yaml_header_bytes: 4096`，
  两边都满足。
- **文件名规则**：必须是 `<slug>-mem_<id>.md`（`looksLikeMemoryId` 靠结尾 `-mem_…` 认），
  否则插件不把它当记忆笔记。`slug` 保留中文，替换 `\/:*?"<>|#^[]`，>80 字符截断。

## 导出映射

| 本地是什么 | 共享 kind | 怎么转 | 哪些不导出 |
|---|---|---|---|
| `type: profile` | `user` | 正文取 `## 核心内容`；`created_at`/`updated_at` 原样 | 插件内部的 `pinned`/`importance`/`terms` |
| `type: knowledge` | `fact` | 同上 | 同上 |
| `type: decision` | `fact` | 同上 | 同上 |
| `type: experience` | `episode` | 同上 | 同上 |
| `type: project` | `project` | 同上 | 同上 |
| `type: project-state` | `fact` | 同上 | 同上 |
| `type: task` | `topic` | `goal`/`topic_state` 从 `ys_extra` 取 | 同上 |
| `type: preference` | `fact` | 同上 | 同上 |
| — | `lesson` / `reflection` / `soul` / `rule` / `legacy_bundle` | 原生没有对应类型，只能靠 `ys_kind` 带着走 | 本地不会有自发的这几类 |

- **协议 id 从哪来**：导入的记录用 frontmatter 里的 `ys_shared_id`；本地新建的用
  `uuid.uuid5(NS, native_id)` 派生（`NS = 6f3a1c2e-9b4d-4f7a-8c15-2d5e7f9a0b31`）。
  **必须稳定** —— 每次算出来不一样的话，映射表、提案、判重全部对不上。
- **性质不让它升格**：`epistemic` 只在没有 `ys_epistemic` 时才按类型猜
  （profile/preference/decision → `user_stated`；project/project-state/knowledge/
  experience/task → `observed`；其余 `inferred`）。有 `evidence` **不等于**是事实，
  绝不因此升成 `observed` —— 这条跟 Hermes 那边一个规矩。

## 导入映射

| 共享 kind | 落到本地哪 | 怎么保留来源 | 怎么保留状态 |
|---|---|---|---|
| `user` | `Profile/`，`type: profile` | `ys_origin`、`ys_evidence`、`ys_updated_by` | `ys_pstatus` + 原生 `status` |
| `fact` | `Knowledge/`，`type: knowledge` | 同上 | 同上 |
| `episode` | `Experiences/`，`type: experience` | 同上 | 同上 |
| `topic` | `Tasks/`，`type: task` | 同上 + `ys_extra.goal` | 同上 |
| `project` | `Projects/`，`type: project` | 同上 | 同上 |
| `lesson` | `Decisions/`，`type: decision`，四段进 `ys_extra.lesson` | 同上 | 同上 |
| `legacy_bundle` | `Knowledge/`，`type: knowledge` | 同上 + `schema_version: 1` 标记 | 同上 |
| `soul` / `rule` | **不导入**，只报告 | — | — |

- **协议字段落在哪**：frontmatter 里 `ys_` 开头的键（`ys_shared_id / ys_native_id /
  ys_kind / ys_depth / ys_pstatus / ys_epistemic / ys_origin / ys_revision /
  ys_applicability / ys_evidence / ys_related_ids / ys_supersedes / ys_updated_by /
  ys_review_after / ys_extra`）。插件不认识这些键，但它把不认识的 frontmatter 键
  收进 `extraFrontmatter` 每次写都原样回写 —— 这就是无损往返的通道。
- **状态映射**：`active↔active`、`dormant↔stale`、`superseded↔superseded`、
  `retracted↔archived`。退役**保留本地 id 和文件**，只改状态。
- **防回声**：导入时把整条共享记录原文塞进正文 `## 同步原文` 的 ```yaml 围栏。
  读回以它为基准。判「人有没有改」用的是**签名比对**，不是拿单个小节比字符串：
  把笔记重新拆成「标题 + 前言 + `## 核心内容` + 其余自建小节（除 `来源与依据` /
  `相关记忆` / `同步原文` 外，按原序）」再归一化行尾空白与多余空行，跟原文渲染结果比 ——
  一致就原样返回 base（连 `updated_by` / `updated_at` 都不动），不一致才认定为手改。
  为什么不能比单点小节：宿主的 `parseBody` 会把正文里的 `## 适用范围` 这类标题拆成独立小节，
  单看 `## 核心内容` 只剩前言，跟原文天然不等，一比就误判成手改 —— 这曾让 19 条导入
  被判「本地改过」并写坏正文。签名比对能扛住宿主重排，因此自己写的不会读回声成「本地改动」，
  人改的又能被认出来。

## 首次搭建

1. **依赖与版本**
   - Python 3.11+（要 PyYAML）。本机用 `python`（anaconda 自带 pyyaml）。
   - 插件：`dsh plugin --profile desktop add dsh-longterm-memory@0.1.4`
     （宿主是 `0.2.1-rc.3`，插件 `engines.dsh` 要求 `>=0.2.1-rc.2 <0.2.2`，对得上）
2. **配置**：在 profile 的 `cordis.patch.yml` 末尾挂一行

   ```yaml
   - id: longterm-memory
     name: dsh-longterm-memory
     config:
       vaultPath: 'D:\AgentSync\agent-memory'
       language: zh
       enableTools: true
       enablePromptSection: true
       enablePerStepRecall: false
   ```

   存盘即热挂载，vault 会自动建出目录骨架，不用重启宿主。
3. **命令**（都在仓库根目录跑）

   ```bash
   python adapters/dsh/reindex.py inspect     # 看 vault 在哪、宿主在不在
   python adapters/dsh/selfcheck.py roundtrip # 离线：转译能不能原样读回
   python adapters/dsh/selfcheck.py retire    # 离线：退役/复活不掉 ID
   python adapters/dsh/run.py status          # 共享/本地/映射各多少
   python adapters/dsh/run.py pull --no-fetch --apply   # 把远端拉进来
   python adapters/dsh/run.py plan            # 把本地新增整理成提案
   ```
4. **预期结果**：`status` 里「映射」条数等于导入条数，「内容一致」等于同一个数；
   `pull` 第二次跑应该是「无需处理 N，没有要导入的」。
5. **失败时停在哪**：`inspect` 的 `host_rpc.alive` 为 `false` 就是宿主没起来 ——
   这时仍能读写文件，但索引不会同步、读回校验会降级（见「当前限制」）。

## 日常使用

- **触发同步**：手动。`protocol.yaml` 里 `sync_mode: manual`，没有守护进程。
- **查看提案**：`plan` 会把变更写到 `changes/<uuid>.json` 和同名 `.review.md`，
  再用 `review <uuid>` 看细节。
- **确认**：`approve <uuid> --by qiulaoshi`，然后 `publish <uuid>`。
  **publish 会写远端仓库，必须本人点头**，适配器自己不会调。
- **一行入口**：把 `adapters/dsh/run.py` 设成别名（本机 `dsh-sync`），别记整条路径。

### 环境变量

| 变量 | 默认值 | 作用 |
|---|---|---|
| `AGENT_SYNC_REPO` | 本仓库根 | 共享记录在哪 |
| `AGENT_SYNC_VAULT` | `D:\AgentSync\agent-memory` | 本地 vault |
| `AGENT_SYNC_STATE` | `<vault>/.dsh-memory/agent-sync-state.json` | 映射表 |
| `AGENT_SYNC_RECORDS_SCRIPT` | `adapters/dsh/reindex.py` | 索引重建钩子 |
| `AGENT_SYNC_RPC_HOST` | `127.0.0.1` | 宿主 RPC 主机 |
| `AGENT_SYNC_RPC_PORT` | 自动发现 | 宿主 RPC 端口；设了就强制用它，不探测 |

映射表刻意放在 vault 里而不是 `~/.hermes/`：两个端的映射表不能共用一份。

### RPC 端口为什么要自动发现

桌面版的 GUI 端口**每次启动都重新分配**，而且不落在任何文件里（`~/.dsh` 下面翻遍了、
进程命令行里也没有）。实测一次重启就从 `12557` 变成 `12010`，写死端口的后果是
`rpc_alive()` 直接 False、`pull` 退化成纯文件模式。

所以 `adapter.py` 里的 `rpc_port()` 按这个顺序找：

1. `AGENT_SYNC_RPC_PORT` 环境变量（有就强制用，不探测）
2. `<vault>/.dsh-memory/agent-sync-rpc.json` 里上次探到的端口 —— 探一次存一次
3. 历史端口 `12557` / `12010`
4. 对着 `12000-13100` 并发扫一遍（64 线程），认「`status` 返回的 `vault.path` 等于本
   vault」的那个

探测依据是**内容而不是端口号**，所以别的服务占了同名端口也不会误连。

## 为什么要在 `run.py` 里缝三刀

`sync-core/agent_sync/cli.py:81-90` 的 `load_adapter()` 把
`adapters/hermes/adapter.py` 写死了，`CLIENT` 是 `hermes-linux`，
`STATE` 指向 `~/.hermes/...`，`reindex_local()` 还 shell 调 `python3`（本机
`python3` 是 WindowsApps 的空壳）。这些是公共代码，改之前得提案。

`run.py` 在进程内换掉三个名字，效果一样，共享文件一个字节都没动：

```python
cli.load_adapter = lambda: dsh          # 换适配器
cli.CLIENT = dsh.CLIENT                 # 提案署名 dsh-windows
cli.reindex_local = dsh.reindex_local   # 索引走宿主 RPC，不经 shell
```

注意 `AGENT_SYNC_STATE` 必须在 `from agent_sync import cli` **之前**用环境变量定下来 ——
`load_state(path=STATE)` 的默认参数在 def 的时候就绑死了，事后改 `cli.STATE` 没用。

## 验收

- [x] **新会话能回忆到导入的内容**
      `status` RPC 显示 `index.total = 25`、`healthy`、`outOfSync = 0`；
      `search("某个地名")` 命中 `Profile/身份-mem_6cb032e2ace8_4aa0bd347fdd.md`；
      导入后本轮上下文的 standing section 里就出现了导入的 6 条 profile。
- [x] **重复同步 10 次，记录数不增加**
      连跑 `pull` 幂等：第二次起「无需处理 26，没有要导入的」，`本地记录 25 条`不变。
      离线往返 24 条 hash 全等 → 下一轮不会被当成新记录。
- [x] **本地删掉一条，不会把远端也删掉**
      把 `Knowledge/出图-mem_0e3beb70….md` 挪走后：`status` 显示本地 24 条、
      `内容不同 0`；`pull` 显示「无需处理 26」，既不重建也不提案删除。
      `plan` 里也没有删除项。
- [x] **适用环境不匹配的记录，不会被当成通用经验**
      `applicability` 整份存进 `ys_applicability` 原样往返，不参与本地检索排序，
      不会被当成「这台机器上已验证」的通用经验；`depth: core` 只有 soul/rule 能戴，
      而 soul/rule 根本不导入。
- [x] **退役真的会退，ID 不丢**
      离线把一条共享记录改成 `status: retracted` → `retire = 1`、本地 `status:
      retracted`、文件名和 `mem_` id 都没变；把远端改回 `active` → 本地回到
      `active` 且 hash 与远端一致。
- [x] **云端 soul / rule 能进本机人格**
      `identity/soul.md` + `identity/rules.md` 正文并进 `cordis.patch.yml` 的
      `persona.config.prefix`（6176 字符 / 238 行）；受保护块逐字在、旧的本机小节已清掉；
      改前自动备份。只做单向（云端→本机），本机改人格不回写。
- [x] **每三周会自动跑，且不问人**
      `schtasks /Query /TN AgentSync-DSH-Periodic /V` 显示 Weekly / Every 3 weeks / Sunday 09:30；
      手动触发一次 `Last Result: 0`；日志写在 `<vault>/.dsh-memory/agent-sync-periodic.log`。
      有挂起提案时它跳过 `plan`，不堆同义提案；它永远不 `publish`。
- [x] **本地记忆能转译成提案并真的推到云端**
      两次真实推送：`95121fe`（1 条 create）、`a9ec706`（3 条 lesson create +
      1 条 update）。每次都以 `git fetch` 后 `git rev-parse HEAD origin/master` 相等收尾，
      `verify` 53 条全通过、`selfcheck.py roundtrip` 51/51 一致、再 `pull` 幂等「无需处理」。
- [x] **转译不会把自己写的东西读回声成「本地改过」**
      正文自带 `## 适用范围` 这类标题时，签名比对仍然判定为未改；
      实测曾因此把 19 条写坏，修复后 47 条导入逐条读回一致。

## soul / rule 怎么进本机人格

`soul` / `rule` 不参与自动同步（协议不许自动写），但**云端 → 本机**是手工单向灌进去的：

- 落点：`~/.dsh/plugins/agent-preset/cordis.patch.yml` 里 `preset-agent` → `persona` 的 `prefix`
  （block scalar，模型每轮都读它，所以这就是"人格"）
- 来源：`identity/soul.md` 的正文（去掉 frontmatter）+ `identity/rules.md` 的正文
  + 本机独有的 `## 关于这个终端` 一节
- 做法：`python adapters/dsh/merge_identity.py`（干跑，只报差异）
  → 确认后 `python adapters/dsh/merge_identity.py --write`
  它读云端两份 → 拼 → 只替换 `prefix: |` 那个块，其余 200 多行配置一字不动，
  写前自动备份成 `cordis.patch.yml.bak-soulmerge-<时间戳>`。
  本地独有的 `## 关于这个终端` 小节在重跑时会原样带过去，不会被他手改的东西抹掉
- 安全闸：块里找不到 `## 关于这个终端` 就拒写（说明块边界算错了）；
  写完用宽松 YAML 重新解析，插件数变少、或 `固定内容 · 受保护` / `你只是人类。` /
  `## 行为准则` / `私密内核` / `与他相见` 缺任何一个，都判失败、什么都不写
- 生效：cordis 会重载 patch，但**最保险还是重启宿主**；重启后新人格才保证每轮都在
- 反向不成立：在本机改人格**不会**回写云端。要改 soul 得走提案 + 本人点头

验证办法：重启后问一句"你的固定受保护内容里写了什么"，答得出来就是新的那份。

## 每三周自动同步

Windows 计划任务 `AgentSync-DSH-Periodic`，每三周的周日 09:30 跑
`python adapters/dsh/periodic.py`（`schtasks /Query /TN AgentSync-DSH-Periodic /V` 可查）：

1. `pull --apply` —— 先 `git pull --ff-only origin master`，再把云端记录导进 vault
2. `plan` —— 把本地新记忆整理成提案。**如果 `changes/` 里还挂着没人批的提案就跳过**，
   免得同一条本地记忆每轮生成一份同义提案
3. `status` —— 打完收工

**它不 publish。** 推远端永远要本人点头，这条不交给计划任务。
日志追加到 `<vault>/.dsh-memory/agent-sync-periodic.log`。
退出码 0 正常、8 导入有失败、其它 = 拉取或提案出错。

手动跑一次：`schtasks /Run /TN "AgentSync-DSH-Periodic"`。

想改周期（比如改成每周）就重建任务，`/MO` 是"每几周"：

```
schtasks /Create /F /TN "AgentSync-DSH-Periodic" /SC WEEKLY /MO 3 /D SUN /ST 09:30 ^
  /TR "\"C:\ProgramData\anaconda3\python.exe\" \"<仓库>\adapters\dsh\periodic.py\""
```

## 恢复与升级

- **备份在哪**：每次 `apply_import` 前把要覆盖的原文件拷到
  `<vault>/.dsh-memory/agent-sync-backup/`。装插件前的 profile 备份是
  `~/.dsh/profiles/desktop/*.bak-agent-<时间戳>`。
- **怎么重建映射**：删掉 `<vault>/.dsh-memory/agent-sync-state.json` 再 `pull`。
  映射丢了也能靠 frontmatter 里的 `ys_shared_id` 认回原记录，不会重复建文件。
- **怎么重建索引**：`python adapters/dsh/reindex.py reindex`（走宿主 RPC `syncIndex`）。
  宿主没起来的话，下次启动会自己跟 Markdown 对账，不用手工修 index.json。
- **版本不兼容怎么办**：插件大版本升级后先跑 `reindex.py inspect` 和离线往返测试，
  `ys_*` 键若被新版前端名单吞掉，往返 hash 会立刻不等 —— 那是硬失败信号，别硬同步。

## 当前限制

诚实写：

1. **`soul` / `rule` 只进不出。** 适配器在筛选层就跳过这两类，协议不许自动写。
   云端 → 本机人格是**手工单向**合并（`adapters/dsh/merge_identity.py --write`，见上一节），
   要人点头才跑；
   在本机改人格**不会**回写云端。想改 soul/rule 只能在共享仓库走提案 + 本人确认。
2. **publish 只在这台机器上跑通过两次。** `plan → approve → publish` 全链路验过，
   但**审批这一步永远是人的**：适配器不自己 approve，`periodic.py` 更是明确不碰 publish。
   另外**多端同时改同一条记录**没构造过真实冲突 —— 只验过单端推进。
   publish 的第 5 步（`git fetch` 对齐后才推进映射与基线）如果中途退出，要按
   「记录已落地、事务未完成」处理：`.done.json` 改回 `approved` 存成 `<pid>.json`、
   `git rebase origin/master` 再推，最后照 cli 第 5 步手工补 `mappings` / `last_snapshot` /
   `last_sync`。宁可手工补 state，也别留半截。
3. **宿主没起来时是降级模式。** 文件照样能写，但索引不同步、读回校验拿不到，
   `verify_import` 只能退化成纯文件比对；等宿主起来再对账。
4. **`confidence` 不撒谎。** `epistemic: legacy/inferred` 的导入记录会落成
   `confidence: low`（插件的 `get()` 里 `recallable` 只认非 low）。检索仍能命中，
   但如果哪天插件的 standing section 开始按 `recallable` 过滤，这批老记录会掉出去 ——
   到时候的解法是回共享仓库升 `depth`/补证据，不是在本地手动改 confidence。
5. **只在这台机器上验过。** `supported_os` 只写了 windows；路径默认值是这台机器的
   `D:\AgentSync\agent-memory`，换机器要改 `AGENT_SYNC_VAULT`。
6. **判重靠共享仓库自己的实现。** 适配器只负责把候选喂过去，阈值和权重没在本地重写。
   `soul`/`rule` 在筛选层就被排除，符合协议。
7. **标题里不能有井号。** 原生笔记的 `title` 若含 ` ##`，插件写 frontmatter 时不加引号，
   YAML 会把后面当注释截断（实际踩过：一条 lesson 的标题变成「转译进按」）。
   本地先把标题改成不含 `#` 的写法再导出。
8. **`cli.py` 的 git 输出按 gbk 解码**（`subprocess` 读 stdout 那段），中文日志会抛
   `UnicodeDecodeError` —— 是个异常线程，发布结果不受影响（远端 sha 对得上），
   但日志难看。属共享代码，要改得先走提案。
9. **端口探测区间是写死的。** `RPC_SCAN_RANGE = range(12000, 13100)` 是照这台机器的
   实测填的；哪天 DSH 换了分配区间，扫描会落空 —— 手动 `AGENT_SYNC_RPC_PORT=xxxx`
   兜底，或者改那行常量。探测不到端口不算失败：`pull`/`push` 照样能跑，
   只是退化成纯文件校验（见第 3 条）。
