"""同步核心 CLI。

    python3 -m agent_sync.cli status         看差异，不写东西
    python3 -m agent_sync.cli plan           生成提案
    python3 -m agent_sync.cli review <id>    人眼读提案
    python3 -m agent_sync.cli approve <id>   你确认
    python3 -m agent_sync.cli publish <id>   提交并推送
    python3 -m agent_sync.cli verify         自检仓库
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .compare import Action, Diff, Mapping, compare, summarize
from .dedupe import find_candidates, render_pairs
from .protocol import Record, validate_set, now_iso
from .proposal import (ACTION_CREATE, ACTION_RETIRE, ACTION_UPDATE,
                       Change, Proposal)

import os

# 路径可被环境变量覆盖 —— 测试要在临时目录里跑，不能碰真实记忆
REPO = Path(os.environ.get("AGENT_SYNC_REPO") or (Path(__file__).resolve().parents[2]))
STATE = Path(os.environ.get("AGENT_SYNC_STATE")
             or (Path.home() / ".hermes" / "memory" / "sync-state.json"))
CLIENT = "hermes-linux"

ACTION_LABEL = {
    Action.NOOP: "不动", Action.PULL: "拉下来", Action.PUSH: "推上去",
    Action.CONFLICT: "冲突·要你定", Action.NEW_PUSH: "新增·推上去",
    Action.NEW_PULL: "新记录·拉下来", Action.RETIRE_LOCAL: "远端退役·本地标 dormant",
    Action.RETIRE_CONFLICT: "远端退役但本地改过·停", Action.RESTORE: "本地删了·问要不要恢复",
}


# ---------- 基础设施 ----------

def load_shared(root: Path = REPO) -> dict[str, Record]:
    out: dict[str, Record] = {}
    for p in sorted((root / "memory" / "records").glob("*.md")):
        r = Record.load(p)
        out[r.id] = r
    for p in sorted((root / "identity").glob("*.md")):
        r = Record.load(p)
        out[r.id] = r
    return out


def git(*args: str, root: Path = REPO, check: bool = True):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


def head_commit(root: Path = REPO) -> str:
    try:
        return git("rev-parse", "HEAD", root=root).stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def load_state(path: Path = STATE) -> dict:
    if not path.exists():
        return {"version": 1, "mappings": {}, "last_sync": None, "last_snapshot": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "mappings": {}, "last_sync": None, "last_snapshot": {}}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_adapter():
    """把 adapters/hermes 当模块导入。"""
    import importlib.util
    p = REPO / "adapters" / "hermes" / "adapter.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location("hermes_adapter", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def preview(body: str, n: int = 6) -> str:
    return "\n".join(body.strip().splitlines()[:n])


# ---------- status ----------

def cmd_status(args) -> int:
    shared = load_shared()
    state = load_state()
    mappings = {k: Mapping(**v) for k, v in state.get("mappings", {}).items()}
    ad = load_adapter()

    native = ad.snapshot_native() if ad else {}
    shared_now = {rid: r.content_hash() for rid, r in shared.items()}

    print(f"仓库      {REPO}")
    print(f"共享记录  {len(shared)} 条")
    print(f"本地记录  {len(native)} 条")
    print(f"映射      {len(mappings)} 条")
    print(f"上次同步  {state.get('last_sync') or '从未'}")
    print()

    new = [nid for nid in native if nid not in {m.native_id for m in mappings.values()}]
    same, diff = [], []
    for m in mappings.values():
        n = native.get(m.native_id)
        s = shared.get(m.shared_id)
        if not n or not s:
            continue
        (same if n.content_hash() == s.content_hash() else diff).append(m.shared_id)

    print(f"  新增待推  {len(new)}")
    print(f"  内容一致  {len(same)}")
    print(f"  内容不同  {len(diff)}")
    if new:
        print("\n新增明细:")
        for nid in new[:20]:
            print(f"  + {nid[:8]}  {native[nid].kind:14s} {native[nid].title[:34]}")
        if len(new) > 20:
            print(f"  … 还有 {len(new)-20} 条")
    if diff:
        print("\n内容不同:")
        for sid in diff[:10]:
            print(f"  ~ {sid[:8]}")
    return 0


# ---------- verify ----------

def cmd_verify(args) -> int:
    shared = load_shared()
    problems = validate_set(shared)
    print(f"解析 {len(shared)} 条记录")
    bad = 0
    for rid, r in shared.items():
        try:
            r.validate(rid[:8])
            if Record.parse(r.to_markdown()).content_hash() != r.content_hash():
                print(f"  ! {rid[:8]} 往返 hash 不一致")
                bad += 1
        except Exception as e:
            print(f"  ! {rid[:8]} {e}")
            bad += 1
    for p in problems:
        print("  !", p)
        bad += 1
    print("全部通过" if bad == 0 else f"{bad} 个问题")
    return 0 if bad == 0 else 1


# ---------- pull ----------

def cmd_pull(args) -> int:
    """拉远端 → 转译 → 增量合并进本地 records。

    默认只计划不写；--apply 才真写。
    """
    ad = load_adapter()
    if ad is None:
        print("找不到 Hermes 适配器", file=sys.stderr)
        return 2

    if not args.no_fetch:
        r = git("pull", "--ff-only", "origin", "master", check=False)
        if r.returncode != 0:
            print("拉取失败（可能有本地改动或分叉）：", file=sys.stderr)
            print(r.stderr.strip()[:400], file=sys.stderr)
            if not args.force:
                return 3
            print("--force：跳过 fetch，用现有内容继续", file=sys.stderr)

    shared = load_shared()
    state = load_state()
    plan = ad.plan_import(shared, state)

    n_new = len(plan["new"])
    n_upd = len(plan["updated"])
    n_con = len(plan["conflict"])
    n_ret = len(plan.get("retire", []))
    print(f"共享记录 {len(shared)} 条")
    print(f"  新增导入  {n_new}")
    print(f"  更新导入  {n_upd}")
    print(f"  跟随退役  {n_ret}")
    print(f"  冲突·跳过 {n_con}")
    print(f"  无需处理  {len(plan['skip'])}")
    print()

    if n_ret:
        print("跟随退役（远端已撤回/取代，本地未改）：")
        for sid, rec, _local in plan.get("retire", [])[:15]:
            print(f"  - {sid[:8]}  {rec.status:11s} {rec.title[:36]}")
        if n_ret > 15:
            print(f"  … 还有 {n_ret-15} 条")
        print()

    # 去重：远端新记录先和本地现有记录比一遍
    dup_map: dict[str, list] = {}
    if plan["new"]:
        incoming = {sid: rec for sid, rec, _ in plan["new"]}
        local_pool = ad.snapshot_native()
        cands = find_candidates(incoming, local_pool)
        for c in cands:
            dup_map.setdefault(c.b_id, []).append(c)

    if plan["new"]:
        print("新增：")
        for sid, rec, _ in plan["new"][:25]:
            mark = f"  ⚠ 疑似和本地 {len(dup_map[sid])} 条重复" if sid in dup_map else ""
            print(f"  + {sid[:8]}  {rec.kind:14s} {rec.title[:36]}{mark}")
        if len(plan["new"]) > 25:
            print(f"  … 还有 {len(plan['new'])-25} 条")
        print()

    if plan["updated"]:
        print("更新（本地没动过，可安全覆盖）：")
        for sid, rec, _ in plan["updated"][:15]:
            print(f"  ~ {sid[:8]}  {rec.title[:36]}")
        print()

    if plan["conflict"]:
        print("冲突（本地也改过，不自动覆盖）：")
        for sid, rec, local in plan["conflict"]:
            print(f"  ! {sid[:8]}  {rec.title[:36]}")
            print(f"      远端 {rec.content_hash()[:10]}  本地 {local.content_hash()[:10]}")

    if dup_map:
        print()
        print(f"⚠ 其中 {len(dup_map)} 条可能和本地已有记录重复：")
        for sid, lst in list(dup_map.items())[:8]:
            rec = next(r for s, r, _ in plan["new"] if s == sid)
            print(f"  {sid[:8]} 「{rec.title[:24]}」")
            for c in lst[:3]:
                other = ad.snapshot_native().get(c.a_id)
                print(f"      {c.score:.2f} ↔ 本地「{(other.title if other else c.a_id[:8])[:24]}」"
                      f"  {'、'.join(c.reasons)}")
        print("  （仍然会导入，但这类通常该合并——先看完再决定）")

    # 空计划判断要把 retire 算进来。
    # 以前只看 new/updated，导致「远端只撤了一条」这种最常见的退役场景
    # 直接 return 0，退役分支永远走不到。
    if n_new == 0 and n_upd == 0 and n_ret == 0:
        pending = bool(load_state().get("pending_reindex"))
        if pending:
            # 默认（不带 --apply）只报告，不写任何东西。
            # 以前这里直接重建索引并改 state —— 计划路径偷偷落盘，
            # 和「默认只计划」的说明矛盾。
            print("没有记录变化，但上次索引重建失败（欠账）。")
            if not args.apply:
                print("要补建索引，加 --apply：")
                print("  python3 -m agent_sync.cli pull --apply")
                return 0
            print("正在补建索引……")
            if not reindex_local():
                print("索引仍然重建失败", file=sys.stderr)
                return 8
            st = load_state(); st.pop("pending_reindex", None); save_state(st)
            print("索引已补齐")
            return 0
        print("没有要导入的。")
        return 0

    if not args.apply:
        print()
        print(f"要真的写进本地，加 --apply：")
        print(f"  python3 -m agent_sync.cli pull --apply")
        return 0

    result = ad.apply_import(plan)
    print()
    print(f"写入 {len(result['written'])} 条，失败 {len(result['failed'])} 条")
    print(f"读回验证 {len(result['verified'])} 条通过，{len(result['broken'])} 条有问题")
    if result.get("retired"):
        print(f"跟随远端退役 {len(result['retired'])} 条")
    for sid, why in result["failed"] + result["broken"]:
        print(f"  ! {sid[:8]} {why}")

    # 只对**验证通过**的推进映射。
    # 写进文件不等于能回忆；读回校验失败的条目如果也记成已同步，
    # 它就永远失去了重试资格，而系统还会以为这条已经好了。
    ok_ids = set(result["verified"])
    by_id = {sid: rec for sid, rec in result["written"]}
    state = load_state()
    progressed = 0
    for sid in ok_ids:
        rec = by_id.get(sid)
        if rec is None:
            continue
        state.setdefault("mappings", {})[sid] = {
            "shared_id": sid, "native_id": sid,
            "shared_hash": rec.content_hash(), "native_hash": rec.content_hash(),
            "origin": rec.origin, "updated_at": now_iso(),
        }
        progressed += 1

    # 退役的也要更新基线，否则下次还会被当成"待退役"
    for sid, rec in result.get("retired", []):
        m = state.setdefault("mappings", {}).get(sid)
        if m:
            m["shared_hash"] = rec.content_hash()
            m["native_hash"] = rec.content_hash()
            m["updated_at"] = now_iso()

    state["last_snapshot"] = {rid: r.content_hash() for rid, r in load_shared().items()}
    state["last_sync"] = now_iso()
    save_state(state)
    print(f"映射已更新 {progressed} 条，共 {len(state['mappings'])} 条")

    # 导入完必须重建本地检索索引，否则 records 有记录但 recall 搜不到
    idx_ok = reindex_local()

    # 索引失败要留标记。否则下一次 pull 看到"文件都一致"就提前返回，
    # 索引永远不会被重建，而每条命令都报告成功。
    st2 = load_state()
    if idx_ok:
        st2.pop("pending_reindex", None)
    else:
        st2["pending_reindex"] = now_iso()
    save_state(st2)

    # 有失败 / 读回有问题 / 索引没建起来 → 都不算成功
    problems = len(result["failed"]) + len(result["broken"])
    if problems or not idx_ok:
        print(f"\n导入不完整：{problems} 条有问题"
              + ("，索引未重建（已记为待重建，下次 pull 会补）" if not idx_ok else ""),
              file=sys.stderr)
        print("失败的条目没有推进映射，下次 pull 会重试。", file=sys.stderr)
        return 8
    return 0


def reindex_local() -> bool:
    """调 Hermes 的 records 索引重建。返回是否成功。

    索引没建起来 = 记录在库里但搜不到 = 不能算导入成功。
    """
    script = Path(os.environ.get("AGENT_SYNC_RECORDS_SCRIPT")
                  or (Path.home() / ".hermes" / "scripts" / "records 脚本"))
    if not script.exists():
        print("  ! 没找到 records 脚本，本地 recall 搜不到新记录", file=sys.stderr)
        return False
    r = subprocess.run(["python3", str(script), "reindex"], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  索引已重建：{r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'ok'}")
        return True
    print(f"  ! 索引重建失败：{r.stderr.strip()[:200]}", file=sys.stderr)
    return False


# ---------- dedupe ----------

def cmd_dedupe(args) -> int:
    """全库扫一遍，列出相似候选。不下结论，只挑人/模型该看的。"""
    ad = load_adapter()
    shared = load_shared()
    native = ad.snapshot_native() if ad else {}

    print(f"共享 {len(shared)} 条 + 本地 {len(native)} 条")
    # 共享内部、本地内部、共享对本地，三种都扫
    all_recs = {**shared, **{f"local:{k}": v for k, v in native.items()}}

    pool_shared = dict(shared)
    cands = find_candidates(pool_shared, pool_shared)
    print()
    print(render_pairs(cands, all_recs))

    if ad and native:
        cross = find_candidates(native, shared)
        if cross:
            print(f"\n本地 ↔ 共享 交叉候选 {len(cross)} 对：")
            for c in cross[:10]:
                a, b = shared.get(c.a_id), native.get(c.b_id)
                print(f"  {c.score:.2f}  {(a.title if a else '?')[:24]}  ↔  {(b.title if b else '?')[:24]}")
                print(f"        {'、'.join(c.reasons)}")

    # 留痕
    if cands:
        log = REPO / "dedupe-log"
        log.mkdir(exist_ok=True)
        f = log / f"{now_iso()[:10]}.md"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {now_iso()}\n\n")
            for c in cands[:30]:
                fh.write(f"- {c.score:.2f} `{c.a_id[:8]}` ↔ `{c.b_id[:8]}`：{'、'.join(c.reasons)}\n")
        print(f"\n留痕：{f.relative_to(REPO)}")
    return 0


# ---------- plan ----------

def cmd_plan(args) -> int:
    ad = load_adapter()
    if ad is None:
        print("找不到 Hermes 适配器", file=sys.stderr)
        return 2

    shared = load_shared()
    state = load_state()
    mappings = {k: Mapping(**v) for k, v in state.get("mappings", {}).items()}
    native = ad.snapshot_native()

    # ---- 三方比较 ----
    # 光比「当前本地」和「当前共享」分不出差异是谁造成的：
    # 远端改了、本地没动，也会显示成"不一样"，然后提议用旧本地覆盖新远端。
    # 必须带上两份基线：共享基线 B（上次同步时远端长什么样）、
    # 本地基线 L0（上次同步时本地长什么样）。
    shared_now = {rid: r.content_hash() for rid, r in shared.items()}
    shared_base = state.get("last_snapshot") or {}
    local_now = {nid: r.content_hash() for nid, r in native.items()}

    diffs = compare(shared_now, shared_base, local_now, mappings)

    changes: list[Change] = []
    remote_changed: list[Diff] = []

    # compare() 只遍历共享侧出现过的 id —— 本地独有、还没推过的新记录不在里面。
    # 这一段单独补，否则本地新建的东西永远推不上去。
    mapped_native = {m.native_id for m in mappings.values()}
    for nid, rec in sorted(native.items()):
        if nid in mapped_native:
            continue
        if nid in shared_now:
            continue
        changes.append(Change(
            shared_id=rec.id, action=ACTION_CREATE, kind=rec.kind, title=rec.title,
            content_hash=rec.content_hash(), reason="本地新增，远端没有",
            source={"native_id": nid, "locator": f"local-ref:records/{nid}"},
            body_preview=preview(rec.body),
        ))

    for d in diffs:
        nid = d.native_id
        rec = native.get(nid) if nid else None

        if d.action == Action.NEW_PUSH:
            changes.append(Change(
                shared_id=d.shared_id, action=ACTION_CREATE, kind=rec.kind, title=rec.title,
                content_hash=rec.content_hash(), reason="本地新增，远端没有",
                source={"native_id": nid, "locator": f"local-ref:records/{nid}"},
                body_preview=preview(rec.body),
            ))
        elif d.action == Action.PUSH:
            remote = shared.get(d.shared_id)
            changes.append(Change(
                shared_id=d.shared_id, action=ACTION_UPDATE, kind=rec.kind, title=rec.title,
                content_hash=rec.content_hash(), old_hash=remote.content_hash() if remote else None,
                reason="只有本地变了",
                source={"native_id": nid, "locator": f"local-ref:records/{nid}"},
                body_preview=preview(rec.body),
            ))
        elif d.action in (Action.PULL, Action.NEW_PULL, Action.RETIRE_LOCAL):
            # 远端变了、本地没动 —— 该导入，不该用旧本地推回去
            remote_changed.append(d)
        elif d.action in (Action.CONFLICT, Action.RETIRE_CONFLICT, Action.RESTORE):
            # 两边都动了 / 本地删了远端还在 —— 停下给人看
            remote_changed.append(d)

    if remote_changed:
        print(f"⚠ 有 {len(remote_changed)} 条不该由本次推送处理：")
        for d in remote_changed[:12]:
            print(f"  [{ACTION_LABEL[d.action]}] {d.shared_id[:8]}  {d.reason}")
            if d.action in (Action.CONFLICT, Action.RETIRE_CONFLICT):
                print(f"      本地 {str(d.local_hash)[:10]}  远端 {str(d.remote_hash)[:10]}"
                      f"  基线 {str(d.baseline_remote)[:10]}")
        if len(remote_changed) > 12:
            print(f"  … 还有 {len(remote_changed)-12} 条")
        print("  这些先跑 `agent-sync pull` 处理，或手工裁决冲突。")
        print()

    if not changes:
        print("没有要推的东西，两边一致。")
        return 0

    # 去重检查：新增的记录，先看共享仓库里有没有像的
    new_changes = [c for c in changes if c.action == ACTION_CREATE]
    if new_changes:
        incoming = {c.shared_id: native[c.source["native_id"]]
                    for c in new_changes if c.source.get("native_id") in native}
        cands = find_candidates(incoming, shared)
        if cands:
            print(f"⚠ 有 {len(cands)} 对可能重复（共享仓库里已有类似记录）：")
            for c in cands[:10]:
                a = shared.get(c.a_id) or incoming.get(c.a_id)
                b = shared.get(c.b_id) or incoming.get(c.b_id)
                print(f"  {c.score:.2f}  本地「{(b.title if b else '?')[:22]}」"
                      f"  ↔  仓库「{(a.title if a else '?')[:22]}」")
                print(f"        {'、'.join(c.reasons)}")
            if len(cands) > 10:
                print(f"  … 还有 {len(cands)-10} 对")
            print("  先看：python3 -m agent_sync.cli dedupe")
            print()
            if not args.allow_dupes:
                print("这些要人判。确认都是新的，再加 --allow-dupes 重新生成提案。")
                for c in changes:
                    if c.shared_id in {x.b_id for x in cands}:
                        c.reason += "（⚠ 疑似重复，未判）"
            else:
                for c in changes:
                    if c.shared_id in {x.b_id for x in cands}:
                        c.reason += "（人工确认是新的）"

    p = Proposal(
        proposal_id=__import__("uuid").uuid4().__str__(),
        created_at=now_iso(), client=CLIENT,
        base_commit=head_commit(), target_ref="master",
        changes=changes,
    )
    path = p.save(REPO)

    # 同时存一份人眼可读的
    rp = REPO / "changes" / f"{p.proposal_id}.review.md"
    rp.write_text(p.render_review(), encoding="utf-8")

    print(f"提案 {p.proposal_id[:8]}：{len(changes)} 条变更")
    print(f"  新增 {sum(1 for c in changes if c.action=='create')}   修改 {sum(1 for c in changes if c.action=='update')}")
    print(f"  {path.relative_to(REPO)}")
    print(f"  {rp.relative_to(REPO)}")
    print(f"\n看一遍：python3 -m agent_sync.cli review {p.proposal_id}")
    return 0


# ---------- review ----------

def _find_proposal(pid: str) -> Path | None:
    d = REPO / "changes"
    if not d.exists():
        return None
    exact = d / f"{pid}.json"
    if exact.exists():
        return exact
    for p in d.glob("*.json"):
        if p.stem.startswith(pid):
            return p
    return None


def cmd_review(args) -> int:
    p = _find_proposal(args.proposal_id)
    if not p:
        print(f"找不到提案 {args.proposal_id}", file=sys.stderr)
        return 2
    prop = Proposal.load(p)
    print(prop.render_review())
    return 0


# ---------- approve ----------

def cmd_approve(args) -> int:
    p = _find_proposal(args.proposal_id)
    if not p:
        print(f"找不到提案 {args.proposal_id}", file=sys.stderr)
        return 2
    prop = Proposal.load(p)
    stale = prop.is_stale(head_commit())
    if stale:
        print("注意：这个提案是基于旧提交生成的，远端可能已经变了。", file=sys.stderr)
        if not args.force:
            print("先跑 plan 重新生成，或者加 --force 坚持。", file=sys.stderr)
            return 3

    h = prop.approve(by=args.by)
    p.write_text(prop.to_json(), encoding="utf-8")
    print(f"已确认 {prop.proposal_id[:8]}")
    print(f"  指纹 {h[:16]}")
    print(f"  确认人 {args.by}")
    print(f"\n发布：python3 -m agent_sync.cli publish {prop.proposal_id}")
    return 0


# ---------- publish ----------

def cmd_publish(args) -> int:
    p = _find_proposal(args.proposal_id)
    if not p:
        print(f"找不到提案 {args.proposal_id}", file=sys.stderr)
        return 2
    prop = Proposal.load(p)

    ok, why = prop.check_approval()
    if not ok:
        # 不提供任何绕过。发布命令里绝不自动签收——
        # 那等于「我确认了」这句话由机器自己说。
        print(f"不能发布：{why}", file=sys.stderr)
        print("先看：python3 -m agent_sync.cli review <id>", file=sys.stderr)
        print("再确认：python3 -m agent_sync.cli approve <id>", file=sys.stderr)
        return 3

    if prop.is_stale(head_commit()) and not args.force:
        print("提案基于旧提交，远端可能变了。加 --force 或先 plan。", file=sys.stderr)
        return 3

    # 1. 写记录文件 —— 两步：先全组预检（零写入），通过了才真写
    ad = load_adapter()
    native = ad.snapshot_native()
    recdir = REPO / "memory" / "records"
    written: list[str] = []

    group_of: dict[str, int] = {}
    for gi, g in enumerate(prop.groups):
        for sid in g:
            group_of[sid] = gi

    # ---- 1a. 预检：算出每个变更最终要写什么，一个字节都不落盘 ----
    planned: list[tuple[Change, Record]] = []
    precheck_errors: list[tuple[str, str, int | None]] = []

    for c in prop.changes:
        gi = group_of.get(c.shared_id)
        if c.action in (ACTION_CREATE, ACTION_UPDATE):
            if c.frozen_md:
                try:
                    rec = Record.parse(c.frozen_md, where=c.shared_id[:8])
                except Exception as e:
                    precheck_errors.append((c.shared_id, f"冻结内容解析失败：{e}", gi))
                    continue
                if rec.content_hash() != c.content_hash:
                    precheck_errors.append((c.shared_id, "冻结内容 hash 不符", gi))
                    continue
                planned.append((c, rec))
            else:
                nid = c.source.get("native_id")
                rec = native.get(nid) if nid else None
                if rec is None:
                    precheck_errors.append((c.shared_id, "本地记录不见了", gi))
                    continue
                if rec.content_hash() != c.content_hash:
                    precheck_errors.append((c.shared_id, "内容在提案之后变了", gi))
                    continue
                planned.append((c, rec))

        elif c.action == ACTION_RETIRE:
            target = recdir / f"{c.shared_id}.md"
            if not target.exists():
                precheck_errors.append((c.shared_id, "要退役的记录不在仓库里", gi))
                continue
            try:
                r = Record.load(target)
            except Exception as e:
                precheck_errors.append((c.shared_id, f"记录读不了：{e}", gi))
                continue
            r.status = c.target_status or "retracted"
            r.updated_at = now_iso()
            r.updated_by = CLIENT
            r.revision += 1
            planned.append((c, r))

    # 依赖组：组里任何一项预检失败，整组不写
    broken = {gi for _sid, _why, gi in precheck_errors if gi is not None}
    blocked = {c.shared_id for c, _ in planned
               if group_of.get(c.shared_id) in broken}
    if blocked:
        planned = [(c, r) for c, r in planned if c.shared_id not in blocked]

    for sid, why, gi in precheck_errors:
        tag = f"（组 {gi}）" if gi is not None else ""
        print(f"  ! {sid[:8]}{tag} {why}", file=sys.stderr)
    if broken:
        print(f"依赖组预检不通过（{len(broken)} 组），整组不写，磁盘未改动。", file=sys.stderr)

    # 不是组失败的独立错误，也一起停
    standalone_err = [e for e in precheck_errors if e[2] is None]
    if standalone_err or broken:
        return 4

    if not planned:
        print("没有可写的内容，中止。", file=sys.stderr)
        return 4

    # ---- 1b. 真写：先记下本次实际 before 字节，失败时只条件恢复 ----
    before: dict[Path, bytes | None] = {}
    for c, rec in planned:
        pth = recdir / f"{rec.id}.md"
        before[pth] = pth.read_bytes() if pth.exists() else None

    write_error = None
    try:
        for c, rec in planned:
            rec.save(recdir)
            written.append(c.shared_id)
    except Exception as e:
        write_error = e

    if write_error is not None:
        print(f"写盘失败：{write_error}", file=sys.stderr)
        # 只恢复**本次写过的**文件，而且要求当前内容仍等于本次写入的内容
        for c, rec in planned:
            pth = recdir / f"{rec.id}.md"
            if c.shared_id not in written:
                continue
            try:
                now_bytes = pth.read_bytes()
            except OSError:
                continue
            if now_bytes != rec.to_markdown().encode("utf-8"):
                print(f"  {c.shared_id[:8]} 写后又被改过，保留现状不覆盖", file=sys.stderr)
                continue
            old = before.get(pth)
            if old is None:
                pth.unlink(missing_ok=True)
            else:
                pth.write_bytes(old)
        return 4

    # 2. 先写审计文件（要跟记录一起 commit），但**状态先不推进**
    done = REPO / "changes" / f"{prop.proposal_id}.done.json"
    prop.approval["status"] = "published"
    done.write_text(prop.to_json(), encoding="utf-8")

    # 3. git —— 只暂存提案里点名的文件，绝不 git add -A
    #    git add -A 会把工作区里别人的东西（未批准的提案、临时笔记）一起传上去，
    #    而用户只确认了这份提案。
    paths = [f"memory/records/{sid}.md" for sid in written]
    paths.append(f"changes/{prop.proposal_id}.done.json")
    removed = []
    for extra in (f"changes/{prop.proposal_id}.json", f"changes/{prop.proposal_id}.review.md"):
        if (REPO / extra).exists():
            removed.append(extra)
    try:
        dirty = git("status", "--porcelain").stdout.strip()
        staged_before = git("diff", "--cached", "--name-only").stdout.strip()
        if staged_before:
            print("暂存区里已有别的东西，先处理掉再发布：", file=sys.stderr)
            for line in staged_before.splitlines():
                print(f"  {line}", file=sys.stderr)
            return 7

        git("add", *paths)
        if removed:
            git("rm", "--cached", "--quiet", "--", *removed, check=False)
            for x in removed:
                (REPO / x).unlink(missing_ok=True)
        staged_now = set(git("diff", "--cached", "--name-only").stdout.split())
        allowed = set(paths) | set(removed)
        unexpected = staged_now - allowed
        if unexpected:
            print(f"暂存区多出了不该有的文件，中止：{unexpected}", file=sys.stderr)
            git("reset", "HEAD", *unexpected, check=False)
            return 7

        msg = f"sync: {len(written)} 条变更（{prop.proposal_id[:8]}）"
        git("commit", "-m", msg)
        push = git("push", "origin", prop.target_ref, check=False)
        if push.returncode != 0:
            # 已经 commit 但没推上去。保留提交，别丢——
            # 下次直接 `git push` 就能补上，不用重新生成提案。
            print("推送失败（本地已提交，未上传）：", push.stderr.strip()[:400], file=sys.stderr)
            print(f"  补推：git -C {REPO} push origin {prop.target_ref}", file=sys.stderr)
            print("  映射和基线**没有**推进，重试不会产生重复记录。", file=sys.stderr)
            return 5
        # 4. 验证远端真的收到了
        git("fetch", "origin", check=False)
        remote_head = git("rev-parse", f"origin/{prop.target_ref}", check=False).stdout.strip()
        local_head = head_commit()
        if remote_head != local_head:
            print(f"推送后远端 head 不一致：{remote_head[:8]} vs {local_head[:8]}", file=sys.stderr)
            print("  映射和基线没有推进。", file=sys.stderr)
            return 6
    except subprocess.CalledProcessError as e:
        print(f"git 出错：{e.stderr[:400]}", file=sys.stderr)
        return 5

    # 5. 远端确认收到了，才推进同步状态。
    #    顺序很重要：先推进状态再推送，一旦推送失败，
    #    本地会以为自己已经同步过，远端其实什么都没有。
    state = load_state()
    shared = load_shared()
    for sid in written:
        c = next((x for x in prop.changes if x.shared_id == sid), None)
        if c is None:
            continue
        nid = c.source.get("native_id")
        if nid:
            state.setdefault("mappings", {})[sid] = {
                "shared_id": sid, "native_id": nid,
                "shared_hash": c.content_hash, "native_hash": c.content_hash,
                "origin": CLIENT, "updated_at": now_iso(),
            }
    state["last_snapshot"] = {rid: r.content_hash() for rid, r in shared.items()}
    state["last_sync"] = now_iso()
    save_state(state)

    p.unlink(missing_ok=True)

    print(f"发布完成：{len(written)} 条")
    print(f"  远端 {prop.target_ref} @ {remote_head[:8]}")
    print(f"  审计 {done.relative_to(REPO)}")
    return 0


# ---------- check（只读） ----------

def cmd_check(args) -> int:
    """定期检查入口：只读，不写任何东西。

    给定时任务用的。它只回答「有没有该处理的差异」，
    不生成提案、不导入、不推送。要动手还是得人来。

    退出码：
        0  没有差异
        1  有差异（该看看了）
        2  环境有问题（不是 git 仓库之类）
    """
    shared = load_shared()
    state = load_state()
    mappings = {k: Mapping(**v) for k, v in state.get("mappings", {}).items()}
    ad = load_adapter()
    native = ad.snapshot_native() if ad else {}

    shared_now = {rid: r.content_hash() for rid, r in shared.items()}
    shared_base = state.get("last_snapshot") or {}
    local_now = {nid: r.content_hash() for nid, r in native.items()}

    diffs = compare(shared_now, shared_base, local_now, mappings)
    mapped_native = {m.native_id for m in mappings.values()}
    new_local = [nid for nid in native if nid not in mapped_native and nid not in shared_now]

    pending_reindex = bool(state.get("pending_reindex"))
    actionable = [d for d in diffs if d.action != Action.NOOP]

    # 远端对齐检查：只看本地副本会漏掉「另一端已经推上来了」。
    # 用 ls-remote 只读探一下远端 HEAD，不 fetch、不改工作区。
    remote_ahead = False
    remote_ok = False
    remote_fail_reason = ""
    try:
        ls = git("ls-remote", "origin", "refs/heads/master", check=False)
        if ls.returncode != 0:
            remote_fail_reason = (ls.stderr or "").strip()[:120] or f"git ls-remote 退出码 {ls.returncode}"
        elif not ls.stdout.strip():
            remote_fail_reason = "远端没有 refs/heads/master（分支名不对或远端是空的）"
        else:
            parts = ls.stdout.split()
            remote_sha = parts[0] if parts else ""
            local_sha = git("rev-parse", "master", check=False).stdout.strip()
            if not remote_sha or len(remote_sha) < 7:
                remote_fail_reason = f"ls-remote 输出无法解析：{ls.stdout.strip()[:60]}"
            elif not local_sha:
                remote_fail_reason = "本地没有 master 分支"
            else:
                remote_ok = True
                if remote_sha != local_sha:
                    remote_ahead = True
    except Exception as e:
        remote_fail_reason = f"{type(e).__name__}: {e}"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{ts}] agent-sync 定期检查")
    print(f"  共享 {len(shared)} 条 · 本地 {len(native)} 条 · 映射 {len(mappings)} 条")

    # 查不到远端 = 状态未知。不能因为本地看起来一致就报告"没有差异"——
    # 网络断了、认证失效、分支名错了，都会被那条路径掩盖成"一切正常"。
    if not remote_ok:
        print(f"  ✗ 远端状态未知：{remote_fail_reason}")
        print("    这次检查没能确认远端是否对齐。")
        if remote_fail_reason:
            print("    排查：git ls-remote origin refs/heads/master")
        return 2

    if remote_ahead:
        print(f"  ⚠ 远端 master 已更新（{remote_sha[:8]}），本地是 {local_sha[:8]}")
        print("    先 git pull 再跑一次检查，看到的才是最新的。")

    if not actionable and not new_local and not pending_reindex and not remote_ahead:
        print("  没有差异。")
        return 0

    if actionable:
        print(f"  待处理 {len(actionable)} 条：")
        for d in actionable[:10]:
            print(f"    [{ACTION_LABEL[d.action]}] {d.shared_id[:8]}  {d.reason}")
        if len(actionable) > 10:
            print(f"    … 还有 {len(actionable)-10} 条")
    if new_local:
        print(f"  本地新增未推 {len(new_local)} 条")
        for nid in new_local[:5]:
            print(f"    + {nid[:8]}  {native[nid].title[:34]}")
    if pending_reindex:
        print("  ⚠ 上次索引重建失败，下次 pull --apply 会补")

    print()
    print("  **这是只读检查。** 要处理请手动跑：")
    if remote_ahead:
        print("    git pull            # 先把远端的新提交拉下来")
    print("    agent-sync pull --apply    # 拉远端的变化")
    print("    agent-sync plan            # 准备推送本地的变化")
    return 1


# ---------- reconcile（补推之后核对收尾） ----------

def cmd_reconcile(args) -> int:
    """push 失败后你手动补推了，用这个核对并推进基线。

    只认「远端确实包含这份提案批准的内容」，
    不把本地之后的新改动当成已经发布。
    """
    p = _find_proposal(args.proposal_id)
    if not p:
        print(f"找不到提案 {args.proposal_id}", file=sys.stderr)
        return 2
    prop = Proposal.load(p)
    ok, why = prop.check_approval()
    if not ok and prop.approval.get("status") != "published":
        print(f"提案本身不可信：{why}", file=sys.stderr)
        return 3

    r = git("fetch", "origin", prop.target_ref, check=False)
    if r.returncode != 0:
        print(f"fetch 失败：{r.stderr.strip()[:200]}", file=sys.stderr)
        return 4

    remote_ref = f"origin/{prop.target_ref}"
    print(f"核对 {prop.proposal_id[:8]}（{len(prop.changes)} 条变更）")

    confirmed, missing = [], []
    for c in prop.changes:
        path = f"memory/records/{c.shared_id}.md"
        show = git("show", f"{remote_ref}:{path}", check=False)
        if show.returncode != 0:
            missing.append((c, "远端没有这个文件"))
            continue
        try:
            rec = Record.parse(show.stdout, where=path)
        except Exception as e:
            missing.append((c, f"远端内容解析失败：{e}"))
            continue

        if c.action in (ACTION_CREATE, ACTION_UPDATE):
            if rec.content_hash() == c.content_hash:
                confirmed.append((c, rec))
            else:
                missing.append((c, f"内容不符（远端 {rec.content_hash()[:10]}，"
                                    f"批准的是 {c.content_hash[:10]}）"))
        elif c.action == ACTION_RETIRE:
            want = c.target_status or "retracted"
            if rec.status == want:
                confirmed.append((c, rec))
            else:
                missing.append((c, f"状态不符（远端 {rec.status}，应为 {want}）"))

    if missing:
        print("远端还不包含批准的内容：", file=sys.stderr)
        for c, why in missing:
            print(f"  ! {c.shared_id[:8]} {why}", file=sys.stderr)
        print("\n先补推：git push origin " + prop.target_ref, file=sys.stderr)
        print("基线没有推进，重试不会产生重复记录。", file=sys.stderr)
        return 5

    # 远端确认无误，推进基线
    state = load_state()
    for c, rec in confirmed:
        nid = c.source.get("native_id")
        if nid:
            state.setdefault("mappings", {})[c.shared_id] = {
                "shared_id": c.shared_id, "native_id": nid,
                "shared_hash": rec.content_hash(), "native_hash": rec.content_hash(),
                "origin": CLIENT, "updated_at": now_iso(),
            }
    state["last_snapshot"] = {rid: r.content_hash() for rid, r in load_shared().items()}
    state["last_sync"] = now_iso()
    state["last_remote_head"] = git("rev-parse", remote_ref).stdout.strip()
    save_state(state)

    prop.approval["status"] = "published"
    done = REPO / "changes" / f"{prop.proposal_id}.done.json"
    if p.suffix == ".json" and not p.name.endswith(".done.json"):
        done.write_text(prop.to_json(), encoding="utf-8")
        p.unlink(missing_ok=True)
        (REPO / "changes" / f"{prop.proposal_id}.review.md").unlink(missing_ok=True)

    print(f"核对通过：{len(confirmed)} 条已在远端")
    print(f"  映射已补齐，共 {len(state['mappings'])} 条")
    print(f"  远端 {prop.target_ref} @ {state['last_remote_head'][:8]}")
    return 0


# ---------- merge ----------

def _resolve(shared: dict, prefix: str):
    """支持前缀匹配，唯一命中才算数。"""
    if prefix in shared:
        return shared[prefix]
    hits = [r for rid, r in shared.items() if rid.startswith(prefix)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"{prefix} 匹配到 {len(hits)} 条，请给更长的前缀：", file=sys.stderr)
        for h in hits[:6]:
            print(f"  {h.id[:12]}  {h.title[:30]}", file=sys.stderr)
    return None


def cmd_merge(args) -> int:
    """把 drop 的独有内容并进 keep，drop 标 superseded。

    一次提交改两个文件——这是「依赖组」，不能分开提交，
    否则中间那一刻仓库里会出现「两条都 active 但都残缺」的状态。
    """
    shared = load_shared()
    keep, drop = _resolve(shared, args.keep), _resolve(shared, args.drop)
    if not keep or not drop:
        print("找不到记录（要用完整 id，或至少能唯一匹配的前缀）", file=sys.stderr)
        return 2

    for r, name in ((keep, "keep"), (drop, "drop")):
        if r.kind in ("soul", "rule"):
            print(f"不能合并 {name}：{r.kind} 类型必须人来改", file=sys.stderr)
            return 3
    if keep.status in ("superseded", "retracted"):
        print(f"keep 已经是 {keep.status} 状态，不能当保留方", file=sys.stderr)
        return 3
    if drop.id in keep.supersedes:
        print("这两条已经合并过了")
        return 0

    # 找出 drop 有、keep 没有的句子
    keep_sents = {s.strip() for s in keep.body.replace("。", "。\n").split("\n") if s.strip()}
    only_in_drop = [
        s.strip() for s in drop.body.replace("。", "。\n").split("\n")
        if s.strip() and s.strip() not in keep_sents and len(s.strip()) >= 8
    ]

    if args.dry_run:
        print(f"合并预览：{drop.id[:8]} → {keep.id[:8]}")
        print(f"  keep: {keep.title}")
        print(f"  drop: {drop.title}")
        print(f"  drop 独有 {len(only_in_drop)} 句：")
        for s in only_in_drop[:10]:
            print(f"    · {s[:70]}")
        if not only_in_drop:
            print("    （没有独有内容，纯重复）")
        return 0

    now = now_iso()
    if only_in_drop:
        keep.body = keep.body.rstrip() + "\n\n" + "\n".join(only_in_drop)
    keep.revision += 1
    keep.updated_at = now
    keep.updated_by = CLIENT
    keep.supersedes = list(dict.fromkeys(list(keep.supersedes) + [drop.id]))
    keep.evidence = keep.evidence + [
        e for e in drop.evidence if e.get("id") not in {x.get("id") for x in keep.evidence}
    ]
    if args.retitle:
        keep.title = args.retitle[:60]

    drop.status = "superseded"
    drop.updated_at = now
    drop.updated_by = CLIENT
    drop.revision += 1
    note = args.note or "合并重复记录"
    drop.body = drop.body.rstrip() + f"\n\n> 已合并进 `{keep.id[:8]}`（{note}）"

    # 先备份
    bak = REPO / "changes" / "merge-backup"
    bak.mkdir(parents=True, exist_ok=True)
    for r in (keep, drop):
        src = REPO / "memory" / "records" / f"{r.id}.md"
        if src.exists():
            (bak / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    keep.save(REPO / "memory" / "records")
    drop.save(REPO / "memory" / "records")

    # 校验
    for r in (keep, drop):
        back = Record.load(REPO / "memory" / "records" / f"{r.id}.md")
        if back.status != r.status:
            print(f"写回校验失败：{r.id[:8]} 状态不对", file=sys.stderr)
            return 5

    # 留痕
    log = REPO / "dedupe-log"
    log.mkdir(exist_ok=True)
    with (log / f"{now[:10]}.md").open("a", encoding="utf-8") as f:
        f.write(f"\n- {now} 合并：`{drop.id[:8]}` → `{keep.id[:8]}`\n")
        f.write(f"  - 依据：{note}\n")
        f.write(f"  - 并进 {len(only_in_drop)} 句独有内容\n")
        f.write(f"  - 保留「{keep.title}」，退役「{drop.title}」\n")

    # 合并也要走确认。以前这里有 --publish 直接 commit+push，
    # 等于给别人开了一条「不经审阅就上传」的旁路。
    # 现在只写文件 + 生成提案，推送必须经过 review → approve → publish。
    prop = Proposal(
        proposal_id=__import__("uuid").uuid4().__str__(),
        created_at=now_iso(), client=CLIENT,
        base_commit=head_commit(), target_ref="master",
        changes=[
            Change(shared_id=keep.id, action=ACTION_UPDATE, kind=keep.kind,
                   title=keep.title, content_hash=keep.content_hash(),
                   old_hash=None, reason=f"合并 {drop.id[:8]} 进来（{note}）",
                   source={"native_id": None, "locator": f"shared:{keep.id}"},
                   body_preview=preview(keep.body),
                   frozen_md=keep.to_markdown()),
            Change(shared_id=drop.id, action=ACTION_RETIRE, kind=drop.kind,
                   title=drop.title, content_hash="", reason=f"被 {keep.id[:8]} 取代",
                   source={"native_id": None, "locator": f"shared:{drop.id}"},
                   target_status="superseded"),
        ],
        groups=[[keep.id, drop.id]],
    )
    pp = prop.save(REPO)
    (REPO / "changes" / f"{prop.proposal_id}.review.md").write_text(
        prop.render_review(), encoding="utf-8")

    print(f"合并完成（文件已写）：{drop.id[:8]} → {keep.id[:8]}")
    print(f"  keep: {keep.title}（rev {keep.revision}，并入 {len(only_in_drop)} 句）")
    print(f"  drop: {drop.status}")
    print()
    print(f"提案 {prop.proposal_id[:8]} 已生成，走正常确认流程：")
    print(f"  agent-sync review {prop.proposal_id}")
    print(f"  agent-sync approve {prop.proposal_id}")
    print(f"  agent-sync publish {prop.proposal_id}")
    return 0


# ---------- main ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent-sync", description="多端 agent 记忆同步")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="看差异").set_defaults(fn=cmd_status)
    sub.add_parser("verify", help="自检仓库").set_defaults(fn=cmd_verify)
    pl2 = sub.add_parser("plan", help="生成提案")
    pl2.add_argument("--allow-dupes", action="store_true", help="确认候选都是新的，强行出提案")
    pl2.set_defaults(fn=cmd_plan)

    sub.add_parser("dedupe", help="扫相似候选").set_defaults(fn=cmd_dedupe)
    sub.add_parser("check", help="只读检查（给定时任务用）").set_defaults(fn=cmd_check)

    pr2 = sub.add_parser("reconcile", help="补推后核对并推进基线")
    pr2.add_argument("proposal_id")
    pr2.set_defaults(fn=cmd_reconcile)

    pm = sub.add_parser("merge", help="合并两条记录")
    pm.add_argument("keep", help="保留哪条")
    pm.add_argument("drop", help="退役哪条")
    pm.add_argument("--note", default="", help="合并依据，会留痕")
    pm.add_argument("--retitle", default="", help="合并后改个标题")
    pm.add_argument("--dry-run", action="store_true", help="只看不动")
    pm.set_defaults(fn=cmd_merge)

    pl = sub.add_parser("pull", help="拉取并导入本地")
    pl.add_argument("--apply", action="store_true", help="真的写进本地 records")
    pl.add_argument("--no-fetch", action="store_true", help="先不 git pull")
    pl.add_argument("--force", action="store_true", help="fetch 失败也继续")
    pl.set_defaults(fn=cmd_pull)

    pr = sub.add_parser("review", help="读提案"); pr.add_argument("proposal_id"); pr.set_defaults(fn=cmd_review)
    pa = sub.add_parser("approve", help="确认提案")
    pa.add_argument("proposal_id")
    pa.add_argument("--by", default="qiulaoshi")
    pa.add_argument("--force", action="store_true")
    pa.set_defaults(fn=cmd_approve)
    pp = sub.add_parser("publish", help="发布")
    pp.add_argument("proposal_id")
    pp.add_argument("--by", default="qiulaoshi")
    pp.add_argument("--yes", action="store_true", help="跳过确认（自己签自己）")
    pp.add_argument("--force", action="store_true", help="强制发布旧提案")
    pp.set_defaults(fn=cmd_publish)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
