#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 agent-sync 的共享记忆分发给各客户端。

Hermes 有自己的适配器（写 records + 重建索引），不用这个。
这个脚本只生成静态兼容分发包。Codex 已有原生记忆，双向接入见 guides/codex.md。

做法：
    共享仓库 → 转成每个端能读的 markdown → 写进各自的 memory 目录
    prompt 文件里加一句「记忆在 memory/，按需读」

用法：
    python3 sync-clients.py status       看各端当前状态
    python3 sync-clients.py export dsh   导出给 dsh
    python3 sync-clients.py export all
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path.home() / "agent-sync"
TZ = timezone(timedelta(hours=8))

# 产物写进仓库，跟着 git 分发给其他机器。
# 不要写进本机的 ~/.dsh 或 ~/.codex —— 那些是本机的，跟其他机器无关。
CLIENTS_DIR = REPO / "clients"

CLIENTS = {
    "dsh": {"label": "DSH"},
    "codex": {"label": "Codex"},
}

# 各端在本机的实际记忆目录（只用于 status 提示，export 不写这里）
LOCAL_HINTS = {
    "dsh": Path.home() / ".dsh",
    "codex": Path.home() / ".codex",
}

# 哪些 kind 该给对方看：
# soul → 进 prompt，不单独存
# rule → 同上
# 其余 → 进 memory 目录
PROMPT_KINDS = {"soul", "rule"}
MEMORY_KINDS = {"user", "fact", "lesson", "episode", "topic", "project",
                "reflection", "legacy_bundle"}


def load_records() -> list[dict]:
    """读共享仓库全部记录，返回 dict 列表。"""
    out = []
    for d in (REPO / "memory" / "records", REPO / "identity"):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            m = re.match(r"---\n(.*?)\n---\n\n?(.*)$", text, re.S)
            if not m:
                continue
            header, body = m.group(1), m.group(2)
            rec = {"_path": str(p), "_body": body.strip()}
            for line in header.splitlines():
                mm = re.match(r'^(\w+):\s*"?(.*?)"?\s*$', line)
                if mm:
                    rec[mm.group(1)] = mm.group(2)
            if rec.get("status") in ("superseded", "retracted"):
                continue
            out.append(rec)
    return out


