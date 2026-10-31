"""记录格式：解析、序列化、内容 hash、校验。

协议 v2.0 唯一权威实现。所有适配器都用这一份，不要各写各的。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("需要 pyyaml：pip install pyyaml")

SCHEMA_VERSION = 2
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)

KINDS = {
    "soul", "rule", "user", "episode", "fact", "lesson",
    "topic", "project", "reflection", "legacy_bundle",
}
DEPTHS = {"recent", "stable", "core"}
STATUSES = {"active", "dormant", "superseded", "retracted"}
EPISTEMIC = {"user_stated", "observed", "inferred", "creative", "legacy"}
EVIDENCE_KINDS = {"session", "tool_result", "user_stated", "file", "legacy"}
SHARING = {"approved"}

# 比较内容 hash 时排除的管理字段
_MANAGED = {"revision", "updated_at", "updated_by"}

MAX_BODY_BYTES = 65536
MAX_HEADER_BYTES = 4096


class RecordError(ValueError):
    """记录不合法。消息要能直接给人看。"""


@dataclass
class Record:
    id: str
    kind: str
    depth: str
    status: str
    title: str
    origin: str
    updated_by: str
    created_at: str
    updated_at: str
    epistemic: str
    sharing: str = "approved"
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    applicability: dict = field(default_factory=lambda: {"frameworks": [], "os": [], "projects": []})
    evidence: list = field(default_factory=list)
    related_ids: list = field(default_factory=list)
    supersedes: list = field(default_factory=list)
    review_after: str | None = None
    body: str = ""
    # 类型专属字段（lesson / topic / migration …）原样保留
    extra: dict = field(default_factory=dict)

    # ---------- 读 ----------

    @classmethod
    def parse(cls, text: str, where: str = "<memory>") -> "Record":
        m = FRONTMATTER_RE.match(text)
        if not m:
            raise RecordError(f"{where}: 缺少 YAML front matter")
        header, body = m.group(1), m.group(2)
        if len(header.encode()) > MAX_HEADER_BYTES:
            raise RecordError(f"{where}: YAML 头部超过 {MAX_HEADER_BYTES} 字节")
        if len(body.encode()) > MAX_BODY_BYTES:
            raise RecordError(f"{where}: 正文超过 {MAX_BODY_BYTES} 字节")

        try:
            # safe_load 已经禁止自定义 tag；duplicate key 由 loader 抛错
            meta = yaml.safe_load(header)
        except yaml.YAMLError as e:
            raise RecordError(f"{where}: YAML 解析失败 —— {e}") from e
        if not isinstance(meta, dict):
            raise RecordError(f"{where}: front matter 不是映射")

        known = {f for f in cls.__dataclass_fields__ if f not in ("body", "extra")}
        extra = {k: v for k, v in meta.items() if k not in known}

        try:
            rec = cls(body=body.strip(), extra=extra, **{k: meta[k] for k in known if k in meta})
        except TypeError as e:
            raise RecordError(f"{where}: 字段缺失 —— {e}") from e

        rec.validate(where)
        return rec

    @classmethod
    def load(cls, path) -> "Record":
        p = Path(path)
        return cls.parse(p.read_text(encoding="utf-8"), where=str(p))

    # ---------- 写 ----------

    def to_markdown(self) -> str:
        meta: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "revision": self.revision,
            "kind": self.kind,
            "depth": self.depth,
            "status": self.status,
            "title": self.title,
            "origin": self.origin,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "epistemic": self.epistemic,
            "sharing": self.sharing,
            "applicability": self.applicability,
            "evidence": self.evidence,
            "related_ids": self.related_ids,
            "supersedes": self.supersedes,
            "review_after": self.review_after,
        }
        # 未知可选字段原样带回，不许丢
        for k, v in self.extra.items():
            meta.setdefault(k, v)
        header = yaml.safe_dump(
            meta, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100
        )
        return f"---\n{header}---\n\n{self.body}\n"

    def save(self, directory) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.id}.md"
        p.write_text(self.to_markdown(), encoding="utf-8")
        return p

    # ---------- hash ----------

    def content_hash(self) -> str:
        """规范对象 hash：排除 revision / updated_at / updated_by。

        两边内容真的相同 → 同一个 hash，跟谁改的、什么时候改的无关。
        """
        obj = {
            k: v
            for k, v in asdict(self).items()
            if k not in _MANAGED and k != "body"
        }
        obj["body"] = normalize_ws(self.body)
        blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ---------- 校验 ----------

    def validate(self, where: str = "<memory>") -> None:
        def bad(msg: str):
            raise RecordError(f"{where}: {msg}")

        if self.schema_version != SCHEMA_VERSION:
            bad(f"schema_version 应为 {SCHEMA_VERSION}，实际 {self.schema_version}")
        try:
            uuid.UUID(str(self.id))
        except (ValueError, AttributeError):
            bad(f"id 不是合法 uuid: {self.id!r}")
        if self.kind not in KINDS:
            bad(f"kind 非法: {self.kind!r}")
        if self.depth not in DEPTHS:
            bad(f"depth 非法: {self.depth!r}")
        if self.status not in STATUSES:
            bad(f"status 非法: {self.status!r}")
        if self.epistemic not in EPISTEMIC:
            bad(f"epistemic 非法: {self.epistemic!r}")
        if self.sharing not in SHARING:
            bad(f"sharing 必须是 approved（远端记录），实际 {self.sharing!r}")
        if not self.title or len(self.title) > 60:
            bad("title 必须非空且不超过 60 字")
        if len(normalize_ws(self.body)) < 10:
            bad("正文太短（至少 10 字符）")
        if self.depth == "core" and self.kind not in {"soul", "rule"}:
            bad("depth=core 只允许 soul / rule")
        if not isinstance(self.applicability, dict):
            bad("applicability 必须是映射")
        for key in ("frameworks", "os", "projects"):
            self.applicability.setdefault(key, [])
            if not isinstance(self.applicability[key], list):
                bad(f"applicability.{key} 必须是数组")
        if not self.evidence:
            bad("evidence 至少一条")
        for ev in self.evidence:
            if not isinstance(ev, dict) or "id" not in ev or "kind" not in ev:
                bad("evidence 每条要有 id 和 kind")
            if ev["kind"] not in EVIDENCE_KINDS:
                bad(f"evidence.kind 非法: {ev['kind']!r}")
        for ts_name in ("created_at", "updated_at"):
            ts = getattr(self, ts_name)
            if ts and not _has_tz(str(ts)):
                bad(f"{ts_name} 必须带时区: {ts!r}")
        if self.kind == "lesson":
            les = self.extra.get("lesson")
            if not isinstance(les, dict):
                bad("lesson 类必须有 lesson 段")
            block: dict = les
            for f in ("context", "action", "outcome", "adjustment"):
                if not block.get(f):
                    bad(f"lesson.{f} 不能为空")
        if self.kind == "topic":
            if not self.extra.get("goal"):
                bad("topic 类必须有 goal")
            if not self.extra.get("topic_state"):
                bad("topic 类必须有 topic_state")


# ---------- 工具 ----------

def normalize_ws(s: str) -> str:
    """只统一换行和行尾空白，不动语义。"""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in s.split("\n")).strip()


def _has_tz(ts: str) -> bool:
    return bool(re.search(r"(Z|[+-]\d{2}:?\d{2})$", ts.strip()))


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def find_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """supersedes / related 不许成环。返回找到的环（空 = 没问题）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in edges}
    for ns in edges.values():
        for n in ns:
            color.setdefault(n, WHITE)
    stack: list[str] = []
    cycles: list[list[str]] = []

    def dfs(n: str) -> bool:
        color[n] = GRAY
        stack.append(n)
        for m in edges.get(n, []):
            if color.get(m, WHITE) == GRAY:
                i = stack.index(m)
                cycles.append(stack[i:] + [m])
            elif color.get(m, WHITE) == WHITE:
                dfs(m)
        stack.pop()
        color[n] = BLACK
        return False

    for n in list(color):
        if color[n] == WHITE:
            dfs(n)
    return cycles


def validate_set(records: dict[str, Record]) -> list[str]:
    """整批校验：id 唯一、supersedes 指向存在、无环。返回问题列表。"""
    problems: list[str] = []
    edges: dict[str, list[str]] = {}
    for rid, rec in records.items():
        refs = list(rec.supersedes) + list(rec.related_ids)
        edges[rid] = [r for r in refs if r in records]
        missing = [r for r in refs if r not in records]
        for m in missing:
            problems.append(f"{rid[:8]} 引用了不存在的记录 {str(m)[:8]}")
    for cyc in find_cycles(edges):
        problems.append("记录引用成环: " + " → ".join(x[:8] for x in cyc))
    return problems
