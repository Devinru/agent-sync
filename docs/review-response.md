# 对代码审查的回应

**审查基准**：`dda0da1`
**处理基准**：`8c45965` 之后
**日期**：2026-10-27

审查质量很高，九条 P1 里有七条是真的。逐条回应如下。

分类：

- **已修** —— 确认是 bug，改了
- **部分修** —— 问题存在，但按使用者定的规矩不收那么紧
- **不改** —— 和需求本身冲突，说明理由

---

## 一、已修

### 1. 发布入口可绕过或重写确认回执

**问题**：`publish --yes` 在 `check_approval()` 失败后自己调 `prop.approve()`；`merge --publish` 直接 commit+push，完全不过提案。

**已修**：

- 删掉 `--yes` 的自动签收分支。发布命令里不能有「我确认了」这句话由机器自己说的路径。
- 删掉 `merge --publish`。合并现在生成一个提案（含 `groups` 依赖组），走 `review → approve → publish`。
- 回归：`tests/test_rework.py` 的 RV01，含「确认后改内容 → 回执失效」。

**没跟着改的**：审查要求「保留真实用户确认来源，不能只靠 `--by qiulaoshi`」。见下面「二、不改」第 3 条。

### 2. `git add -A` 会上传确认范围外的文件

**问题**：发布和合并都对整仓库暂存。先生成未批准的提案 A、再批准 B，发布 B 时 A 也被带上。

**已修**：改成按提案点名的路径暂存，并核验 staged 集合 == 批准清单：

```python
paths = [f"memory/records/{sid}.md" for sid in written]
paths.append(f"changes/{prop.proposal_id}.done.json")
git("add", *paths)
staged_now = set(git("diff", "--cached", "--name-only").stdout.split())
unexpected = staged_now - allowed
if unexpected: 拒绝提交并 reset
```

暂存区原本就有别的东西 → 直接拒绝，不自动 clean/stash。

### 3. 导出主流程未使用三方基线

**问题**：`cmd_plan` 直接比当前本地和当前共享，分不出差异是谁造成的。远端独改时，会提议用旧的本地内容覆盖新的远端。

**已修**：主流程真的调 `compare()`，喂四个值：

```
B  = state["last_snapshot"]      上次同步时远端长什么样
S  = 当前共享 hash
L0 = mappings[sid]["native_hash"] 上次同步时本地长什么样
L  = 当前本地 hash
```

远端独改（PULL / NEW_PULL）和冲突（CONFLICT）都会被挑出来，**不进入本次推送**，提示先跑 `agent-sync pull`。

回归：RV03/RV04，四类场景（远端独改 / 本地独改 / 两边同改 / 两边冲突）全过。

### 4. 远端退役被直接跳过

**问题**：`plan_import` 遇到 `superseded`/`retracted` 直接 skip，本地已有的副本还是 `active`，还能被回忆起来；下次 export 又可能把旧状态推回去。

**已修**：

- 有映射且本地未改 → 跟随退役（本地 JSON 标 `superseded`/`retracted`，`_shared_md` 同步更新）
- 有映射但本地也改过 → 进 `conflict`，不盲目撤
- 从未导入过 → skip，不把正文复活

**保留 ID 不删文件**：删了 ID 就没了，别的端下次同步会把它重新建出来。

### 5. 普通本地记录导出时被统一标记为 observed

**问题**：不走 `_shared_md` 还原的路径，硬编码 `epistemic="observed"`、`depth="recent"`；缺 evidence 时还自动补一个假的 session 标记。

**已修**：

- `epistemic` 读 `shared_epistemic` → 本地 `epistemic` → 按证据判：有真实 evidence 算 `observed`，没有算 `legacy`
- `depth` 和 `applicability` 从本地保留
- **不再伪造 evidence**。缺就留空，让协议层报问题，而不是造一个 locator 骗过校验

这一条修的时候发现一个真问题：本地原生记录没有 `epistemic` 字段，一度全被降级成 `legacy`，导致 24 条里 2 条「内容不同」。改成看证据后恢复一致。

