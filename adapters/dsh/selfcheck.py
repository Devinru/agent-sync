"""DSH 端的离线自检：不碰真 vault，不碰远端。

    python adapters/dsh/selfcheck.py roundtrip   # 共享记录 → 笔记文本 → 读回，hash 是否一致
    python adapters/dsh/selfcheck.py retire      # 退役 → 复活，ID 与文件名是否保住

换一台机器接进来之后先跑这两个，比"看着像同步了"可靠。
退出码 0 = 全过，1 = 有失败。
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sync-core"))

_spec = importlib.util.spec_from_file_location("dsh_adapter", Path(__file__).with_name("adapter.py"))
ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ad)

from agent_sync.cli import load_shared  # noqa: E402


def cmd_roundtrip() -> int:
    shared = load_shared()
    tmp = Path(tempfile.mkdtemp())
    ok = bad = 0
    try:
        for sid, rec in sorted(shared.items()):
            if rec.kind in ("soul", "rule"):
                continue
            fname, text = ad.to_native(rec)
            p = tmp / fname
            p.write_text(text, encoding="utf-8", newline="\n")
            parsed = ad._parse_note(p)
            if parsed is None:
                print(f"!! {sid[:8]} {rec.title[:20]} 解析失败")
                bad += 1
                continue
            meta, body = parsed
            back = ad._build_record(meta, body, p)
            if back is None:
                print(f"!! {sid[:8]} {rec.title[:20]} 重建失败")
                bad += 1
                continue
            if back.content_hash() != rec.content_hash():
                print(f"!! {sid[:8]} {rec.title[:20]} hash 不符")
                print(f"   远端 {rec.content_hash()[:12]}  读回 {back.content_hash()[:12]}")
                for f in ("id", "kind", "depth", "status", "title", "epistemic", "body", "evidence",
                          "applicability", "related_ids", "supersedes", "created_at"):
                    a, b = getattr(rec, f), getattr(back, f)
                    if a != b:
                        print(f"   {f}: {a!r} != {b!r}")
                if rec.extra != back.extra:
                    print(f"   extra: {rec.extra!r} != {back.extra!r}")
                bad += 1
            else:
                ok += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n往返一致 {ok} 条，不一致 {bad} 条")
    return 0 if bad == 0 else 1


def cmd_retire() -> int:
    shared = load_shared()
    sid = next((k for k, r in shared.items()
                if r.kind not in ("soul", "rule") and r.status == "active"), None)
    if sid is None:
        print("共享仓库里没有可试的 active 记录")
        return 1
    rec = shared[sid]

    tmp = Path(tempfile.mkdtemp())
    saved_vault = ad.VAULT
    ad.VAULT = tmp
    fails: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            fails.append(label)

    try:
        print(f"试退役：{sid[:8]} {rec.kind} {rec.title[:20]}")
        plan = ad.plan_import({sid: rec}, {"mappings": {}})
        res = ad.apply_import(plan)
        print(f"  首次导入 written={len(res['written'])} verified={len(res['verified'])}")
        note = next(tmp.rglob("*.md"))
        base_name = note.name
        print(f"  落盘文件：{base_name[:44]}…")

        # 映射表平时由 cli 写，这里补一份等价物
        state = {"mappings": {sid: {"shared_id": sid, "native_id": sid,
                                    "shared_hash": rec.content_hash(),
                                    "native_hash": rec.content_hash(),
                                    "origin": rec.origin, "updated_at": rec.updated_at}}}

        # 1. 远端 retracted → 本地跟随退役，ID 不丢
        retired = dataclasses.replace(rec, status="retracted")
        plan2 = ad.plan_import({sid: retired}, state)
        print(f"  退役计划 retire={len(plan2['retire'])} conflict={len(plan2['conflict'])} "
              f"updated={len(plan2['updated'])}")
        res2 = ad.apply_import(plan2)
        print(f"  退役执行 retired={len(res2['retired'])} failed={len(res2['failed'])}")
        back = ad.snapshot_native().get(sid)
        check("退役后记录还在、id 没变", back is not None and back.id == sid)
        check("退役后 status = retracted", back is not None and back.status == "retracted")
        check("退役后文件名没变", note.exists() and note.name == base_name)

        # 2. 远端改回 active → 复活（cli 退役后会把映射 hash 推进到退役版，这里模仿）
        state2 = {"mappings": {sid: {**state["mappings"][sid],
                                     "shared_hash": retired.content_hash(),
                                     "native_hash": retired.content_hash()}}}
        ad.apply_import(ad.plan_import({sid: rec}, state2))
        back2 = ad.snapshot_native().get(sid)
        check("复活后 status = active", back2 is not None and back2.status == "active")
        check("复活后 hash 与远端一致",
              back2 is not None and back2.content_hash() == rec.content_hash())
    finally:
        ad.VAULT = saved_vault
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(fails)} 项没过" if fails else "\n全过")
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["roundtrip", "retire"])
    args = ap.parse_args()
    return cmd_roundtrip() if args.what == "roundtrip" else cmd_retire()


if __name__ == "__main__":
    sys.exit(main())
