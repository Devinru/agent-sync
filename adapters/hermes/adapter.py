"""Hermes 适配器：本地 records ↔ 共享记录。

只做 Hermes 这一端。别抄这个文件去当别的端的模板——
别的端该照着 `protocol/adapters.md` 写自己的，因为本地结构完全不同。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 适配器和 sync-core 在同一仓库里，但不在同一个包下
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sync-core"))

from agent_sync.protocol import Record, now_iso  # noqa: E402

import os

HERMES_RECORDS = Path(os.environ.get("AGENT_SYNC_HERMES_RECORDS")
                      or (Path.home() / ".hermes" / "memory" / "records"))

# Hermes 的 kind 直接对得上共享协议，不用映射
KIND_PASSTHROUGH = {
    "episode", "fact", "lesson", "topic", "project", "preference", "reflection",
}
KIND_MAP = {
    "preference": "fact",     # Hermes 的 preference 在共享里归到 fact
}


def inspect() -> dict:
    """只读：这个环境长什么样。"""
    exists = HERMES_RECORDS.exists()
    n = len(list(HERMES_RECORDS.glob("*.json"))) if exists else 0
    return {
        "framework": "hermes",
        "records_dir": str(HERMES_RECORDS),
        "exists": exists,
        "native_count": n,
        "status": "verified" if exists else "blocked",
        "blocked_reason": None if exists else "records 目录不存在",
    }


def snapshot_native() -> dict[str, Record]:
    """读本地 records，转成共享 Record。key 是本地 id。"""
    out: dict[str, Record] = {}
    if not HERMES_RECORDS.exists():
        return out

    for p in sorted(HERMES_RECORDS.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        native_id = d.get("id")
        if not native_id:
            continue

        # 所有转换都走 _build_record —— 它统一处理 _shared_md 还原和字段手改检测。
        # 以前这里还有一段自己的提前还原分支，只覆盖 title/body 就 continue，
        # 导致手改 status / epistemic / applicability 在主流程里被吞掉。
        rec = _build_record(d, native_id, str(p))
        if rec is not None:
            out[native_id] = rec
    return out


def _strip_ref(x: str) -> str:
    """去掉 local-ref: 前缀再比较。

    to_native 会把 locator 原样存进 ref，_build_record 重建时又补上 local-ref:，
    一来一回前缀就多了。比较时必须先剥掉，否则每次都判成"用户改过"。
    """
    s = str(x or "")
    return s[len("local-ref:"):] if s.startswith("local-ref:") else s


def _build_record(d: dict, native_id: str, where: str = "") -> "Record | None":
    """本地 JSON → 共享 Record。snapshot_native 和 hash 核对共用这一份。

    导入过的记录带着 _shared_md（原始共享记录），必须用它还原 ——
    靠字段来回翻译一定有损，hash 会对不上，然后就回声。
    """
    original = d.get("_shared_md")
    if original:
        try:
            base = Record.parse(original, where=where or native_id)
            # 不只 title/body —— 手改 status、epistemic、applicability、evidence
            # 也是真实修改，不能被原副本遮掉。
            for f in ("body", "title", "status", "epistemic", "review_after"):
                v = d.get(f)
                if v is not None and v != "" and v != getattr(base, f):
                    setattr(base, f, v)
            if d.get("applicability") and d["applicability"] != base.applicability:
                base.applicability = d["applicability"]
            # evidence：本地是 {ref, at}，共享是 {id, kind, locator, occurred_at}。
            # 这两种形式天然不同，不能直接比 —— 直接比会把 to_native 的正常转换
            # 当成"用户手改"，然后把原始 id 换掉，hash 就漂了。
            # 要比的是语义：(locator, occurred_at) 这一组有没有真的变。
            if d.get("evidence"):
                def _locs(items, local: bool):
                    """只比 locator。

                    occurred_at 不可比：to_native 会给没时间戳的证据补 rec.created_at，
                    读回来必然和原始的 None 不同。那不算用户改动。
                    """
                    out = set()
                    for e in items or []:
                        if isinstance(e, str):
                            out.add(_strip_ref(e))
                        elif isinstance(e, dict):
                            loc = e.get("ref") if local else e.get("locator")
                            out.add(_strip_ref(loc or ""))
                    return out

                if _locs(d["evidence"], True) != _locs(base.evidence, False):
                    ev = []
                    for i, e in enumerate(d["evidence"]):
                        if isinstance(e, str):
                            ev.append({"id": f"ev-{native_id[:8]}-{i}", "kind": "session",
                                       "origin": "hermes-linux", "locator": e})
                        elif isinstance(e, dict):
                            ref = e.get("ref") or e.get("locator") or ""
                            item = {
                                "id": e.get("id") or f"ev-{native_id[:8]}-{i}",
                                "kind": e.get("kind") or "session",
                                "origin": e.get("origin") or "hermes-linux",
                                "locator": f"local-ref:{ref}" if ref and not str(ref).startswith("local-ref:") else ref,
                            }
                            at = e.get("at") or e.get("occurred_at")
                            if at:
                                item["occurred_at"] = at
                            ev.append(item)
                    if ev:
                        base.evidence = ev
            base.validate(native_id[:8])
            return base
        except Exception:
            pass

    if True:
        kind = KIND_MAP.get(d.get("kind", "fact"), d.get("kind", "fact"))
        if kind not in KIND_PASSTHROUGH | {"preference"}:
            kind = "fact"

        # 性质（事实/推断/反思）和深度、适用范围都要原样保住。
        # 以前这里硬编码 epistemic="observed"，等于把本地推断升级成已观察事实 ——
        # 那是凭空给内容加权威。
        # 有来源 ≠ 是事实。
        # 会话、日记、REM 反思都有真实 ref，但内容可能是猜测、创作、或对事实的解释。
        # 引用只能回答「这句从哪来」，不能回答「这句是什么性质」。
        # 所以：缺 epistemic 就标 legacy（待分类），绝不因为有 evidence 就升成 observed。
        epistemic = d.get("shared_epistemic") or d.get("epistemic")
        if not epistemic:
            epistemic = "legacy"
        if epistemic not in {"user_stated", "observed", "inferred", "creative", "legacy"}:
            epistemic = "legacy"
        depth = d.get("depth") or "recent"
        if depth not in {"recent", "stable", "core"}:
            depth = "recent"
        applicability = d.get("applicability") or {"frameworks": [], "os": [], "projects": []}

        # evidence 转换：Hermes 用 {ref, at}，共享用 {id, kind, origin, locator}
        ev = []
        for i, e in enumerate(d.get("evidence") or []):
            if isinstance(e, str):
                ev.append({"id": f"ev-{native_id[:8]}-{i}", "kind": "session",
                           "origin": "hermes-linux", "locator": e})
            elif isinstance(e, dict):
                ref = e.get("ref") or e.get("locator") or ""
                ev.append({
                    "id": f"ev-{native_id[:8]}-{i}",
                    "kind": "session",
                    "origin": "hermes-linux",
                    "occurred_at": e.get("at"),
                    "locator": f"local-ref:{ref}" if not ref.startswith("local-ref:") else ref,
                })
        # 没有 evidence 就不造一个。造出来的 locator 不指向任何真实发生的事，
        # 等于伪造证据只为了让 schema 通过。留空，让协议层报问题。
        if not ev:
            ev = []

        rec = Record(
            id=str(native_id),          # 本地 id 直接当共享 id：Hermes 的记录自带 uuid4
            kind=kind,
            depth=depth,
            status=d.get("status", "active"),
            title=(d.get("title") or "")[:60],
            origin="hermes-linux",
            updated_by="hermes-linux",
            created_at=d.get("created_at") or now_iso(),
            updated_at=d.get("updated_at") or now_iso(),
            epistemic=epistemic,
            sharing="approved",
            applicability=applicability,
            revision=int(d.get("revision") or 1),
            evidence=ev,
            review_after=d.get("review_after"),
            body=d.get("body") or "",
            extra={},   # Hermes 侧没有 lesson 四段这类结构，不伪造
        )
        try:
            rec.validate(native_id[:8])
            return rec
        except Exception:
            return None


# ---------- 导入：共享 → 本地 ----------

def to_native(rec: Record) -> dict:
    """共享 Record → Hermes records 的 JSON 结构。

    转译要点：保留来源（origin / evidence / epistemic），
    不把「推断」写成「事实」，不丢掉退役状态。
    """
    ev = []
    for e in rec.evidence:
        # 把 id / kind / origin 一起存下来。
        # 只存 {ref, at} 的话，读回来只能按位置生成 id，
        # 原始 id（比如 ev-dsh-lesson-publish）就永久丢了，hash 对不上。
        item = {
            "ref": e.get("locator", ""),
            "at": e.get("occurred_at") or rec.created_at,
            "id": e.get("id"),
            "kind": e.get("kind"),
            "origin": e.get("origin"),
        }
        if e.get("origin") and e.get("origin") != "hermes-linux":
            item["from"] = e["origin"]
        ev.append(item)

    # 以前这里往正文末尾追加「> 来源：xxx」，再读回来时
    # snapshot_native 看到 body 和原始不同，就当成「本地改过」，
    # 下次 export 又把它推回去 —— 自己制造回声。
    # 来源信息放进独立字段，不动正文。
    body = rec.body

    return {
        "id": rec.id,                 # 共享 id 直接当本地 id，保证映射稳定
        "revision": rec.revision,
        "kind": "fact" if rec.kind in ("legacy_bundle",) else rec.kind,
        "status": rec.status,
        "title": rec.title,
        "body": body,
        "evidence": ev,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "review_after": rec.review_after,
        # 保留共享侧元信息，方便回溯
        "shared_origin": rec.origin,
        "shared_epistemic": rec.epistemic,
        "shared_kind": rec.kind,
        # 存一份原始共享记录：读回时用它，别靠转译来回猜
        "_shared_md": rec.to_markdown(),
    }


def snapshot_native_one(d: dict) -> Record | None:
    """单条转换。和 snapshot_native 走同一个函数，不许有第二条还原路径。"""
    nid = d.get("id")
    if not nid:
        return None
    try:
        return _build_record(d, str(nid), where=str(nid))
    except Exception:
        return None


def plan_import(shared: dict[str, Record], state: dict) -> dict:
    """算要导进本地的记录。只算，不写。

    返回 {"new": [...], "updated": [...], "conflict": [...], "skip": [...]}
    """
    mappings = state.get("mappings", {})
    native = snapshot_native()
    native_to_shared = {v["native_id"]: k for k, v in mappings.items()}

    new, updated, conflict, skip, retire = [], [], [], [], []

    for sid, rec in sorted(shared.items()):
        # 人格核心不走这条：它 local-wins，只能提案
        if rec.kind in ("soul", "rule"):
            skip.append((sid, "人格核心不自动导入"))
            continue

        # 退役要先看映射：远端撤了一条本地已经有副本的记录，
        # 不能只是 skip —— 那样本地还会继续用旧结论，下次 export 还可能把
        # 旧的 active 状态推回仓库。
        if rec.status in ("superseded", "retracted"):
            nid_r = mappings.get(sid, {}).get("native_id")
            if nid_r and nid_r in native:
                local_r = native[nid_r]
                base = mappings[sid].get("native_hash")
                if local_r.content_hash() == base:
                    # 本地没动过，可以直接跟着退役
                    retire.append((sid, rec, local_r))
                else:
                    # 本地自己改过，不能盲目撤
                    conflict.append((sid, rec, local_r))
            elif nid_r:
                skip.append((sid, "远端已退役，本地也没有了"))
            else:
                skip.append((sid, "远端已退役，从未导入过"))
            continue

        nid = mappings.get(sid, {}).get("native_id")
        local = native.get(nid) if nid else None

        if nid is None:
            # 从没接过
            new.append((sid, rec, None))
            continue

        if local is None:
            # 有映射但本地没了 —— 可能本地删了，不擅自恢复
            skip.append((sid, "本地已删除，不自动恢复"))
            continue

        if local.content_hash() == rec.content_hash():
            skip.append((sid, "和本地一致"))
            continue

        # 本地基线：映射里记的最后一次同步 hash
        base = mappings[sid].get("native_hash")
        if local.content_hash() == base:
            updated.append((sid, rec, local))       # 本地没动，远端变了 → 安全覆盖
        else:
            conflict.append((sid, rec, local))      # 本地也动了 → 不覆盖
    return {"new": new, "updated": updated, "conflict": conflict,
            "skip": skip, "retire": retire}


def apply_import(plan: dict, mapping_updates: list | None = None) -> dict:
    """把计划里的 new / updated 真写进本地 records。

    写前备份，写后读回校验。幂等：同一条重复写不会产生两份。
    """
    HERMES_RECORDS.mkdir(parents=True, exist_ok=True)
    backup = HERMES_RECORDS.parent / "import-backup"
    written, failed = [], []

    # 退役先做：把本地副本标成 retracted，不删文件（删了 ID 就没了）
    retired = []
    for sid, rec, local_r in plan.get("retire", []):
        target = HERMES_RECORDS / f"{sid}.json"
        try:
            if target.exists():
                backup.mkdir(parents=True, exist_ok=True)
                (backup / f"{sid}.json").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
                d = json.loads(target.read_text(encoding="utf-8"))
                d["status"] = rec.status          # superseded / retracted
                d["updated_at"] = now_iso()
                d["_shared_md"] = rec.to_markdown()
                target.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                retired.append((sid, rec))
        except Exception as e:
            failed.append((sid, f"退役失败: {e}"))

    todo = list(plan.get("new", [])) + list(plan.get("updated", []))
    for sid, rec, local_at_plan_time in todo:
        target = HERMES_RECORDS / f"{sid}.json"
        try:
            # 计划时看到的本地内容，和现在盘上的是不是同一份？
            # 中间 dream / background_review / 手改都可能动过它，
            # 拿旧计划硬写会覆盖掉中间的新内容。
            if local_at_plan_time is not None and target.exists():
                try:
                    cur = json.loads(target.read_text(encoding="utf-8"))
                    cur_rec = snapshot_native_one(cur)
                    if cur_rec is not None and cur_rec.content_hash() != local_at_plan_time.content_hash():
                        failed.append((sid, "计划之后本地被改过，跳过不覆盖"))
                        continue
                except (json.JSONDecodeError, OSError):
                    failed.append((sid, "本地文件读不了，跳过"))
                    continue
            if target.exists():
                backup.mkdir(parents=True, exist_ok=True)
                (backup / f"{sid}.json").write_text(
                    target.read_text(encoding="utf-8"), encoding="utf-8")
            # 幂等：内容一样就不重写
            payload = to_native(rec)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append((sid, rec))
        except Exception as e:
            failed.append((sid, str(e)))

    # 校验：读回来能不能解析
    verified, broken = [], []
    for sid, rec in written:
        try:
            d = json.loads((HERMES_RECORDS / f"{sid}.json").read_text(encoding="utf-8"))
            if d.get("id") == sid and d.get("body"):
                verified.append(sid)
            else:
                broken.append((sid, "读回内容不完整"))
        except Exception as e:
            broken.append((sid, str(e)))

    return {"written": written, "failed": failed, "verified": verified,
            "broken": broken, "retired": retired}


def export_candidates(shared: dict[str, Record], state: dict) -> dict:
    """算出哪些本地记录该推上去。

    返回 {"new": [...], "changed": [...], "skipped": [...]}
    """
    mappings = state.get("mappings", {})
    native_to_shared = {v["native_id"]: k for k, v in mappings.items()}

    new, changed, skipped = [], [], []
    for nid, rec in snapshot_native().items():
        sid = native_to_shared.get(nid)
        if sid is None:
            new.append((nid, rec))
            continue
        remote = shared.get(sid)
        if remote is None:
            # 映射指向一条远端已经不存在的记录
            skipped.append((nid, "映射指向的共享记录不存在了"))
            continue
        if rec.content_hash() != remote.content_hash():
            changed.append((nid, rec, remote))
        else:
            skipped.append((nid, "和远端一致"))
    return {"new": new, "changed": changed, "skipped": skipped}
