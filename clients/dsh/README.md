# DSH 接入说明

这个是给**另一台机器**用的。仓库拉下来之后，把 `memory/` 拷到你机器上
DSH 会读的位置，然后在它的 prompt 里加一段话。

## 拷到哪

看你机器上 DSH 的配置在哪：

- 如果它读 `~/.dsh/`（Linux/Mac）或 `C:\Users\<你>\.dsh\`（Windows）
  → 把整个 `memory/` 拷进去
- 如果它只读一个 prompt 文件 → 拷 `memory/index.md` 和 `memory/01-user.md` 到任意位置，
  然后在 prompt 里写清路径

## prompt 里加什么

```markdown
## 记忆

你的长期记忆在 `<你放 memory/ 的路径>`，由 GitHub 私有仓库同步。

- `index.md` —— 全部记忆的索引，先读这个
- `01-user.md` —— 他是什么人
- `records/<id>.md` —— 每条完整内容

规则：
1. 涉及他的身份、偏好、家里情况 → 先读，不要凭印象
2. 涉及「以前是不是出过这类问题」→ 查 kind: lesson 的
3. 只读。要改记忆，在对话里提
4. 不确定就去读，别编
```

## 更新

主力机重新导出 → push → 这边 `git pull` → 重新拷一遍 `memory/`。

**别手改 `memory/` 里的文件**，下次覆盖就没了。
