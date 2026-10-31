"""索引重建脚本（给 cli.py 的 AGENT_SYNC_RECORDS_SCRIPT 钩子用）。

cli.py 是 shell 起它的：`python3 <脚本> reindex`。用 run.py 跑同步时
不会走这条路（run.py 直接挂了进程内函数），这个文件是给别处 / 定时任务
/ 换一台机器照 guide 重建时用的。

    python adapters/dsh/reindex.py reindex
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("dsh_adapter", HERE / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "reindex"
    dsh = _load()
    if cmd == "reindex":
        ok = dsh.reindex_local()
        print("索引已同步" if ok else "索引同步失败")
        return 0 if ok else 1
    if cmd == "inspect":
        import json
        print(json.dumps(dsh.inspect(), ensure_ascii=False, indent=2))
        return 0
    print(f"不认识的命令：{cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
