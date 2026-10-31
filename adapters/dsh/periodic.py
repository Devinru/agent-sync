"""定期同步：把云端拉进本地 vault，再把本地新记忆整理成提案。

只做两件事，**不推远端** —— publish 要用户点头，这条永不动摇。

每三周由 Windows 计划任务跑一次（任务名 `AgentSync-DSH-Periodic`）：
    python adapters/dsh/periodic.py

也可以手动跑。退出码：0 一切正常；8 导入有失败；其它非 0 = 拉取/提案出错。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VAULT = Path(os.environ.get("AGENT_SYNC_VAULT") or r"D:\AgentSync\agent-memory")
LOG = VAULT / ".dsh-memory" / "agent-sync-periodic.log"
PY = sys.executable or "python"


def run(*args: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", GIT_TERMINAL_PROMPT="0")
    p = subprocess.run([PY, str(HERE / "run.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(HERE.parents[1]))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def pending_proposals() -> list[str]:
    """changes/ 里还挂着、没人批的提案。有的话就不再生成新的 —— 
    同一批本地新增每轮重复生成，会把 changes/ 塞成一堆同义提案。"""
    out = []
    for p in sorted((REPO / "changes").glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("approval") or {}).get("status") == "pending":
            out.append(p.stem)
    return out


def main() -> int:
    started = datetime.now().astimezone()
    chunks = [f"\n===== {started.isoformat(timespec='seconds')} 定期同步 =====\n"]
    rc_pull, out_pull = run("pull", "--apply")
    chunks.append("--- pull --apply ---\n" + out_pull)

    waiting = pending_proposals()
    if waiting:
        chunks.append("--- plan：跳过 ---\n已有未处理的提案，等用户看完再生成新的："
                      + "、".join(waiting) + "\n")
        rc_plan = 0
    else:
        rc_plan, out_plan = run("plan")
        chunks.append("--- plan（只生成提案，不推）---\n" + out_plan)

    rc_status, out_status = run("status")
    chunks.append("--- status ---\n" + out_status)
    tail = f"\n== 结果：pull={rc_pull} plan={rc_plan} status={rc_status}\n"
    chunks.append(tail)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(chunks))
    print("".join(chunks))
    print(f"日志：{LOG}")

    if rc_pull != 0:
        return rc_pull
    if rc_plan != 0:
        return rc_plan
    return 0


if __name__ == "__main__":
    sys.exit(main())
