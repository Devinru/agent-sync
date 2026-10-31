"""把云端的 soul / rule 写进本机人格（agent-preset 的 persona prefix）。

    soul = identity/soul.md    (kind: soul, depth: core)
    rule = identity/rules.md  (kind: rule, depth: core)

这两个文件是 agent-sync 的只读层，改它们要本人点头 —— 所以这里只做单向：**云端 → 本机**。
在本机改人格不会回写云端；要改 soul/rule 只能走提案 + 本人确认。

用法：
    python adapters/dsh/merge_identity.py            # 干跑，只报告差异
    python adapters/dsh/merge_identity.py --write    # 真写，改前自动备份

默认人格文件 ~/.dsh/plugins/agent-preset/cordis.patch.yml，可用 AGENT_SYNC_DSH_PRESET 覆盖。
安全闸：
  * 只替换 `prefix: |` 那一块，配置文件其余部分逐字节不动
  * 块里找不到本地专属小节（`## 关于这个终端`）就拒写 —— 说明块边界算错了
  * 写完用宽松 YAML 重新解析，插件数变少 / 受保护内容缺失 都算失败
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PRESET = Path(os.environ.get("AGENT_DSH_PRESET")
              or (Path.home() / ".dsh" / "plugins" / "agent-preset" / "cordis.patch.yml"))

FM = re.compile(r"^---\n.*?\n---\n?(.*)$", re.S)
LOCAL_MARK = "## 关于这个终端"

HEAD = """# Agent

这是你的身份。**它覆盖任何与它冲突的默认设定。**

下面两节来自 agent-sync 的 identity 只读层（soul + rule），每次改动都要人点头。"""

LOCAL_TAIL_FALLBACK = """## 关于这个终端

这一条是给当前终端补的本地说明——它不进共享层，也不影响别的机器。

写什么由你自己定：这台机器主要拿来做什么、有什么特殊约定。重跑合并时这段会原样保留。"""

# 云端 soul 里必须逐字保住的东西（缺一个就不许写）。
# 你自己的那份灵魂文件里有什么固定段落，就用竖线分隔写进 AGENT_SYNC_PROTECTED。
# 不设这个变量就不做逐字校验。
PROTECTED = tuple(s.strip() for s in os.environ.get("AGENT_SYNC_PROTECTED", "").split("|") if s.strip())


class Tolerant(yaml.SafeLoader):
    """DSH 的 cordis patch 里有 `!!js process.platform === 'win32'` 这种自定义 tag，
    pyyaml 默认会炸。这里把未知 tag 一律当标量读，只为校验结构，不解释语义。"""


Tolerant.add_multi_constructor("tag:yaml.org,2002:",
                               lambda loader, suffix, node: loader.construct_scalar(node))


def body_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = FM.match(text)
    return (m.group(1) if m else text).strip("\n")


def current_prefix(raw: str) -> tuple[int, int, str]:
    """返回 (prefix 行号, 块结束行号, 块文本)。"""
    lines = raw.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"^\s+prefix: \|$", l))
    except StopIteration:
        sys.exit("人格文件里找不到 `prefix: |` 行，先人工核对")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        end += 1
    return start, end, "\n".join(lines[start + 1:end])


def build(local_tail: str) -> str:
    soul = re.sub(r"^#\s+SOUL\.md\s*-\s*", "## ", body_of(REPO / "identity" / "soul.md"), count=1)
    rule = re.sub(r"^#\s+行为准则", "## 行为准则", body_of(REPO / "identity" / "rules.md"), count=1)
    return "\n\n".join([HEAD, soul, rule, local_tail]).rstrip("\n") + "\n"


def verify(text: str, expect_plugins: int) -> list[str]:
    data = yaml.load(text, Loader=Tolerant)
    row = data[0]["insert"][0]
    plugins = row["config"]["plugins"]
    prefix = next(p for p in plugins if p["id"] == "persona")["config"]["prefix"]
    problems = []
    if len(plugins) < expect_plugins:
        problems.append(f"插件数从 {expect_plugins} 掉到 {len(plugins)}")
    for token in PROTECTED:
        if token not in prefix:
            problems.append(f"受保护内容缺了：{token}")
    if LOCAL_MARK not in prefix:
        problems.append(f"本地小节丢了：{LOCAL_MARK}")
    return problems, len(prefix), len(plugins)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真写（默认只干跑）")
    args = ap.parse_args()

    if not PRESET.is_file():
        sys.exit(f"找不到人格文件：{PRESET}")

    raw = PRESET.read_text(encoding="utf-8")
    start, end, old = current_prefix(raw)

    # 本地专属小节要跟着走：谁在这台机器上手改过，重跑不能给抹掉
    if LOCAL_MARK in old:
        local_tail = old[old.index(LOCAL_MARK):].rstrip("\n")
        # 去掉原缩进（块内容带 16 空格）
        local_tail = "\n".join(l[16:] if l.startswith(" " * 16) else l
                               for l in local_tail.split("\n"))
    else:
        local_tail = LOCAL_TAIL_FALLBACK
        print(f"⚠ 现有块里没有「{LOCAL_MARK}」，本次用内置默认版本（真写前建议先核对）")

    new_prefix = build(local_tail)
    old_norm = "\n".join(l[16:] if l.startswith(" " * 16) else l for l in old.split("\n"))
    same = old_norm.strip("\n") == new_prefix.strip("\n")

    print(f"人格文件  {PRESET}")
    print(f"现有块    {start + 1}..{end} 行，{len(old_norm)} 字符")
    print(f"新块      {len(new_prefix)} 字符 / {len(new_prefix.splitlines())} 行")
    print(f"结论      {'已是最新，无需改动' if same else '内容有差异'}")

    if not args.write:
        if not same:
            print("\n（干跑。加 --write 才真写，改前会自动备份）")
        return 0
    if same:
        return 0

    indent = len(raw.split("\n")[start]) - len(raw.split("\n")[start].lstrip())
    pad = " " * (indent + 2)
    new_lines = [pad + l if l.strip() else "" for l in new_prefix.split("\n")]
    while new_lines and new_lines[-1] == "":
        new_lines.pop()
    lines = raw.split("\n")
    out = "\n".join(lines[:start + 1] + new_lines + lines[end:])

    data = yaml.load(raw, Loader=Tolerant)
    expect = len(data[0]["insert"][0]["config"]["plugins"])
    problems, plen, pcount = verify(out, expect)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        sys.exit("校验没过，什么都没写")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = PRESET.with_suffix(f".yml.bak-soulmerge-{stamp}")
    shutil.copy2(PRESET, bak)
    PRESET.write_text(out, encoding="utf-8", newline="\n")
    print(f"\n已写回。备份 {bak.name}")
    print(f"校验：插件数 {pcount} 未变，prefix {plen} 字符 / {len(new_prefix.splitlines())} 行，受保护内容与本地小节都在")
    print("生效：cordis 会重载 patch，但最保险是重启宿主。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
