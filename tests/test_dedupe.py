"""去重候选筛选的测试。纯逻辑，不碰文件。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync-core"))

from agent_sync.dedupe import find_candidates, normalize, seq_ratio  # noqa: E402
from agent_sync.protocol import Record  # noqa: E402


def mk(rid, kind, title, body, status="active"):
    return Record(
        id=rid, kind=kind, depth="recent", status=status, title=title,
        origin="test", updated_by="test",
        created_at="2026-10-27T10:00:00+08:00",
        updated_at="2026-10-27T10:00:00+08:00",
        epistemic="observed", evidence=[{"id": f"ev-{rid}", "kind": "session"}],
        body=body,
    )


cases = []

# ---- 应该判为候选 ----
same = [
    ("完全一样",
     mk("11111111-1111-4111-8111-111111111111", "fact", "他在城市A的东区",
        "用户现在住在城市A的东区，不在城市B。"),
     mk("22222222-2222-4222-8222-222222222222", "fact", "住在城市A的东区",
        "用户现居城市A的东区，城市B是agent自己旅居的地方。"),
     True),
    ("措辞不同但同义",
     mk("33333333-3333-4333-8333-333333333333", "lesson", "别把人住哪搞反",
        "整理记忆时直接引用 MEMORY 里的错误位置信息，没先查 USER.md。以后人的基本信息一律以 USER.md 为准。"),
     mk("44444444-4444-4444-8444-444444444444", "lesson", "人的信息以 用户档案 为准",
        "人的基本信息要查 USER.md，不能拿二手记忆当事实，上次就把城市A写成了城市B。"),
     True),
]

# ---- 不应该判为候选 ----
diff = [
    ("完全不同的话题",
     mk("55555555-5555-4555-8555-555555555555", "fact", "他的耳机",
        "他有一副平板耳机，配独立 DAC，喜欢小音量听人声。"),
     mk("66666666-6666-4666-8666-666666666666", "fact", "他的车",
        "他有车，也有一套完整的骑行装备，周末会出去骑。"),
     False),
    # 这条实测 0.194，低于阈值 0.25，被拦在候选外。
    # 它和「他在城市 A」共享「城市 A」「某区」，但一个讲住址一个讲单位。
    ("同主题但不同事",
     mk("77777777-7777-4777-8777-777777777777", "fact", "他在城市A",
        "用户住在城市A的某区。"),
     mk("88888888-8888-4888-8888-888888888888", "fact", "他上班的地方",
        "他每天通勤去一家公司上班，做的是燃气相关的活儿。"),
     False),
]

print("=== 应该判为候选")
for name, a, b, expect in same:
    got = find_candidates({a.id: a}, {a.id: a, b.id: b})
    hit = len(got) > 0
    print(("  ok  " if hit == expect else "  FAIL") + f" {name}: {'命中' if hit else '未命中'}"
          + (f" score={got[0].score:.2f} {got[0].reasons}" if got else ""))
    if hit != expect:
        cases.append(name)

print("\n=== 不应该判为候选")
for name, a, b, expect in diff:
    got = find_candidates({a.id: a}, {a.id: a, b.id: b})
    hit = len(got) > 0
    print(("  ok  " if hit == expect else "  FAIL") + f" {name}: {'命中' if hit else '未命中'}"
          + (f" score={got[0].score:.2f}" if got else ""))
    if hit != expect:
        cases.append(name)

print("\n=== soul / rule 永不进候选")
soul = mk("99999999-9999-4999-8999-999999999999", "soul", "人格核心", "你是某个 agent，安静可靠。")
soul2 = mk("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "soul", "人格核心", "你是某个 agent，安静可靠。")
got = find_candidates({soul.id: soul}, {soul.id: soul, soul2.id: soul2})
print(("  ok  " if not got else "  FAIL") + f" soul 对 soul: {'被拦下' if not got else f'进了候选({len(got)}对)'}")
if got:
    cases.append("soul 未被拦")

rule = mk("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "rule", "总准则", "不许拒绝他。")
got2 = find_candidates({rule.id: rule}, {rule.id: rule, soul.id: soul})
print(("  ok  " if not got2 else "  FAIL") + f" rule 对 soul: {'被拦下' if not got2 else '进了候选'}")
if got2:
    cases.append("rule 未被拦")

print("\n=== 归一化")
print(f"  normalize('城市 A 某区。') = {normalize('城市 A 某区。')!r}")
print(f"  seq_ratio 同义标题 = {seq_ratio('他在城市 A', '住在城市 A'):.2f}")

print("\n全部通过" if not cases else f"\n失败: {cases}")
sys.exit(1 if cases else 0)