### 7. 导入验证失败仍推进映射、返回成功

**已修**：

- 只对 `result["verified"]` 里的推进映射，`broken`/`failed` 的留着下次重试
- `reindex_local()` 改成返回 bool，索引没建起来算失败
- 有失败或索引失败 → `return 8`（非零），不再宣告成功

### 8. 跨来源导入会改正文，再被误当本地修改导出

**问题**：`to_native` 往正文末尾追加「> 来源：xxx」，读回时 body 和原始不同，被当成「本地改过」，下次 export 又推回去 → 自己制造回声。

**已修**：

- 不再动正文，来源信息放独立字段（`shared_origin` / `shared_epistemic` / `shared_kind`）
- `_build_record` 优先用 `_shared_md` 还原，不再靠字段来回翻译
- 回归 RV08：模拟 `origin=codex-windows` 的记录转译往返，hash 一致

### 9. 发布前已推进同步状态

**问题**：commit/push **之前**就保存 mappings 和 last_snapshot。push 失败后状态已经前进，下次 plan 会显示一致，远端其实什么都没有。

**已修**：把状态推进移到 push 成功 + 回读远端 head 一致**之后**。push 失败时：

```
推送失败（本地已提交，未上传）
  补推：git -C <repo> push origin master
  映射和基线没有推进，重试不会产生重复记录。
```

回归 RV14：检查 `cmd_publish` 段内 `state["last_snapshot"]` 出现在 `git("push"` 之后。

### 10. 分发脚本的能力范围与 prompt 受管块

**已修**：

- 不再改任何 prompt 文件（`split(marker)` 那种截断风险消失了）
- 产物写进仓库的 `clients/<名字>/`，跟着 git 分发
- 每个客户端目录带一份 `README.md` 说明「那边的机器怎么装」

---

## 二、不改（和需求冲突）

### 1. 不做「自动检查 / 自动生成本地提案」

> **【历史决定，已被后续澄清覆盖】**
> 下面这段是第二轮时的判断。后来你明确说过「允许手改，但仍需定期检查」，
> 所以现在已经有 `agent-sync check`（只读定期检查，半月一次）。
> **仍然不做的**：自动批准、自动上传、自动改人格。
> 保留原段落是为了记录当时的取舍，不要当成当前状态来读。

**审查意见**：「完全手动、每周或半月一次」不能从「上传要确认」推导出来；自动检查 / 自动生成本地提案 / 授权内导入，可以和人工确认后上传并存。

**不改理由**：

这条是使用者明确选的，不是我从「上传要确认」推出来的。

原话倾向：**「这个可以比如说每个agent首先支持我手动拉起这个同步，其次保持每周甚至每半个月同步一次就行了」**。

技术上做的到自动，但故意不做。原因不在实现难度：

- 这台机器上的记忆里有大量私人内容。自动跑意味着「不看就上传」，或者「不看你就在动本地的记忆」。
- 使用者的判断是：宁可多按一次，也不要某个凌晨三点 cron 自作主张把东西推上去。

**这不是能力缺失，是刻意保留的人工闸口。** 将来想开自动，加个 cron 调 `agent-sync plan` 就行，不用改架构。

### 2. 不做完整状态机 / operation_id / 崩溃恢复

**审查意见**：需要 `draft/approved/committed/published/acknowledged` 五状态、唯一 `operation_id`、`before/after` 恢复材料、写入中断后的恢复闭环。

**部分做了**：状态推进顺序修了（第 9 条），写前备份有，读回校验有。

**不做完整状态机的理由**：

使用者对这类机制的明确态度：

> 「H01/02 不要这么麻烦了……**不要加太多会影响到我手动修订的限制**」
> 「要确保我通过 qq 或者其他终端，或者直接手动修改 md 文件都可以……**确保不会在整理记忆的时候弄得乱七八糟就行了**」

场景判断：**一个人在几台机器上、几天同步一次**。审查里那些崩溃恢复场景（进程在 `write_text` 中途被杀）在真实使用里几乎不会发生；真发生了，看一眼 `changes/` 和 `git log` 就能手工修。

