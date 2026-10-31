"""compare.py 的判定表测试。纯逻辑，不碰文件。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync-core"))

from agent_sync.compare import Action, Mapping, compare, summarize  # noqa: E402


def run(shared_now, shared_base, local_now, mappings):
    return compare(shared_now, shared_base, local_now, mappings)


def one(shared_now, shared_base, local_now, mappings):
    d = run(shared_now, shared_base, local_now, mappings)
    assert len(d) == 1, f"期望 1 条，实际 {len(d)}"
    return d[0].action


M = {"s1": Mapping("s1", "n1", "h-base", "h-base")}

cases = [
    # (名字, 期望, shared_now, shared_base, local_now, mappings)
    ("两边都没动", Action.NOOP,
     {"s1": "h-base"}, {"s1": "h-base"}, {"n1": "h-base"}, M),
    ("只有远端变了", Action.PULL,
     {"s1": "h-remote"}, {"s1": "h-base"}, {"n1": "h-base"}, M),
    ("只有本地变了", Action.PUSH,
     {"s1": "h-base"}, {"s1": "h-base"}, {"n1": "h-local"}, M),
    ("两边改成一样", Action.NOOP,
     {"s1": "h-same"}, {"s1": "h-base"}, {"n1": "h-same"}, M),
    ("两边都改不一样", Action.CONFLICT,
     {"s1": "h-remote"}, {"s1": "h-base"}, {"n1": "h-local"}, M),
    ("远端新记录", Action.NEW_PULL,
     {"s1": "h-new"}, {}, {}, {}),
    ("远端退役本地没动", Action.RETIRE_LOCAL,
     {}, {"s1": "h-base"}, {"n1": "h-base"}, M),
    ("远端退役本地又改了", Action.RETIRE_CONFLICT,
     {}, {"s1": "h-base"}, {"n1": "h-local"}, M),
    ("本地删了远端没动", Action.RESTORE,
     {"s1": "h-base"}, {"s1": "h-base"}, {}, M),
    ("本地删了远端也改", Action.CONFLICT,
     {"s1": "h-remote"}, {"s1": "h-base"}, {}, M),
]

fails = []
for name, expect, sn, sb, ln, mp in cases:
    try:
        got = one(sn, sb, ln, mp)
        ok = got == expect
    except AssertionError as e:
        got, ok = f"<{e}>", False
    print(("  ok  " if ok else "  FAIL") + f" {name}: 期望 {expect.value} 得到 {got}")
    if not ok:
        fails.append(name)

# 多记录汇总
# a 两边没动 / b 只有本地改了 / c 只有远端改了
diffs = run(
    {"a": "1", "b": "1", "c": "9"},
    {"a": "1", "b": "1", "c": "3"},
    {"na": "1", "nb": "9", "nc": "3"},
    {"a": Mapping("a", "na", "1", "1"),
     "b": Mapping("b", "nb", "2", "2"),
     "c": Mapping("c", "nc", "3", "3")},
)
s = summarize(diffs)
print(f"\n汇总测试: {s}")
assert s.get("noop") == 1 and s.get("pull") == 1 and s.get("push") == 1, s
print("\n全部通过" if not fails else f"\n失败 {len(fails)} 项: {fails}")
sys.exit(1 if fails else 0)
