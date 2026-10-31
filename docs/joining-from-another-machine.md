# 新机器接入

**所有机器统一走 GitHub 私有仓库同步。** 没有局域网、没有中转、没有例外。

```
机器 A（主力）       机器 B（办公室）      机器 C（笔记本）
     │                    │                    │
     └────────────┬───────┴────────────────────┘
                  ↓
        github.com/your-org/agent-sync（私有）
```

每台机器自己 clone、自己 pull、自己 push。**谁先推谁先到，后推的人会撞冲突**——所以推送前必须 pull，见下面「冲突」。

---

## 一、认证（每台机器做一次）

私有仓库，每台机器都要有凭证。**推荐 SSH**，一次配好永久有效。

### 1. 生成密钥

```bash
ssh-keygen -t ed25519 -C "office-pc" -f ~/.ssh/id_ed25519
# 提示密码时可以直接回车（不设密码），也可以设一个
```

### 2. 把公钥加到 GitHub

```bash
cat ~/.ssh/id_ed25519.pub
```

复制输出，打开 `https://github.com/settings/ssh/new`，Title 随便写（比如「办公室电脑」），粘贴，保存。

### 3. 验证

```bash
ssh -T git@github.com
# Hi your-org! You've successfully authenticated...
```

看到 `Hi your-org!` 就成了。

---

## 二、克隆

```bash
git clone git@github.com:your-org/agent-sync.git ~/agent-sync
cd ~/agent-sync
```

Windows 上路径自己换，比如 `C:\Users\<你>\agent-sync`。

### 验证

```bash
git log --oneline -3
cat protocol.yaml | head -3
```

能看到提交记录和 `protocol_version: "2.0"` 就对了。

---

## 三、跑自检

```bash
cd ~/agent-sync

python3 -c "
import sys; sys.path.insert(0,'sync-core')
from pathlib import Path
from agent_sync.protocol import Record
n=0
for p in Path('memory/records').glob('*.md'):
    Record.load(p); n+=1
print(f'{n} 条记录解析正常')"

python3 tests/test_compare.py
python3 tests/test_dedupe.py
```

三个都过，说明协议层没问题，可以开始接。

---

## 四、让这台机器的 agent 接上

### 4.1 先回答三个问题

**这台机器上的 agent 是什么？** Codex / DSH / Claude Code / 别的？

**它的记忆存在哪？** 三种可能：

| 情况 | 特征 |
|---|---|
| **有结构化记忆** | 有自己的目录 / SQLite / 检索接口 |
| **只有一段 prompt** | 一个 `AGENTS.md` / `CLAUDE.md` / 配置文件，没有别的 |
| **完全无状态** | 每次会话从零开始，什么都不记 |

**它怎么读记忆？** 自动注入上下文 / 需要显式读文件 / 有检索工具？

### 4.2 按情况选做法

**情况 A：有结构化记忆**

照着 `protocol/adapters.md` 实现六个接口，写一份 `guides/<你的机器名>.md`。

**情况 B：只有一段 prompt**

在这台机器上跑（路径改成实际值）：

```bash
# 在主力机上改好 sync-clients.py 的 CLIENTS 字典，然后：
python3 sync-clients.py export <机器名>
```

或者在那边直接写一段 prompt：

```markdown
## 记忆

你的长期记忆在 `~/agent-sync/memory/records/`，由 GitHub 同步。

- `memory/index.md` —— 全部记忆的索引，先读这个
- `memory/records/<id>.md` —— 每条完整内容

规则：
1. 涉及他的身份、偏好、家里情况 → 先查索引再读，不要凭印象
2. 涉及「以前是不是出过这类问题」→ 查 kind: lesson 的
3. 这些文件只读。要改记忆，在对话里提，由 agent 或用户决定
4. 不确定就去读，别编
```

**情况 C：完全无状态**

至少让它能读：

```bash
mkdir -p ~/.agent-memory
cp ~/agent-sync/identity/soul.md ~/.agent-memory/
cp ~/agent-sync/memory/index.md ~/.agent-memory/
```

然后在它的启动配置里加一句：**「你的记忆在 `~/.agent-memory/`，涉及他的事先读。」**

---

## 五、日常同步

### 开工前

```bash
cd ~/agent-sync && git pull --ff-only
```

### 收工前（如果你这台也写了料）

```bash
cd ~/agent-sync
git add -A && git commit -m "sync from <机器名>"
git push
```

### 或者用脚本

```bash
# 拉
agent-sync pull --apply          # Hermes 用这个（会转译进本地 records）
# 或者纯 git
git pull --ff-only
```

### 频率

**不用每天。** 一周或半个月一次都够，想起来就同步一下。

---

## 六、冲突怎么办

**多台机器同时改同一个文件**，`git push` 会被拒。

```bash
git pull --rebase origin master
# 有冲突就手工解，然后
git add <解决的文件> && git rebase --continue
```

**但更可能的是逻辑冲突，不是文本冲突**：

- A 机器加了一条「他在城市 A」
- B 机器加了一条「他住在城市 A 的某区」
- 文本上不冲突（不同文件），但**语义重复**

这种情况 git 管不了，要靠 `agent-sync dedupe`：

```bash
cd ~/agent-sync/sync-core
python3 -m agent_sync.cli dedupe
```

它会扫出相似候选，然后判、然后 merge。**判重规则见 README 的「同步规则」一节。**

---

## 七、三条硬规则

### 1. 推送前一定先 pull

```bash
git pull --ff-only && git push
```

**不 pull 直接 push，撞上了要花时间解冲突。**

### 2. soul / rule 不许自动改

`identity/soul.md` 和 `identity/rules.md` 是人格的根。

- 本机（写着 `sync_policy: local-wins` 的那台）改了 → 可以覆盖
- **其他机器改了 → 只提案，不直接推**

理由：人格是「这个 agent 是谁」，不该被别的机器悄悄改。

### 3. 转译必须可逆

从共享格式转成本地格式，再转回来，**hash 必须一样**。

做不到就存一份原始共享记录（见 `adapters/hermes/adapter.py` 的 `_shared_md`），读的时候直接还原。

**不这么做会回声**：第一次导入 22 条，第二次 push 又要推 22 条，然后无限来回。

---

## 八、这台机器怎么确认接对了

```bash
# 1. 能拉到最新
cd ~/agent-sync && git pull --ff-only

# 2. 记录能解析
cd sync-core && python3 -m agent_sync.cli verify

# 3. agent 真的读到了
```

第三项得实测：问那个 agent 一句「我住在哪」。

- 答「城市 A」→ 接对了
- 答城市 B → 读错了源（城市 B 是 agent 自己的位置，不是用户的）
- 答不上来 → 没读到，检查 prompt 路径

---

## 附：各机器的接入状态

| 机器 | 系统 | agent | 状态 |
|---|---|---|---|
| NAS VM（主力） | Linux | Hermes | ✅ 已接入，有完整适配器 |
| 办公室电脑 | ? | ? | ⬜ 待接入 |
| 笔记本 | ? | ? | ⬜ 待接入 |
| 4070ti 那台 | Windows | DSH | ⚠️ GitHub git 流量被 reset，需走代理或另想办法 |

**最后那台要特别注意**：实测 `github.com` 的 TCP 443 能建连，但 TLS 一发数据就被 reset。要么让它走自己的代理（`127.0.0.1:7897`），要么这台放弃直接同步，改由主力机代它收发。