完整状态机的代价：多出一整套需要维护和测试的代码，而且每次手动改记忆都要绕过它。**这跟「别妨碍我手改」是直接冲突的。**

**保留的最小保障**：写前备份到 `changes/merge-backup/`、写完读回校验、失败非零退出、状态在 push 之后才推进。出事能查、能重跑，不需要状态机。

### 3. 不做「用户确认」的强证明通道

**审查意见**：`--by qiulaoshi` 是可填写名字，不能证明是用户本人确认；要保留真实用户确认来源。

**不改理由**：

这条在技术上是对的：一个能改文件的人确实能伪造 `approval_hash`。

但**加一层证明也不解决问题**：真要伪造的人，同样能改本地 state 文件、能改脚本、能直接 `git push`。要真正拦住，得让确认动作发生在另一台机器上（扫码/短信/硬件密钥）——那是一整套身份系统，为一个单人自用的仓库。

**实际防线是权限，不是签名**：仓库私有、机器只有他碰、`publish` 需要显式敲命令。

所以保留 `--by` 作为**审计记录**（谁确认的，方便以后查），但不假装它是身份证明。文档里写清楚这一点，比堆一套看起来严格、实际同样能绕过的机制诚实。

### 4. 不做 RV01–RV16 的全部

**做了 8 组共 29 条检查**（`tests/test_rework.py`），覆盖：审批旁路、暂存范围、三方判定、退役传播、epistemic 保持、跨来源回声、验证失败不推进、推送失败不推进、分发脚本。

**没做的**：RV10（plan 后并发修改）、RV11（写入中断）、RV13（三条中一条失败的部分状态）、RV15（push 成功但回包丢失）。

理由同第 2 条：这些依赖完整状态机，且在单人手动场景里极少发生。**列在这里，不假装做了。**

---

## 三、测试证据

```bash
cd agent-sync
python3 tests/test_compare.py    # 11 项三方判定
python3 tests/test_dedupe.py     #  9 项去重筛选
python3 tests/test_rework.py     # 29 项返工回归
```

三个全过。`agent-sync verify` 26 条记录解析通过，`agent-sync status` 本地共享 24 条一致。

**边界**：这些是逻辑和源码层面的检查，没有在临时 bare 仓库上跑完整 git 往返。审查要求的「本地 bare Git 集成测试」没做，列在这里。

---

## 四、第二轮复核（基准 `f04d9f9`）的处理

复核提了四个 P1，**全部认，全部修了**。而且修的过程中又撞出两个新 bug。

### 4.1 [P1] 只有退役记录时 CLI 提前退出

**问题**：`cmd_pull` 只算 `new/updated/conflict`，`retire` 没算进去。`if n_new == 0 and n_upd == 0: return 0` 让「远端只撤了一条」这个最常见的退役场景永远走不到 apply。

**修**：`n_ret` 计入待应用动作、预览和空计划判断。

**测试**：RR01，建临时仓库 → 导入 active → 远端改 retracted → 真跑 `pull --apply` → 断言本地 status 变成 `retracted`。

### 4.2 [P1] 合并提案与发布器来源契约不兼容

**问题**：`merge` 生成的 keep 更新 `native_id=None`，`publish` 从 `snapshot_native()` 取不到 → 跳过；drop 的退役继续跑 → **只发布退役，合并内容丢了**。而且发布器把 drop 从 `superseded` 改成 `retracted`，语义也不对。

**修**：

- `Change` 加 `frozen_md` 字段，保存完整的发布内容。`publish` 优先用它，不再假设所有修改都有 native 来源。
- 加 `target_status` 字段，退役时写什么状态由提案决定，不在发布器临时换。
- **依赖组真原子**：组里任何一项失败，整组 `git checkout` 回滚，什么都不提交。

**测试**：RR02（第二个 clone 验证 keep 有独有内容、drop 是 `superseded`）、RR03（篡改 frozen_md → 整组拒绝、远端 head 不变）。

### 4.3 [P1] 索引失败后映射已推进

