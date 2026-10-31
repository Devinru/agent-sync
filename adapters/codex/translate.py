"""Translate durable registry sections into LOCAL CANDIDATES, never publish.

Only MEMORY.md task groups are read. Rollout routing metadata is omitted;
semantic task outcomes, scope, conditions, preferences and failure lessons stay.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
from datetime import datetime, timedelta

from adapter import Record, stable_id

_SECRET = re.compile(
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"']?[A-Za-z0-9_+/.=-]{16,})"
)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PATH = re.compile(r"(?<![A-Za-z0-9])(?:\\\\\?\\)?[A-Za-z]:[\\/][^`\n\"'，。；;）)<>|]+")
_KEPT_SECTIONS = {"User preferences", "Reusable knowledge", "Failures and how to do differently"}


def sanitize(text):
    """Remove host coordinates and example customer IDs, retain filename meaning."""
    if _SECRET.search(text):
        raise ValueError("发现疑似凭据 secret，停止转译，须人工审阅")
    if re.search(r"\\\\(?!\?\\)[^\\\s]", text):
        raise ValueError("发现 UNC 主机路径，停止转译，须人工审阅")
    def relative_path(match):
        value = match[0].strip().replace("\\", "/")
        parts = [p for p in value.split("/") if p]
        if ".agents" in parts:
            return "[project-root]/" + "/".join(parts[parts.index(".agents"):])
        # Known directory-only mentions in prose may have a following word.
        if len(parts) == 2 and " " in parts[-1]:
            return "[local-project]/" + parts[-1]
        return "[local-resource]/" + parts[-1]
    text = _PATH.sub(relative_path, text)
    text = re.sub(r"(?<![\d.])(?:127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?", "[local-endpoint]", text)
    text = re.sub(r"(?<![A-Za-z0-9])\d{11,18}(?![A-Za-z0-9])", "[redacted-sample-id]", text)
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|wxid[_-]|rollout_path=", text, re.I):
        raise ValueError("仍含本机路径或原始会话定位，停止转译")
    return text


def _project_alias(title):
    for name in ("gptribao", "CHAOQI", "HowToLiveBetter"):
        if name.casefold() in title.casefold():
            return name
    if "一户一说明" in title or "隐患台账" in title:
        return "一户一说明"
    if "竞赛" in title:
        return "集团AI竞赛"
    if "可视化试点" in title:
        return "非居民巡检路径可视化"
    return sanitize(title)


def translate_registry(text, existing=None, now=None):
    existing = existing or {}
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    moment = datetime.fromisoformat(now)
    if moment.tzinfo is None:
        raise ValueError("转译时间必须带时区")
    matches = list(re.finditer(r"(?m)^# Task Group: (.+)$", text))
    if not matches:
        raise ValueError("没有 Task Group，不能当作 durable registry 转译")
    records, review = [], []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.end():end]
        raw_title = match[1].strip()
        title = sanitize(raw_title)
        if len(title) > 60:
            raise ValueError("主题标题超过协议 60 字，须人工拆分")
        task_outcomes, status_notes, kept, scope, reuse = [], [], [], "", ""
        current_task = "原记录"
        keep = False
        for line in chunk.splitlines():
            if line.startswith("scope:"):
                scope = line.removeprefix("scope:").strip()
            elif line.startswith("applies_to:") and "reuse_rule=" in line:
                reuse = line.split("reuse_rule=", 1)[1].strip()
            elif line.startswith("## Task "):
                keep = False
                current_task = line.removeprefix("## ")
                task_outcomes.append(current_task)
            elif line.startswith("## "):
                section = line[3:].strip()
                keep = section in _KEPT_SECTIONS
                if keep:
                    kept.extend(["", line])
            elif line.startswith("### "):
                # keywords and rollout_summary_files are routing metadata.
                keep = False
            elif keep:
                kept.append(line)
            if "thread_id=" in line:
                # The parenthesized suffix also contains real QA/status caveats.
                # Omit coordinates, NOT the result qualifier after the stable ID.
                qualifier = re.search(r"thread_id=" + _UUID + r",\s*(.*?)\)\s*$", line)
                if qualifier:
                    status_notes.append(f"- {current_task}：{qualifier[1]}")
        if not kept:
            raise ValueError(f"{title}: 没有可审阅的语义章节")
        updates = sorted(set(re.findall(r"updated_at=([^,\s)]+)", chunk)))
        last_update = max(updates, key=datetime.fromisoformat) if updates else "unknown"
        body = (f"# {title}\n\n"
                f"这是一份从 Codex 本地长期摘要转译的历史项目记忆；原摘要最后更新：{last_update}。\n"
                "以下完成、未完成、数据和环境描述均属于原记录时间；再次使用时核对当前源文件、口径、软件版本与授权。\n\n"
                f"## 适用范围\n\n{scope}\n\n{reuse}\n\n"
                "## 原任务与当时状态\n\n" + "\n".join("- " + outcome for outcome in task_outcomes) +
                ("\n\n## 来源中明确记载的验证范围\n\n" + "\n".join(status_notes) if status_notes else "") +
                "\n" + "\n".join(kept).strip() + "\n")
        body = sanitize(body)
        rid = stable_id("codex-durable-project:" + raw_title)
        old = existing.get(rid)
        if old and (old.kind != "project" or old.origin != "codex-windows"):
            raise ValueError(f"{title}: 确定性 ID 与其他来源记录冲突")
        thread_ids = sorted(set(re.findall(r"thread_id=(" + _UUID + r")", chunk)))
        evidence = [{"id": "ev-codex-" + tid, "kind": "legacy", "origin": "codex-windows",
                     "locator": "local-ref:codex-memory/" + tid} for tid in thread_ids]
        if not evidence:
            evidence = [{"id": "ev-codex-" + rid, "kind": "legacy", "origin": "codex-windows",
                         "locator": "local-ref:codex-durable-section/" + rid}]
        source_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        extra = dict(old.extra) if old else {}
        extra["codex_source"] = {"kind": "durable-summary", "section_id": rid,
                                "source_sha256": source_hash, "last_summary_update": last_update,
                                "redactions": ["host-specific paths", "local endpoints", "sample customer identifiers"],
                                "omitted": ["rollout routing metadata", "keyword-only duplicates"]}
        rec = Record(id=rid, kind="project", depth="recent", status="active", title=title,
                     origin="codex-windows", updated_by="codex-windows", created_at=old.created_at if old else now,
                     updated_at=now, revision=old.revision if old else 1,
                     epistemic="legacy", sharing="local_candidate", body=body,
                     applicability={"frameworks": [], "os": [], "projects": [_project_alias(raw_title)]},
                     evidence=evidence, extra=extra,
                     review_after=old.review_after if old else (moment + timedelta(days=120)).isoformat(timespec="seconds"))
        approved_copy = dataclasses.replace(rec, sharing="approved")
        approved_copy.validate(title)
        action = "create" if old is None else "noop" if approved_copy.content_hash() == old.content_hash() else "update"
        if action == "update":
            rec.revision += 1
        records.append(rec)
        review.append({"id": rid, "title": title, "action": action,
                       "source_start_line": text.count("\n", 0, match.start()) + 1,
                       "source_end_line": text.count("\n", 0, end),
                       "source_sha256": source_hash, "last_summary_update": last_update,
                       "evidence_count": len(evidence), "content_hash_if_approved": approved_copy.content_hash()})
    return records, review
