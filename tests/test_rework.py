"""审查返工的回归测试 —— 局部逻辑与源码检查。

**这份是源码级检查，不是端到端测试。**
大部分断言是「某段代码里有没有这个调用/这个顺序」，
少数（RV01 的 Proposal 行为、RV08 的转译往返）是真的跑函数。

真正的命令级端到端测试在 tests/test_commands.py（RR01–RR06），
那份才建临时 bare Git、真的跑 CLI。

跑：python3 tests/test_rework.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sync-core"))

from agent_sync.compare import Action, Mapping, compare  # noqa: E402
from agent_sync.proposal import Change, Proposal  # noqa: E402
from agent_sync.protocol import Record  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f"  {detail}" if detail and not cond else ""))


def mkrec(rid=None, kind="fact", title="测试", body="这是一条测试记录的内容，长度足够。",
          epistemic="observed", status="active"):
    return Record(
        id=rid or str(uuid.uuid4()), kind=kind, depth="recent", status=status,
        title=title, origin="test", updated_by="test",
        created_at="2026-10-27T10:00:00+08:00",
        updated_at="2026-10-27T10:00:00+08:00",
        epistemic=epistemic,
        evidence=[{"id": "ev-1", "kind": "session", "locator": "local-ref:x"}],
        body=body,
    )


# ---------- RV01: 没有确认就不能发布 ----------

def rv01():
    print("\nRV01 审批旁路")
    prop = Proposal(
        proposal_id=str(uuid.uuid4()), created_at="2026-10-27T10:00:00+08:00",
        client="test", base_commit="deadbeef", target_ref="master",
        changes=[Change(shared_id=str(uuid.uuid4()), action="create", kind="fact",
                        title="x", content_hash="h1", reason="test")],
    )
    ok, why = prop.check_approval()
    check("未确认时 check_approval 必须失败", not ok, why)

    prop.approve("tester")
    ok2, _ = prop.check_approval()
    check("确认后通过", ok2)

    prop.changes[0].title = "改过了"
    ok3, why3 = prop.check_approval()
    check("确认后改内容 → 回执失效", not ok3, why3)

    # publish 源码里不能再有自动签收
    src = (ROOT / "sync-core" / "agent_sync" / "cli.py").read_text(encoding="utf-8")
    check("publish 里没有 prop.approve 自动签收",
          "if not ok and args.yes" not in src)
    check("merge 没有 --publish 直推参数",
          '"--publish"' not in src)
    check("没有 git add -A",
          'git("add", "-A")' not in src)


# ---------- RV03/RV04: plan 用三方比较 ----------

def rv03():
    print("\nRV03/RV04 三方判定（真实 compare 路径）")
    sid = str(uuid.uuid4())
    nid = str(uuid.uuid4())
    m = {sid: Mapping(sid, nid, "base-h", "base-h")}

    # 远端独改、本地没动 → 必须 PULL，不能 PUSH
    d1 = compare({sid: "remote-h"}, {sid: "base-h"}, {nid: "base-h"}, m)
    act1 = d1[0].action
    check("远端独改 → pull（不是 push）", act1 == Action.PULL, act1)

    # 本地独改、远端没动 → PUSH
    d2 = compare({sid: "base-h"}, {sid: "base-h"}, {nid: "local-h"}, m)
    check("本地独改 → push", d2[0].action == Action.PUSH, d2[0].action)

    # 两边改不同 → CONFLICT
    d3 = compare({sid: "remote-h"}, {sid: "base-h"}, {nid: "local-h"}, m)
    check("两边都改 → conflict", d3[0].action == Action.CONFLICT, d3[0].action)

    # 两边改成一样 → NOOP
    d4 = compare({sid: "same-h"}, {sid: "base-h"}, {nid: "same-h"}, m)
    check("两边改成一样 → noop", d4[0].action == Action.NOOP, d4[0].action)

    # cli.py 主流程真的调了 compare
    src = (ROOT / "sync-core" / "agent_sync" / "cli.py").read_text(encoding="utf-8")
    check("cmd_plan 里调用 compare()", "diffs = compare(" in src)
    check("用共享基线 last_snapshot", 'state.get("last_snapshot")' in src
          or "last_snapshot" in src)


# ---------- RV05: 退役传播 ----------

def rv05():
    print("\nRV05 退役传播")
    src = (ROOT / "adapters" / "hermes" / "adapter.py").read_text(encoding="utf-8")
    check("plan_import 有 retire 分支", "retire.append" in src)
    check("apply_import 处理退役", 'd["status"] = rec.status' in src)
    check("退役保留 ID（不删文件）", 'target.exists():' in src or "retired.append" in src)


# ---------- RV06: epistemic 不硬编码 ----------

def rv06():
    print("\nRV06 epistemic 保持")
    src = (ROOT / "adapters" / "hermes" / "adapter.py").read_text(encoding="utf-8")
    check("不再硬编码 observed", 'epistemic="observed",' not in src)
    check("读 shared_epistemic", 'd.get("shared_epistemic")' in src)
    check("保留 applicability", 'applicability=applicability' in src)
    check("不缺证据时不伪造 session 标记",
          'ev = [{"id": f"ev-{native_id[:8]}", "kind": "session"' not in src)


# ---------- RV08: 跨来源导入不回声 ----------

def rv08():
    print("\nRV08 跨来源导入不回声")
    src = (ROOT / "adapters" / "hermes" / "adapter.py").read_text(encoding="utf-8")
    check("不再往正文追加来源说明", '> 来源：{rec.origin}' not in src)
    check("原始共享记录存进 _shared_md", '"_shared_md": rec.to_markdown()' in src)

    # 真的走一遍：共享记录 → to_native → 读回 → hash 应该不变
    sys.path.insert(0, str(ROOT / "adapters" / "hermes"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("adp", ROOT / "adapters" / "hermes" / "adapter.py")
    adp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adp)

    rec = mkrec(origin_ok := None) if False else mkrec()
    rec.origin = "codex-windows"      # 模拟另一个端来的
    native = adp.to_native(rec)
    back = adp._build_record(native, rec.id, "test")
    check("跨来源记录转译往返 hash 一致",
          back is not None and back.content_hash() == rec.content_hash(),
          f"{back.content_hash()[:10] if back else '?'} vs {rec.content_hash()[:10]}")


# ---------- RV12: 验证失败不推进 ----------

def rv12():
    print("\nRV12 验证失败不推进")
    src = (ROOT / "sync-core" / "agent_sync" / "cli.py").read_text(encoding="utf-8")
    check("只对 verified 推进映射", 'ok_ids = set(result["verified"])' in src)
    check("索引失败返回非零", "if problems or not idx_ok:" in src)
    check("reindex_local 返回 bool", "def reindex_local() -> bool:" in src)


# ---------- RV14: 推送失败不推进 ----------

def rv14():
    print("\nRV14 推送失败不推进状态")
    src = (ROOT / "sync-core" / "agent_sync" / "cli.py").read_text(encoding="utf-8")
    # 只看 cmd_publish 这一段，pull 里也有 state 更新
    seg = src[src.find("def cmd_publish"):]
    seg = seg[:seg.find("def cmd_merge")] if "def cmd_merge" in seg else seg
    i_push = seg.find('git("push", "origin"')
    i_state = seg.find('state["last_snapshot"]')
    check("cmd_publish 里更新状态在 git push 之后",
          i_push != -1 and i_state != -1 and i_state > i_push,
          f"push@{i_push} state@{i_state}")
    check("推送失败时提示补推", "补推：git -C" in src)


# ---------- RV16: prompt 受管块 ----------

def rv16():
    print("\nRV16 分发脚本不改 prompt")
    src = (ROOT / "sync-clients.py").read_text(encoding="utf-8")
    check("不再 split(marker) 截断 prompt", "split(marker)" not in src)
    check("产物写进仓库 clients/", "CLIENTS_DIR = REPO" in src)


# ----------

for fn in (rv01, rv03, rv05, rv06, rv08, rv12, rv14, rv16):
    fn()

fails = [r for r in results if not r[1]]
print(f"\n{'全部通过' if not fails else f'失败 {len(fails)} 项'}  （{len(results)} 条检查）")
for n, ok, d in fails:
    print(f"  ! {n} {d}")
sys.exit(1 if fails else 0)