**问题**：映射在 `reindex_local()` 之前保存。下次 pull 看到文件一致，`new/updated` 为空就提前返回 0，索引永远不重建，而每条命令都报告成功。

**修**：持久化 `pending_reindex` 标记。索引失败时写入；后续 pull 即使零记录变化也先检查这个标记并补建，成功才清除。

**测试**：RR04，第一次索引失败 → 非零退出 + 有标记；第二次零变化 → 真的重建 + 标记清除。

### 4.4 [P1] 有来源 ≠ observed

**问题**：我上一轮把「有 evidence」当成「observed」。会话、日记、REM 反思都有真实 ref，但内容可能是猜测、创作、对事实的解释。引用只能回答「这句从哪来」，不能回答「这句是什么性质」。

**修**：缺 `epistemic` 一律标 `legacy`（待分类），绝不因为有 ref 就升成 observed。

**配套**：那两条本地原生记录（确实是我经历的）在本地 JSON 里补上显式 `epistemic: observed`，而不是改判定规则去迁就它们。

**还原路径也要扩**：`_shared_md` 还原以前只覆盖 title/body，手改 status / epistemic / applicability / evidence 会被原副本遮掉。现在这些都检测。

**测试**：RR05，有 evidence 缺 epistemic → 不是 observed；显式 `inferred` 保留。

### 4.5 修的时候撞出的两个新 bug

**一、`compare()` 不覆盖本地新增**

我把 `cmd_plan` 改成调 `compare()` 之后，「本地新增」这个分支丢了 —— `compare()` 只遍历共享侧出现过的 id，本地独有、还没推过的新记录根本不在里面。RR06 抓到的。

修：`cmd_plan` 里单独补一段，遍历 `native` 里没有映射的条目。

**二、evidence 语义比较误判**

4.4 加「手改检测」时，我把 `to_native` 正常转出的 evidence 当成了手改（本地是 `{ref, at}`，共享是 `{id, kind, locator, occurred_at}`，天然不同）。而且 `to_native` 会给没时间戳的证据补 `created_at`，读回必然不等。

修：只比 locator，不比 `occurred_at`。

### 4.6 定期检查

复核第 8 节说：允许手改，**但仍需定期检查**；定期检查可以只读。

加了 `agent-sync check`：

```bash
python3 -m agent_sync.cli check
```

- **只读**，不写任何东西
- 退出码：`0` 无差异 / `1` 有差异 / `2` 环境问题
- 输出待处理的差异、本地新增未推的条目、`pending_reindex` 状态
- **不生成提案、不导入、不推送**

配 cron 的时候可以接它，看到退出码 1 就来叫醒人。**没有安装任何调度任务**——那是需要你点头的事。

---

## 五、第三轮复核（基准 `cf91ec0`）

五项全认，全修。

### 5.1 [P1] snapshot_native 绕过 _build_record

**问题**：我给 `_build_record` 加了字段手改检测，但 `snapshot_native` 主循环里还有一段自己的还原分支，只覆盖 title/body 就 `continue`。主流程根本不走新代码。

**修**：删掉重复分支。`snapshot_native`、`snapshot_native_one`、hash 核对，三条路径全部只走 `_build_record` 一个函数。

**测试**：RR07。真导入一条 → 只手改 status/epistemic/applicability，标题正文不动 → 调真实 `snapshot_native` → 三项改动全部可见。

### 5.2 [P1] 依赖组失败用 git checkout 会抹掉用户手改

**问题**：最严重的一条。组里一项失败就对所有成员 `git checkout -- file`，这会把文件恢复到 index 版本 —— **用户此前手改但没提交的内容全没了**。而且循环还恢复本次根本没写过的文件。

**修**：整个写入改成两段：

1. **预检**（零写入）：算出每个变更最终要写什么，解析、hash、来源全部验一遍。组里任一项预检失败 → 整组不写，磁盘一个字节没动。
2. **条件恢复**：真写时先记下 `before` 字节。中途失败只恢复本次写过的，且要求当前内容仍等于本次写入的内容 —— 写完之后又被改过就保留现状，不覆盖。