def render_memory_index(records: list[dict], label: str) -> str:
    """给人/agent 看的索引。"""
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Agent 记忆 · {label}",
        "",
        f"> 自动生成于 {now}。来源：agent-sync 共享仓库。",
        "> **不要手改这个文件**——改了下次同步会被覆盖。",
        "> 要改记忆，在对话里说，由 agent 处理。",
        "",
        f"共 {len(records)} 条。",
        "",
        "## 怎么用",
        "",
        "1. 这份索引是概览，每条记忆的完整内容在 `memory/records/` 里",
        "2. 需要细节时读对应文件",
        "3. 涉及「他是什么人」的问题，先读 `01-user.md`",
        "4. 涉及「以前怎么处理过」的问题，读 `02-lessons.md`",
        "",
        "## 索引",
        "",
    ]
    by_kind: dict[str, list] = {}
    for r in records:
        by_kind.setdefault(r.get("kind", "fact"), []).append(r)

    for kind in ("user", "fact", "lesson", "topic", "project", "episode",
                 "reflection", "legacy_bundle"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"### {kind} · {len(items)} 条")
        lines.append("")
        for r in items:
            title = r.get("title", "")[:50]
            rid = r.get("id", "")[:8]
            lines.append(f"- `{rid}` {title}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_prompt_block(label: str) -> str:
    """写进 prompt 文件的那一段。要短，不能挤占人格。"""
    return f"""
## 记忆（agent-sync 共享）

你的长期记忆在 `~/.{label.lower()}/memory/`，由 agent-sync 仓库同步过来。

- `memory/index.md` —— 全部记忆的索引，先读这个
- `memory/01-user.md` —— 用户是什么人
- `memory/02-lessons.md` —— 以前踩过的坑
- `memory/03-facts.md` —— 原子事实
- `memory/records/` —— 每条记忆的完整内容

**规则**：

1. 涉及他的身份、偏好、家里情况 → 先读 `01-user.md`，不要凭印象
2. 涉及「以前是不是出过这类问题」→ 读 `02-lessons.md`
3. 这些文件**只读**。要改记忆，在对话里提，由 agent 或用户决定
4. 不确定就去读，别编
"""


def render_install_note(label: str, client: str) -> str:
    """给那台机器上的人看：拉下来之后怎么装。"""
    if client == "codex":
        return """# Codex 接入说明

Codex 已有本地长期记忆。双向转译及本机安装请读 [guides/codex.md](../../guides/codex.md)。

本目录仅是旧版静态兼容包，不是 Codex 受管记忆；不要用它覆盖 `~/.codex/memories/` 或 SQLite。

在仓库根先运行 `python -X utf8 adapters/codex/cli.py inspect`，再按指南生成计划、取得本机授权并导入。
导入后用 `verify` 和新会话实际回忆验收；首次人格授权不能从其他机器继承。

更新走 `adapters/codex/cli.py fetch`、`plan`、经确认后的 `apply`。导出使用 `translate` 生成本地候选，人工审阅后再发布。
"""
    return f"""# {label} 接入说明

这个是给**另一台机器**用的。仓库拉下来之后，把 `memory/` 拷到你机器上
{label} 会读的位置，然后在它的 prompt 里加一段话。

## 拷到哪

看你机器上 {label} 的配置在哪：

- 如果它读 `~/.{client}/`（Linux/Mac）或 `C:\\Users\\<你>\\.{client}\\`（Windows）
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
"""


def export(client: str) -> int:
    cfg = CLIENTS[client]
    mem = CLIENTS_DIR / client / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    recs_dir = mem / "records"
    recs_dir.mkdir(exist_ok=True)

    records = load_records()
    if not records:
        print("共享仓库里没有记录", file=sys.stderr)
        return 2

    # 1. 索引
    (mem / "index.md").write_text(render_memory_index(records, cfg["label"]), encoding="utf-8")

    # 2. 分类汇总
    groups = {"user": "01-user.md", "lesson": "02-lessons.md", "fact": "03-facts.md"}
    buckets: dict[str, list] = {k: [] for k in groups}
    for r in records:
        k = r.get("kind", "fact")
        if k in groups:
            buckets[k].append(r)

    for kind, fname in groups.items():
        items = buckets[kind]
        parts = [f"# {fname.replace('.md','')} （{len(items)} 条）\n"]
        for r in items:
            parts.append(f"\n## {r.get('title','')}\n")
            parts.append(f"`{r.get('id','')[:8]}` · {r.get('epistemic','?')}\n")
            parts.append(r["_body"] + "\n")
        (mem / fname).write_text("".join(parts), encoding="utf-8")

    # 3. 每条一个文件（完整副本）
    for r in records:
        rid = r.get("id", "")
        if not rid:
            continue
        (recs_dir / f"{rid}.md").write_text(
            f"# {r.get('title','')}\n\n"
            f"- kind: {r.get('kind','')}\n- epistemic: {r.get('epistemic','')}\n"
            f"- updated: {r.get('updated_at','')}\n\n{r['_body']}\n",
            encoding="utf-8")

    # 4. 接入说明（给人看，不是给 agent）
    (CLIENTS_DIR / client / "README.md").write_text(
        render_install_note(cfg["label"], client), encoding="utf-8")

    print(f"{cfg['label']}: {len(records)} 条 → {mem.relative_to(REPO)}")
    return 0


def status() -> int:
    print(f"共享仓库: {REPO}")
    records = load_records()
    print(f"  可同步记录 {len(records)} 条\n")
    for name, cfg in CLIENTS.items():
        mem = CLIENTS_DIR / name / "memory"
        n = len(list((mem / "records").glob("*.md"))) if mem.exists() else 0
        has_idx = (mem / "index.md").exists()
        has_readme = (CLIENTS_DIR / name / "README.md").exists()
        print(f"{cfg['label']:6s}  已导出 {n:3d} 条  索引 {'有' if has_idx else '无'}  "
              f"接入说明 {'有' if has_readme else '无'}")
    print(f"\n产物在 {CLIENTS_DIR.relative_to(REPO)}/，跟着 git 走")
    print("另一台机器拉下来后照着 clients/<名字>/README.md 装")
    return 0


def main(argv) -> int:
    if len(argv) < 2 or argv[1] == "status":
        return status()
    cmd = argv[1]
    if cmd == "export":
        targets = argv[2:] or ["all"]
        if "all" in targets:
            targets = list(CLIENTS)
        rc = 0
        for t in targets:
            if t not in CLIENTS:
                print(f"不认识的端: {t}（可选 {', '.join(CLIENTS)}）", file=sys.stderr)
                rc = 2
                continue
            rc |= export(t)
        return rc
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
