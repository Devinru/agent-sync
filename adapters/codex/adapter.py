"""Codex file-backed recall bridge. Never edits generated memory or its DB.

The six adapter operations intentionally do not call git, an LLM, or a publisher.
The supported ad_hoc note is a small pointer; full shared records live separately.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sync-core"))
from agent_sync.protocol import Record, validate_set
from agent_sync.dedupe import find_candidates

VERSION = "0.1.0"
PROTECTED = {"soul", "rule"}


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return digest(path.read_bytes()) if path.is_file() else None


def stable_id(text):
    # Stable local candidate IDs, with UUID4 variant/version bits required by v2.
    return str(uuid.UUID(bytes=hashlib.sha256(text.encode()).digest()[:16], version=4))


def strict_record(text, where):
    # Use the shared Record implementation; enforce documented YAML restrictions
    # at the boundary without changing the shared protocol implementation.
    header = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not header:
        raise ValueError(f"{where}: 缺少 front matter")
    for token in yaml.scan(header[1]):
        if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken, yaml.tokens.TagToken)):
            raise ValueError(f"{where}: 禁止 YAML anchor / alias / tag")
    node = yaml.compose(header[1], Loader=yaml.SafeLoader)
    def check_keys(n):
        if isinstance(n, yaml.MappingNode):
            seen = set()
            for key, value in n.value:
                if not isinstance(key, yaml.ScalarNode) or key.value in seen:
                    raise ValueError(f"{where}: 重复或非标量 YAML key")
                seen.add(key.value)
                check_keys(value)
        elif isinstance(n, yaml.SequenceNode):
            for item in n.value:
                check_keys(item)
    check_keys(node)
    rec = Record.parse(text, where)
    if uuid.UUID(rec.id).version != 4:
        raise ValueError(f"{where}: id 必须为 uuid4")
    return rec


class Adapter:
    def __init__(self, repo=ROOT, home=None):
        self.repo = Path(repo).resolve()
        self.home = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
        self.store = self.home / "agent-sync"

    def _state(self):
        path = self.store / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"mappings": {}}

    def _sources(self):
        paths = [self.repo / "protocol.yaml"]
        paths += sorted((self.repo / "identity").glob("*.md"))
        paths += sorted((self.repo / "memory" / "records").glob("*.md"))
        return paths

    def shared(self):
        protocol = yaml.safe_load((self.repo / "protocol.yaml").read_text(encoding="utf-8"))
        if str(protocol["protocol_version"]).split(".")[0] != "2":
            raise ValueError("未知协议主版本，停止写入")
        records = {}
        raw = {}
        for path in self._sources()[1:]:
            text = path.read_text(encoding="utf-8")
            rec = strict_record(text, str(path))
            if rec.id in records:
                raise ValueError(f"重复共享 ID: {rec.id}")
            identity = path.parent.name == "identity"
            if identity != (rec.kind in PROTECTED):
                raise ValueError(f"{path}: protected 记录只能放在 identity")
            if not identity and path.stem != rec.id:
                raise ValueError(f"{path}: 文件名必须与 ID 一致")
            records[rec.id], raw[rec.id] = rec, text
        problems = validate_set(records)
        if problems:
            raise ValueError("; ".join(problems))
        return records, raw

    def inspect(self, config=None):
        import tomllib
        config_file = self.home / "config.toml"
        cfg = tomllib.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
        return {"framework": "codex", "adapter_version": VERSION, "profile": str(self.home),
                "native_memory": str(self.home / "memories"), "local_recall_store": str(self.store),
                "memories_feature": cfg.get("features", {}).get("memories"),
                "use_memories": cfg.get("memories", {}).get("use_memories"),
                "generate_memories": cfg.get("memories", {}).get("generate_memories"),
                "disable_on_external_context": cfg.get("memories", {}).get("disable_on_external_context"),
                "status": "partial", "native_index_external_import": "unsupported",
                "native_note_entry": "memories/extensions/ad_hoc/notes/<timestamp>-<slug>.md",
                "recall": "local full-text scan via CLI; global AGENTS.md supplies discovery",
                "capacity": "disk-backed; global instructions share Codex project_doc_max_bytes budget",
                "bootstrap_done": self._state().get("bootstrap_done", False),
                "global_override_exists": (self.home / "AGENTS.override.md").exists()}

    def snapshot_native(self, cursor=None):
        records = {}
        for path in sorted((self.store / "records").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            original = strict_record(data["_shared_md"], str(path))
            # Every field is represented in editable JSON. Do not let the preserved
            # original hide real changes to status/evidence/applicability/unknown fields.
            current = Record(**data["record"])
            current.validate(str(path))
            if current.id != original.id or path.stem != current.id:
                raise ValueError(f"本地 ID 不一致: {path}")
            records[current.id] = current
        return records

    def native_candidates(self):
        """Read only generated durable entries, never transcripts/auth/SQLite."""
        return self._scan_native_file("MEMORY.md") + self._scan_native_file("memory_summary.md")

    def _scan_native_file(self, filename):
        path = self.home / "memories" / filename
        if not path.exists():
            return []
        section, start, block = "Codex 本地记忆", 0, []
        result = []
        def flush():
            body = "\n".join(block).strip()
            if len(body) < 10:
                return
            locator = f"local-ref:codex-memory/{filename}/{start}"
            result.append({"id": stable_id(locator + ":" + body), "native_id": locator,
                           "title": section[:60], "kind": "fact", "depth": "recent", "status": "active",
                           "body": body, "epistemic": "legacy", "sharing": "local_candidate",
                           "evidence": [{"id": stable_id(locator), "kind": "legacy", "origin": "codex-windows", "locator": locator}],
                           "requires_review": ["classification", "privacy", "deduplication", "sharing"]})
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("#"):
                flush()
                block = []
                section = line.lstrip("# ")
            elif line.startswith("- "):
                flush()
                start, block = line_no, [line[2:]]
            elif block and line.strip():
                block.append(line)
        flush()
        return result

    @staticmethod
    def _candidate_record(item):
        return Record(id=item["id"], kind=item["kind"], depth="recent", status="active", title=item["title"],
                      body=item["body"], origin="codex-windows", updated_by="codex-windows",
                      created_at="1970-01-01T00:00:00+00:00", updated_at="1970-01-01T00:00:00+00:00",
                      epistemic="legacy", sharing="local_candidate", evidence=item["evidence"])

    def propose_export(self, native=None, shared=None, mapping=None):
        native = self.snapshot_native() if native is None else native
        shared = self.shared()[0] if shared is None else shared
        mapping = self._state()["mappings"] if mapping is None else mapping
        changes = []
        for rid, rec in native.items():
            baseline = mapping.get(rid)
            if baseline and rec.content_hash() == baseline["local_hash"]:
                continue
            changes.append({"id": rid, "action": "update" if rid in shared else "create",
                            "protected": rec.kind in PROTECTED,
                            "content_hash": rec.content_hash(), "proposed_record": dataclasses.asdict(rec),
                            "requires_confirmation": True})
        candidates = self.native_candidates()
        incoming = {c["id"]: self._candidate_record(c) for c in candidates}
        pairs = find_candidates(incoming, shared, limit=max(1, len(incoming) * len(shared)))
        return {"status": "draft_needs_user_review", "changes": changes, "native_candidates": candidates,
                "possible_duplicates": [p.as_dict() for p in pairs], "deletes": [],
                "note": "本地候选未经分类和分享审批，不是可直接 publish 的共享记录；没有上传行为。"}

    def plan_import(self, records=None, native=None, mapping=None):
        shared, raw = self.shared()
        selected = None
        if records is not None:
            selected = {r.id if isinstance(r, Record) else r["id"] for r in records}
            if selected - shared.keys():
                raise ValueError("选中的共享 ID 不存在")
            shared = {k: v for k, v in shared.items() if k in selected}
        native = self.snapshot_native() if native is None else native
        state = self._state()
        mapping = state["mappings"] if mapping is None else mapping
        writes, conflicts, new, baseline_updates = [], [], {}, []
        for rid, rec in shared.items():
            local, base = native.get(rid), mapping.get(rid)
            source_hash = rec.content_hash()
            protected = rec.kind in PROTECTED or bool(local and local.kind in PROTECTED) or bool(base and base.get("protected"))
            if local and local.content_hash() == source_hash:
                if not base:
                    conflicts.append({"id": rid, "type": "unmapped_local", "title": rec.title})
                elif base["local_hash"] != source_hash or base["shared_hash"] != source_hash:
                    baseline_updates.append({"id": rid, "kind": rec.kind, "title": rec.title,
                                             "shared_hash": source_hash, "protected": protected})
                continue
            if base and not local:
                conflicts.append({"id": rid, "type": "local_missing", "title": rec.title})
                continue
            if local and (not base or local.content_hash() != base["local_hash"]):
                conflicts.append({"id": rid, "type": "both_changed" if not base or source_hash != base["shared_hash"] else "local_changed", "title": rec.title})
                continue
            path = self.store / "records" / f"{rid}.json"
            data = encoded({"format": "codex-agent-v1", "record": dataclasses.asdict(rec), "_shared_md": raw[rid]})
            writes.append({"id": rid, "title": rec.title, "kind": rec.kind, "path": str(path),
                           "content": data.decode("utf-8"), "before": file_hash(path),
                           "shared_hash": source_hash, "protected": protected})
            if local is None and rec.kind not in PROTECTED:
                new[rid] = rec
        if selected is None:
            for rid in mapping.keys() - shared.keys():
                conflicts.append({"id": rid, "type": "remote_missing", "title": "远端文件缺失，不能推断为退役"})
        pool = dict(native)
        for item in self.native_candidates():
            pool[item["id"]] = self._candidate_record(item)
        pairs = find_candidates(new, pool, limit=max(1, len(new) * len(pool)))
        duplicates = []
        for pair in pairs:
            item = pair.as_dict()
            item["pair_key"] = ":".join(sorted([pair.a_id, pair.b_id]))
            item["incoming_title"] = new[pair.a_id].title
            item["local_title"] = pool[pair.b_id].title
            item["incoming_body"] = new[pair.a_id].body
            item["local_body"] = pool[pair.b_id].body
            duplicates.append(item)
        dependencies = self._sources() + [self.home / "AGENTS.md", self.home / "AGENTS.override.md",
                                          self.home / "memories" / "MEMORY.md", self.store / "state.json",
                                          self.home / "memories" / "memory_summary.md",
                                          Path(__file__), Path(__file__).with_name("cli.py"),
                                          ROOT / "sync-core" / "agent_sync" / "protocol.py",
                                          ROOT / "sync-core" / "agent_sync" / "dedupe.py"]
        dependencies += sorted((self.store / "records").glob("*.json"))
        plan = {"adapter_version": VERSION, "repo": str(self.repo), "home": str(self.home),
                "bootstrap_done": state.get("bootstrap_done", False), "writes": writes,
                "selected_ids": sorted(selected) if selected is not None else None,
                "baseline_updates": baseline_updates,
                "conflicts": conflicts, "duplicates": duplicates,
                "dependencies": {str(p): file_hash(p) for p in dependencies}}
        plan["plan_hash"] = digest(encoded(plan))
        return plan

    def _bridge(self):
        cli = Path(__file__).with_name("cli.py")
        return ("## 本机共享记忆接入\n\n"
                f"共享记忆的本机长期层在 `{self.store}`，与 Codex 自动记忆并存。\n"
                f"涉及过往经历、长期偏好、项目上下文时，先读 `{self.store / 'index.md'}`，再读相关 JSON 的 record 正文。\n"
                f"全文检索：`python \"{cli}\" recall \"关键词\"`；默认只返回 active 且适用当前 Codex/Windows 的记录。\n"
                "检索结果保留 epistemic、来源、日期、状态和 applicability；不要把 inferred/creative/legacy 当已验证现实事实。\n"
                "人格正文中的现实经历按虚拟人格叙事理解；实际工具能力、隐私边界和更高层指令仍须遵守。\n"
                "本次首次同步已经明确授权；此后人格/规则变化先报告，记忆导入先出计划，任何上传另行确认。\n"
                "不要把本地自动记忆、原始会话或个人路径整包上传。\n")

    def _index(self, records):
        rows = ["# 本机共享记忆索引", "", "完整正文在 records/<id>.json 的 record 字段；_shared_md 是往返溯源副本。",
                "Codex 自动记忆仍在 ../memories，本目录只维护外部共享记录。", ""]
        for rec in sorted(records.values(), key=lambda r: (r.kind, r.title)):
            rows.append(f"- [{rec.kind}/{rec.status}/{rec.epistemic}] {rec.title} | {rec.id} | applicability={json.dumps(rec.applicability, ensure_ascii=False)}")
        return ("\n".join(rows) + "\n").encode("utf-8")

    @staticmethod
    def _write_atomic(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def apply_import(self, plan, authorization):
        selected = plan.get("selected_ids")
        fresh = self.plan_import(records=[{"id": rid} for rid in selected] if selected is not None else None)
        if fresh["plan_hash"] != plan.get("plan_hash") or plan != fresh:
            raise ValueError("计划过期或内容发生变化，请重新规划")
        if authorization.get("plan_hash") != plan["plan_hash"] or not authorization.get("confirmation_ref"):
            raise ValueError("缺少绑定计划的明确授权")
        state = self._state()
        first = not state.get("bootstrap_done")
        if first and not authorization.get("bootstrap"):
            raise ValueError("首次导入需要显式授权 bootstrap")
        if plan["conflicts"]:
            raise ValueError("存在冲突，停止导入")
        operations = plan["writes"] + plan["baseline_updates"]
        if any(w["protected"] for w in operations) and not first:
            raise ValueError("首次授权已用完；protected 变化需要独立人工处理")
        if first and (self.home / "AGENTS.override.md").exists():
            raise ValueError("AGENTS.override.md 会覆盖全局人格，请先人工处理")
        decisions = authorization.get("duplicate_decisions", {})
        for candidate in plan["duplicates"]:
            decision = decisions.get(candidate["pair_key"], {})
            if decision.get("decision") not in {"separate", "linked"} or not decision.get("reason"):
                raise ValueError("判重候选尚未逐对判断并记录理由")
        if not operations and not first:
            return {"ok": True, "written": 0, "ids": list(state["mappings"]), "backup": None}
        shared = self.shared()[0]
        writes = {Path(w["path"]): w["content"].encode("utf-8") for w in plan["writes"]}
        for w in operations:
            state["mappings"][w["id"]] = {"native_id": w["id"], "shared_hash": w["shared_hash"], "local_hash": w["shared_hash"],
                                          "protected": w["protected"], "kind": w["kind"]}
        if first:
            # Preserve the parsed cloud bodies verbatim, no model rewrite/dedupe.
            identity = [shared[w["id"]] for w in plan["writes"] if shared[w["id"]].kind in PROTECTED and shared[w["id"]].status == "active"]
            if identity:
                if any(r.kind == "soul" for r in identity):
                    agent_text = "\n\n".join(r.body for r in sorted(identity, key=lambda r: 0 if r.kind == "soul" else 1))
                else:
                    agent_text = (self.home / "AGENTS.md").read_text(encoding="utf-8") if (self.home / "AGENTS.md").exists() else ""
                    agent_text += "\n\n" + "\n\n".join(r.body for r in identity)
            else:
                agent_text = (self.home / "AGENTS.md").read_text(encoding="utf-8") if (self.home / "AGENTS.md").exists() else ""
            # Put discovery first, so the shared instruction size budget can't hide it.
            writes[self.home / "AGENTS.md"] = (self._bridge() + "\n---\n\n" + agent_text + "\n").encode("utf-8")
            if len(writes[self.home / "AGENTS.md"]) > 32768:
                raise ValueError("全局人格超过默认 32 KiB 指令预算，需另行设计分层")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            note = self.home / "memories" / "extensions" / "ad_hoc" / "notes" / f"{stamp}-agent-sync-local-bridge.md"
            if note.exists():
                raise ValueError("补充 note 路径已存在")
            writes[note] = ("# 共享记忆首次接入\n\n"
                           "用户于 2026-10-27 明确要求本机对接私有 agent-sync 仓库；仅首次增量导入共享记忆，人格采用云端版本。\n"
                           f"完整本地记录与状态在 `{self.store}`，索引在 `{self.store / 'index.md'}`。\n"
                           "原有 Codex 自动记忆保留。此说明是资料定位信息，不是新的事实证据。\n"
                           "后续人格/规则变更与私人资料上传需要重新确认。宿主后台是否已合并此 note 尚待宿主处理，不能仅凭文件存在声称完成。\n").encode("utf-8")
            state["note_path"] = str(note)
            state["bootstrap_done"] = True
            state["bootstrap_confirmation_ref"] = authorization["confirmation_ref"]
        all_local = self.snapshot_native()
        for w in plan["writes"]:
            all_local[w["id"]] = shared[w["id"]]
        writes[self.store / "index.md"] = self._index(all_local)
        state["duplicate_decisions"] = {**state.get("duplicate_decisions", {}), **decisions}
        state["last_plan_hash"] = plan["plan_hash"]
        state["adapter_version"] = VERSION
        writes[self.store / "state.json"] = encoded(state)
        # Host notes are append-only and must never be removed. Write the one
        # pointer only AFTER all rollback-capable files have passed readback QA.
        note_path = Path(state["note_path"]) if first else None
        note_data = writes.pop(note_path) if first else None
        if first:
            writes[note_path] = note_data
        before = {path: path.read_bytes() if path.exists() else None for path in writes}
        backup = self.store / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True, exist_ok=False)
        journal = []
        for index, (path, data) in enumerate(before.items()):
            name = "AGENTS.md" if path == self.home / "AGENTS.md" else f"{index:04d}.before"
            if data is not None:
                (backup / name).write_bytes(data)
            journal.append({"path": str(path), "backup": name if data is not None else None, "after_hash": digest(writes[path])})
        (backup / "journal.json").write_bytes(encoded(journal))
        (backup / "authorization.json").write_bytes(encoded(authorization))
        completed = []
        try:
            for path, data in writes.items():
                if path == note_path:
                    continue
                self._write_atomic(path, data)
                completed.append(path)
            result = {"ok": True, "written": len(plan["writes"]), "ids": list(state["mappings"]), "backup": str(backup)}
            verified = self.verify_import(result)
            if not verified["ok"]:
                raise ValueError(f"导入读回验证失败: {verified['problems']}")
            if note_path:
                self._write_atomic(note_path, note_data)
            return result
        except BaseException:
            for path in reversed(completed):
                # Restore only bytes written by this transaction; preserve concurrent edits.
                if file_hash(path) != digest(writes[path]):
                    continue
                if before[path] is None:
                    path.unlink()
                else:
                    Adapter._write_atomic(path, before[path])
            raise

    @staticmethod
    def applicable(rec):
        app = rec.applicability
        return (not app.get("frameworks") or "codex" in [str(x).lower() for x in app["frameworks"]]) and (not app.get("os") or "windows" in [str(x).lower() for x in app["os"]]) and not app.get("projects")

    def recall(self, query, include_inactive=False):
        query = query.casefold()
        found = []
        for rec in self.snapshot_native().values():
            if not include_inactive and (rec.status != "active" or rec.kind in PROTECTED or not self.applicable(rec)):
                continue
            if query not in (rec.title + "\n" + rec.body).casefold():
                continue
            found.append({"id": rec.id, "title": rec.title, "kind": rec.kind, "status": rec.status,
                          "epistemic": rec.epistemic, "created_at": rec.created_at, "origin": rec.origin,
                          "applicability": rec.applicability, "body": rec.body,
                          "path": str(self.store / "records" / f"{rec.id}.json")})
        return found

    def verify_import(self, result=None):
        state = self._state()
        native = self.snapshot_native()
        problems = []
        checked = 0
        for rid, baseline in state["mappings"].items():
            rec = native.get(rid)
            if rec is None:
                problems.append(f"{rid}: 本地记录缺失")
                continue
            if rec.content_hash() != baseline["local_hash"]:
                problems.append(f"{rid}: 本地内容与导入基线不同")
            if Record.parse(rec.to_markdown()).content_hash() != rec.content_hash():
                problems.append(f"{rid}: 往返 hash 不一致")
            if rec.kind not in PROTECTED and rec.status == "active" and self.applicable(rec):
                if rid not in {r["id"] for r in self.recall(rec.title)}:
                    problems.append(f"{rid}: 本地检索失败")
                checked += 1
        index = self.store / "index.md"
        if state.get("bootstrap_done") and (not index.exists() or index.read_bytes() != self._index(native)):
            problems.append("本地索引过期或缺失")
        return {"ok": not problems, "records": len(native), "recall_checked": checked,
                "problems": problems, "host_native_index_import": "unsupported",
                "ad_hoc_consolidation": "host_managed_not_asserted"}
