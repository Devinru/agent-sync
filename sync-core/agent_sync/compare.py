"""三方比较：判断每条记录该干什么。

四个值：
    B  = 上次成功同步时的共享版本（基线）
    S  = 现在共享仓库里的版本
    L0 = 上次成功同步时的本地版本（基线）
    L  = 现在本地版本

**不用时间戳判断谁对**——跨机器时钟不可靠。只看内容 hash。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    NOOP = "noop"                  # 两边都没动
    PULL = "pull"                  # 只有远端变了 → 导入
    PUSH = "push"                  # 只有本地变了 → 提案导出
    CONFLICT = "conflict"          # 两边都变了 → 人工裁决
    NEW_PUSH = "new_push"          # 本地新记录 → 提案新增
    NEW_PULL = "new_pull"          # 远端新记录 → 新导入
    RETIRE_LOCAL = "retire_local"  # 远端退役，本地没动 → 本地标 dormant
    RETIRE_CONFLICT = "retire_conflict"  # 远端退役但本地有改动 → 停
    RESTORE = "restore"            # 本地删了但远端还在 → 问要不要恢复


@dataclass
class Mapping:
    """本地 ↔ 共享 的对应关系 + 双方基线 hash。"""
    shared_id: str
    native_id: str
    shared_hash: str          # 导入/导出时的共享内容 hash
    native_hash: str          # 导入/导出时的本地内容 hash
    origin: str = ""          # 哪来的
    updated_at: str = ""


@dataclass
class Diff:
    shared_id: str
    native_id: str | None
    action: Action
    reason: str
    local_hash: str | None = None
    remote_hash: str | None = None
    baseline_remote: str | None = None
    baseline_local: str | None = None


def compare(
    shared_now: dict[str, str],
    shared_base: dict[str, str],
    local_now: dict[str, str],
    mappings: dict[str, Mapping],
) -> list[Diff]:
    """纯函数，不读盘不写盘。

    shared_now  {shared_id: content_hash}
    shared_base {shared_id: content_hash}  上次同步时的共享状态
    local_now   {native_id: content_hash}
    mappings    {shared_id: Mapping}
    """
    out: list[Diff] = []
    all_ids = set(shared_now) | set(shared_base) | set(mappings)

    for sid in sorted(all_ids):
        m = mappings.get(sid)
        s_now = shared_now.get(sid)
        s_base = shared_base.get(sid)
        l_now = local_now.get(m.native_id) if m else None
        l_base = m.native_hash if m else None

        # ---- 本地没有映射 ----
        if m is None:
            if s_now and not s_base:
                out.append(Diff(sid, None, Action.NEW_PULL, "远端有新记录，本地没接过"))
            elif s_base and not s_now:
                out.append(Diff(sid, None, Action.NOOP, "远端删了，本地本来就没有"))
            else:
                out.append(Diff(sid, None, Action.NOOP, "已记录但无映射，跳过"))
            continue

        # ---- 远端被删掉（退役/撤回） ----
        if s_base and not s_now:
            if l_now == l_base:
                out.append(Diff(sid, m.native_id, Action.RETIRE_LOCAL,
                                "远端退役，本地没动过", l_now, None, s_base, l_base))
            else:
                out.append(Diff(sid, m.native_id, Action.RETIRE_CONFLICT,
                                "远端退役但本地又改了，需要人看", l_now, None, s_base, l_base))
            continue

        # ---- 本地被删掉 ----
        if l_base and not l_now:
            if s_now == s_base:
                out.append(Diff(sid, m.native_id, Action.RESTORE,
                                "本地删了但远端还在，问要不要恢复", None, s_now, s_base, l_base))
            else:
                out.append(Diff(sid, m.native_id, Action.CONFLICT,
                                "本地删了、远端也改了", None, s_now, s_base, l_base))
            continue

        # ---- 双方状态 ----
        r_changed = s_now != s_base
        l_changed = l_now != l_base

        if not r_changed and not l_changed:
            out.append(Diff(sid, m.native_id, Action.NOOP, "两边都没动", l_now, s_now, s_base, l_base))
        elif r_changed and not l_changed:
            out.append(Diff(sid, m.native_id, Action.PULL, "只有远端变了", l_now, s_now, s_base, l_base))
        elif l_changed and not r_changed:
            if l_now == s_now:
                # 本地改成了和远端一样 —— 直接把映射 hash 更新，不产生提案
                out.append(Diff(sid, m.native_id, Action.NOOP,
                                "本地内容和远端一致，只更新基线", l_now, s_now, s_base, l_base))
            else:
                out.append(Diff(sid, m.native_id, Action.PUSH, "只有本地变了", l_now, s_now, s_base, l_base))
        else:
            if l_now == s_now:
                out.append(Diff(sid, m.native_id, Action.NOOP,
                                "两边改成了一样的内容", l_now, s_now, s_base, l_base))
            else:
                out.append(Diff(sid, m.native_id, Action.CONFLICT,
                                "两边都改了且不一样", l_now, s_now, s_base, l_base))
    return out


def summarize(diffs: list[Diff]) -> dict[str, int]:
    from collections import Counter
    c = Counter(d.action.value for d in diffs)
    return dict(c)
