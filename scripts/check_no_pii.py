#!/usr/bin/env python3
"""提交前扫描个人信息 / 凭据。

用法：
    python3 scripts/check_no_pii.py            # 扫暂存区（pre-commit 用）
    python3 scripts/check_no_pii.py --all      # 扫全部已跟踪文件
    python3 scripts/check_no_pii.py <文件...>  # 指定文件

规则两部分：
  1. 内置通用规则（电话、邮箱、密钥、绝对路径…）—— 对谁都适用
  2. 项目本地规则 `.pii-patterns`（可选，不进 git）—— 写你自己的私有关键词

退出码：0 干净 / 1 命中
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 内置通用规则。每项：(名称, 正则)
GENERIC = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("疑似密钥", re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b")),
    ("Bearer 令牌", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{24,}")),
    ("非空 api_key", re.compile(r"""api[_-]?key\s*[:=]\s*["'][^"'\s]{8,}["']""", re.I)),
]

# 本机绝对路径里带真实用户名的（形如 /home/<用户名>/…），换成你自己的再扩规则
HOME_PATH = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)/")

# 这些邮箱是公开的，不算个人信息
EMAIL_OK = {
    "git@github.com",
    "noreply@anthropic.com",
    "noreply@github.com",
    "example@example.com",
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")

# 允许出现的绝对路径用户名（CI、容器里的通用名）
HOME_OK = {"runner", "user", "ubuntu", "root", "app", "node", "vscode"}

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf"}


def load_local_patterns() -> list[tuple[str, re.Pattern]]:
    """读 .pii-patterns：一行一个正则，# 开头是注释。"""
    f = ROOT / ".pii-patterns"
    if not f.exists():
        return []
    out = []
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            out.append((f"本地规则 L{i}", re.compile(s)))
        except re.error as e:
            print(f".pii-patterns 第 {i} 行正则无效: {e}", file=sys.stderr)
    return out


def staged_files() -> list[Path]:
    r = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                       cwd=ROOT, capture_output=True, text=True)
    return [ROOT / n for n in r.stdout.splitlines() if n.strip()]


def tracked_files() -> list[Path]:
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [ROOT / n for n in r.stdout.splitlines() if n.strip()]


def scan(files: list[Path], rules) -> int:
    hits = 0
    for p in files:
        if not p.is_file() or p.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = p.relative_to(ROOT)
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, rx in rules:
                m = rx.search(line)
                if not m:
                    continue
                frag = m.group(0)
                print(f"  ✗ {rel}:{lineno}  [{name}]  {frag[:40]}")
                hits += 1
                break
            else:
                # 邮箱单独判（放行公开地址）
                for m in EMAIL.finditer(line):
                    if m.group(0) not in EMAIL_OK:
                        print(f"  ✗ {rel}:{lineno}  [邮箱]  {m.group(0)}")
                        hits += 1
                # 绝对路径里的用户名
                for m in HOME_PATH.finditer(line):
                    u = m.group(1)
                    if u not in HOME_OK:
                        print(f"  ✗ {rel}:{lineno}  [本机路径用户名]  {u}")
                        hits += 1
    return hits


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--all":
        files = tracked_files()
        scope = "全部已跟踪文件"
    elif args:
        files = [Path(a) for a in args]
        scope = f"{len(files)} 个指定文件"
    else:
        files = staged_files()
        scope = "暂存区"

    rules = list(GENERIC)
    local = load_local_patterns()
    rules.extend(local)

    print(f"扫描 {scope}（{len(files)} 个文件，{len(rules)} 条规则）")
    if not files:
        print("没有要扫的文件")
        return 0

    hits = scan(files, rules)
    if hits:
        print(f"\n发现 {hits} 处疑似个人信息/凭据，已阻止。")
        print("确认是误报就改规则或改写内容；不要用 --no-verify 绕过。")
        return 1

    print("干净。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
