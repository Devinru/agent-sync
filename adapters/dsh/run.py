"""用 DSH 适配器跑 sync-core 的 CLI。

sync-core 的 `load_adapter()` 里写死了 Hermes 的路径（cli.py:81-90），
这是共用的东西，我不去改它 —— 改公共协议得先提案。这里在进程内把那两个
名字换掉，效果一样，一个字节都不碰共享文件：

    python adapters/dsh/run.py status
    python adapters/dsh/run.py plan
    python adapters/dsh/run.py pull --apply

跑之前先设好环境变量（guide 里有完整清单）：
    AGENT_SYNC_REPO   默认就是本仓库根目录
    AGENT_SYNC_VAULT   默认 D:\\AgentSync\\agent-memory
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

sys.path.insert(0, str(REPO / "sync-core"))

VAULT = Path(os.environ.get("AGENT_SYNC_VAULT") or r"D:\AgentSync\agent-memory")

# 映射表放本机 vault 里 —— 别写到 Hermes 的 ~/.hermes/memory/sync-state.json。
# cli.py 的 STATE 是模块级常量，且 load_state() 的默认参数在 def 时就绑死了，
# 事后改 cli.STATE 没用，只能在这两行之前用环境变量定下来。
os.environ.setdefault("AGENT_SYNC_STATE", str(VAULT / ".dsh-memory" / "agent-sync-state.json"))
os.environ.setdefault("AGENT_SYNC_RECORDS_SCRIPT", str(HERE / "reindex.py"))

from agent_sync import cli  # noqa: E402


def _load_adapter():
    spec = importlib.util.spec_from_file_location("dsh_adapter", HERE / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dsh = _load_adapter()

# 三个缝合点，全在进程内：
cli.load_adapter = lambda: dsh          # 换适配器
cli.CLIENT = dsh.CLIENT                 # 提案署名 dsh-windows，不是 hermes-linux
cli.reindex_local = dsh.reindex_local   # 索引走宿主 RPC，不经 shell 的 python3

if __name__ == "__main__":
    sys.exit(cli.main(sys.argv[1:]))
