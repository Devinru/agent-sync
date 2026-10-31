"""语义去重：先筛候选，再交模型判定。

**这一层是确定性的**——同样输入永远同样输出，能测、能复现。
它的任务不是「判断重不重复」，是「把不可能的排掉，把可能的挑出来」。

真正「是不是同一件事」交给模型看，但这层必须先跑：
全库两两比对交给模型，几百条就是几万次判断，又慢又贵还不稳定。

用法：
    from agent_sync.dedupe import find_candidates
    pairs = find_candidates(new_records, pool_records)
    # 每对带分数和命中原因，交给模型逐对判

**soul / rule 不进这个流程。** 见 NO_AUTO_MERGE。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .protocol import Record

# 这些类型永远不自动合并：报差异给人看，人决定
NO_AUTO_MERGE = {"soul", "rule"}

# 候选阈值（调过，别随手改，改了要重跑测试）
TH_SCORE = 0.25      # 综合分阈值，故意压低：宁可多给几对假候选，也别漏真重复
TH_LEN_RATIO = 0.28  # 长度比下限（太短的不比）

# 权重：光靠双字重合抓不住「同一件事、完全不同措辞」，
# 所以给「同名实体」（用户档案 这种）单独一层，权重最高。
W_BODY = 0.30        # 双字重合
W_TITLE = 0.15       # 标题相似
W_LATIN = 0.35       # 英文/代码标识重合 —— 最强信号
W_TRI = 0.20         # 中文三字重合

_PUNCT = re.compile(r"[\s\u3000\-—_.,，。、；;：:！!？?（）()\[\]【】「」『』\"'“”‘’·~～*#>`|/\\+=]+")


def normalize(text: str) -> str:
    """NFKC + 去标点 + 小写。只做无损归一，不改语义。"""
    t = unicodedata.normalize("NFKC", text or "")
    return _PUNCT.sub("", t).lower()


def bigrams(s: str) -> set[str]:
    """中文按双字切片。单字信息量太低，整句又太粗。"""
    t = normalize(s)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


_LATIN = re.compile(r"[a-z][a-z0-9_.]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def entities(text: str) -> tuple[set[str], set[str]]:
    """抽两类实体。

    latin：用户档案、MEMORY.md、renpy 这种——辨识度极高，几乎可以说
           「同一条记忆才会提到同一个标识符」。
    tri：中文三字窗口。三字比双字有辨识度得多，
         三字片段能锁定专有名词，同前缀的词就不会跟它撞。
    """
    t = normalize(text)
    latin = set(_LATIN.findall(t))
    tri: set[str] = set()
    for seg in _CJK.findall(t):
        if len(seg) >= 3:
            tri |= {seg[i:i + 3] for i in range(len(seg) - 2)}
        elif seg:
            tri.add(seg)
    return latin, tri


def overlap(a: set, b: set) -> float:
    """重合率。用 min 做分母——短的那个说了算，
    免得「一条很长一条很短」被稀释成不相似。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def seq_ratio(a: str, b: str) -> float:
    """标题相似度：先看前缀包含，再退到双字重合。"""
    x, y = normalize(a), normalize(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    # 一个包含另一个（短的那个至少 4 字）
    if len(x) >= 4 and len(y) >= 4:
        if x in y or y in x:
            return 0.92
    return jaccard(bigrams(x), bigrams(y))


@dataclass
class Candidate:
    a_id: str
    b_id: str
    title_sim: float
    body_sim: float
    len_ratio: float
    same_kind: bool
    score: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {
            "a_id": self.a_id, "b_id": self.b_id,
            "title_sim": round(self.title_sim, 3),
            "body_sim": round(self.body_sim, 3),
            "len_ratio": round(self.len_ratio, 3),
            "same_kind": self.same_kind,
            "score": round(self.score, 3),
            "reasons": self.reasons,
        }


def _is_candidate(a: Record, b: Record) -> Candidate | None:
    if a.id == b.id:
        return None
    if a.kind in NO_AUTO_MERGE or b.kind in NO_AUTO_MERGE:
        return None
    if a.status in ("superseded", "retracted") or b.status in ("superseded", "retracted"):
        return None

    full_a, full_b = f"{a.title} {a.body}", f"{b.title} {b.body}"
    title_sim = seq_ratio(a.title, b.title)
    body_sim = jaccard(bigrams(a.body[:2000]), bigrams(b.body[:2000]))

    la_latin, la_tri = entities(full_a)
    lb_latin, lb_tri = entities(full_b)
    latin_sim = overlap(la_latin, lb_latin)
    tri_sim = overlap(la_tri, lb_tri)

    la, lb = len(normalize(a.body)), len(normalize(b.body))
    len_ratio = min(la, lb) / max(la, lb) if max(la, lb) else 0.0
    same_kind = a.kind == b.kind

    score = (W_BODY * body_sim + W_TITLE * title_sim
             + W_LATIN * latin_sim + W_TRI * tri_sim)

    reasons = []
    if latin_sim > 0:
        shared = sorted(la_latin & lb_latin)[:4]
        reasons.append(f"同名标识（{', '.join(shared)}）")
    if tri_sim >= 0.15:
        reasons.append(f"中文短语重合（{tri_sim:.2f}）")
    if body_sim >= 0.30:
        reasons.append(f"正文重合（{body_sim:.2f}）")
    if title_sim >= 0.60:
        reasons.append(f"标题像（{title_sim:.2f}）")

    if score < TH_SCORE:
        return None

    if same_kind:
        score += 0.05
        reasons.append("同 kind")
    if len_ratio < TH_LEN_RATIO:
        score *= 0.75
        reasons.append("长度差很大")
    if not reasons:
        reasons.append("综合分达标")

    return Candidate(
        a_id=a.id, b_id=b.id,
        title_sim=title_sim, body_sim=body_sim, len_ratio=len_ratio,
        same_kind=same_kind, score=min(score, 1.0), reasons=reasons,
    )


def find_candidates(
    incoming: dict[str, Record],
    pool: dict[str, Record],
    limit: int = 80,
) -> list[Candidate]:
    """incoming 里每条，去 pool 里找像的。

    pool 可以包含 incoming 自己（会给排除）。
    返回按 score 从高到低，最多 limit 对。
    """
    out: list[Candidate] = []
    pool_ids = list(pool)
    for rid, rec in incoming.items():
        for pid in pool_ids:
            c = _is_candidate(rec, pool[pid])
            if c:
                out.append(c)
    out.sort(key=lambda x: x.score, reverse=True)

    # 同一对被两个方向各算一次的情况去重
    seen, deduped = set(), []
    for c in out:
        key = tuple(sorted((c.a_id, c.b_id)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped[:limit]


def render_pairs(cands: list[Candidate], records: dict[str, Record]) -> str:
    """给人/模型看的候选清单。"""
    if not cands:
        return "没有找到相似记录。\n"
    lines = [f"找到 {len(cands)} 对相似候选（按相似度排序）", ""]
    for i, c in enumerate(cands, 1):
        a, b = records.get(c.a_id), records.get(c.b_id)
        lines.append(f"### {i}. 相似度 {c.score:.2f}")
        lines.append("")
        lines.append(f"- A `{c.a_id[:8]}` [{a.kind if a else '?'}] {a.title if a else '?'}")
        lines.append(f"  {preview(a.body) if a else ''}")
        lines.append(f"- B `{c.b_id[:8]}` [{b.kind if b else '?'}] {b.title if b else '?'}")
        lines.append(f"  {preview(b.body) if b else ''}")
        lines.append(f"- 依据：{'、'.join(c.reasons)}")
        lines.append("")
    lines.append("**这些只是候选，不是判定结果。** 需要逐对人看：是不是同一件事？")
    return "\n".join(lines) + "\n"


def preview(body: str, n: int = 90) -> str:
    t = " ".join((body or "").split())
    return t[:n] + ("…" if len(t) > n else "")
