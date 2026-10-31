"""提案：本地变更 → 待确认的发布申请。

一次提案 = 一个 JSON 文件 `changes/<proposal_id>.json`。
确认时绑定的是一个 **业务差异的 hash**，不是 commit、不是时间戳。

改了提案里任何一条内容（哪怕标题多一个字），旧的回执就失效——得重新给你看。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .protocol import Record, new_id, now_iso

# 提案里的动作（和 compare.Action 的 PUSH / NEW_PUSH 对应）
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_RETIRE = "retire"
ACTION_RESTORE = "restore"


@dataclass
class Change:
    shared_id: str
    action: str                 # create / update / retire / restore
    kind: str
    title: str
    content_hash: str
    reason: str
    source: dict = field(default_factory=dict)   # {native_id, locator}
    body_preview: str = ""
    old_hash: str | None = None
    # 冻结的发布内容（完整记录 markdown）。
    # 有它就按它写，不再回头去 native 找 —— merge 这类操作根本没有 native 副本。
    frozen_md: str = ""
    # 退役时写什么状态。merge 产生的是 superseded，撤回才是 retracted。
    # 不在发布器里靠猜。
    target_status: str = ""

    def validate(self, where: str) -> None:
        if self.action not in {ACTION_CREATE, ACTION_UPDATE, ACTION_RETIRE, ACTION_RESTORE}:
            raise ValueError(f"{where}: action 非法 {self.action!r}")
        if not self.shared_id:
            raise ValueError(f"{where}: 缺 shared_id")
        if not self.kind:
            raise ValueError(f"{where}: 缺 kind")
        if self.action != ACTION_RETIRE and not self.content_hash:
            raise ValueError(f"{where}: 非退役变更必须有 content_hash")
        if self.action != ACTION_RETIRE and not (self.frozen_md or self.source.get("native_id")):
            raise ValueError(f"{where}: 非退役变更必须能拿到内容（frozen_md 或 native_id）")
        if self.action == ACTION_RETIRE and self.target_status not in ("", "superseded", "retracted"):
            raise ValueError(f"{where}: target_status 非法 {self.target_status!r}")


@dataclass
class Proposal:
    proposal_id: str
    created_at: str
    client: str
    base_commit: str
    target_ref: str
    changes: list[Change] = field(default_factory=list)
    groups: list[list[str]] = field(default_factory=list)  # 依赖组：一起改一起提交
    approval: dict = field(default_factory=lambda: {
        "status": "pending", "approved_at": None,
        "approved_by": None, "approval_hash": None,
    })
    notes: str = ""

    # ---------- hash ----------

    def business_hash(self) -> str:
        """业务差异的 hash。审批绑的是这个。

        排除：created_at / approval / notes / base_commit（时间和管理信息不算业务差异）
        包含：target_ref + 每条变更的 id/action/kind/title/content_hash
        """
        payload = {
            "target_ref": self.target_ref,
            "changes": [
                {
                    "shared_id": c.shared_id,
                    "action": c.action,
                    "kind": c.kind,
                    "title": c.title,
                    "content_hash": c.content_hash,
                    "old_hash": c.old_hash,
                }
                for c in sorted(self.changes, key=lambda x: (x.shared_id, x.action))
            ],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ---------- 状态 ----------

    def approve(self, by: str) -> str:
        h = self.business_hash()
        self.approval = {
            "status": "approved",
            "approved_at": now_iso(),
            "approved_by": by,
            "approval_hash": h,
        }
        return h

    def check_approval(self) -> tuple[bool, str]:
        a = self.approval
        if a.get("status") != "approved":
            return False, f"提案状态是 {a.get('status')}，还没确认"
        cur = self.business_hash()
        if a.get("approval_hash") != cur:
            return False, "提案内容在确认之后变了，回执失效，需要重新看一遍"
        return True, "ok"

    def is_stale(self, current_commit: str) -> bool:
        return self.base_commit != current_commit

    # ---------- 序列化 ----------

    def to_json(self) -> str:
        d = asdict(self)
        d["proposal_id"] = self.proposal_id
        d["business_hash"] = self.business_hash()
        return json.dumps(d, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Proposal":
        d = json.loads(text)
        d.pop("business_hash", None)
        ch = [Change(**c) for c in d.pop("changes", [])]
        p = cls(changes=ch, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        for c in p.changes:
            c.validate(c.shared_id[:8])
        return p

    def save(self, root: Path) -> Path:
        d = root / "changes"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.proposal_id}.json"
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path) -> "Proposal":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    # ---------- 人眼可读 ----------

    def render_review(self) -> str:
        s = {"create": 0, "update": 0, "retire": 0, "restore": 0}
        for c in self.changes:
            s[c.action] = s.get(c.action, 0) + 1
        lines = [
            f"# 同步提案 {self.proposal_id[:8]}",
            "",
            f"- 来源端：{self.client}",
            f"- 目标分支：{self.target_ref}",
            f"- 生成于：{self.created_at}",
            f"- 基于提交：`{self.base_commit[:8]}`",
            f"- 内容指纹：`{self.business_hash()[:16]}`",
            "",
            "## 变更汇总",
            "",
            "  ".join(f"{k}={v}" for k, v in s.items() if v),
            "",
            "## 逐条",
            "",
        ]
        LABEL = {"create": "新增", "update": "修改", "retire": "退役", "restore": "恢复"}
        for i, c in enumerate(self.changes, 1):
            lines.append(f"### {i}. [{LABEL[c.action]}] {c.title}")
            lines.append("")
            lines.append(f"- 记录：`{c.shared_id[:8]}` · {c.kind}")
            lines.append(f"- 原因：{c.reason}")
            if c.old_hash and c.old_hash != c.content_hash:
                lines.append(f"- 内容：`{c.old_hash[:10]}` → `{c.content_hash[:10]}`")
            else:
                lines.append(f"- 内容：`{c.content_hash[:10]}`")
            if c.body_preview:
                lines += ["", "预览：", "", "> " + "\n> ".join(c.body_preview.splitlines()[:6]), ""]
            else:
                lines.append("")
        if self.groups:
            lines += ["## 依赖组", "", "下面这些记录要一起提交，不能分开：", ""]
            for g in self.groups:
                lines.append("- " + " + ".join(f"`{x[:8]}`" for x in g))
            lines.append("")
        lines += [
            "## 确认方式",
            "",
            f"```bash",
            f"python3 -m agent_sync.cli publish {self.proposal_id}",
            f"```",
            "",
            "**确认前请逐条看。** 确认之后内容再变，回执就失效。",
        ]
        return "\n".join(lines) + "\n"