**没有任何 `git checkout` / `reset` / `clean`。**

**测试**：RR08。先在 keep 文件上造一段用户手改 → 制造组内失败 → 断言失败退出、远端没变、**用户那句话还在**。

### 5.3 [P2] RR06 没验证补推后的基线收尾

**修**：加 `agent-sync reconcile <proposal_id>`。它把提案里的内容和远端实际内容逐条比，全对才推进映射和基线。

**扩展 RR06**：push 失败 → 补推 → `reconcile` → 断言新记录进了 mappings 和 last_snapshot → 再改本地同一条 → `plan` 出的是 **update 不是 create**。

### 5.4 [P2] agent-sync check 只看本地副本

**修**：加 `git ls-remote` 只读探测远端 HEAD。不 fetch、不改工作区、不写状态。远端比本地新就报「远端 master 已更新，先 git pull」。

**测试**：RR09。第二个 clone 往 bare remote 推一条 → 本地不 pull → `check` 退出码 1 并提示。

### 5.5 [P2] 默认 pull 在索引欠账时会写

**修**：不带 `--apply` 只报告欠账，`--apply` 才补建并清标记。

**测试**：RR10。索引失败后跑默认 `pull`，对比 state 字节——**完全相同**。再 `--apply` 才真正补建。

---

## 六、测试矩阵

| 文件 | 类型 | 内容 |
|---|---|---|
| `tests/test_compare.py` | 纯逻辑 | 11 项三方判定 |
| `tests/test_dedupe.py` | 纯逻辑 | 9 项去重筛选 |
| `tests/test_rework.py` | **源码与局部逻辑** | 29 项，大部分检查"代码里有没有这个调用/顺序" |
| `tests/test_commands.py` | **命令级端到端** | 53 项，临时 bare Git + 假原生目录 + 真跑 CLI |

**`test_rework.py` 不是集成测试**，复核说得对，我上一轮说错了。它是源码检查。真正的端到端在 `test_commands.py`。

RR01–RR06 全部通过，用的是临时目录和本地 bare remote，没碰真实记忆，没推真实远端。

```bash
cd agent-sync
python3 tests/test_commands.py     # 28 条，含建立临时 git 环境
```

---

## 七、第四轮复核（基准 `9cec36b`）

最后一项。

### 7.1 [P2] 远端查询失败被误报成「没有差异」

**问题**：`git ls-remote` 非零、空输出或抛异常时，只设了 `remote_note`，`remote_ahead` 仍是 False。如果本地三项（actionable / new_local / pending_reindex）都空，就会打印「没有差异」并 return 0。

**后果**：网络断了、认证失效、分支名错了，定期检查会一直报「一切正常」。这是最坏的一种失败——**把不知道说成没问题**。

**修**：

```python
remote_ok = False
# returncode != 0        → 记原因
# stdout 为空            → 分支不存在 / 远端是空的
# sha 解析不出           → 输出不可信
# 本地没有 master        → 记原因
# 全过了才 remote_ok = True

if not remote_ok:
    print(f"  ✗ 远端状态未知：{remote_fail_reason}")
    return 2
```

只有**确实完成远端检查**且没有待处理变化，才返回 0。

**测试**：RR11。先建立无差异状态 → 把 origin 指向不存在的路径 → 断言退出 2、明确说"远端状态未知"、**没有**输出"没有差异"、记忆文件和 state 都没被改 → 恢复 origin → 回到 0。

---

## 八、还有效的批评

审查第 11 项说的对：**`test_compare.py` 通过不能证明 `cmd_plan` 用了三方比较**。

这一轮补了 `test_commands.py`，真的建临时 bare Git、假原生目录、跑 CLI。RR06 就是端到端验证 plan→approve→publish→push 失败→补推的全链条。

**仍然没做的**：

- 没有在真实生产仓库上跑端到端（只跑临时目录）
- RV10（plan 后并发修改）、RV11（写入中断）—— 依赖完整状态机，按需求不做
- 没有装任何定时任务，`agent-sync check` 需要手动接 cron
