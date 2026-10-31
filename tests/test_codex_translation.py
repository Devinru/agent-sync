"""Native durable-memory translation, without publishing or touching originals."""
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters" / "codex"))
from adapter import strict_record


def translator():
    path = ROOT / "adapters" / "codex" / "translate.py"
    assert path.exists(), "native translation has not been implemented"
    spec = importlib.util.spec_from_file_location("codex_translation_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLE = r'''# Task Group: gptribao 明细核验

scope: Historical completed task, not current inventory.
applies_to: cwd=C:\Users\Private Name\Desktop\gptribao; reuse_rule=Check real headers first.

## Task 1: 原始任务已经完成，视觉核验未完成
### rollout_summary_files
- rollout_summaries/hidden.md (cwd=C:\Users\Private Name\Desktop\gptribao, rollout_path=C:\private\raw-session.jsonl, updated_at=2026-10-22T05:00:00+00:00, thread_id=01a16239-7842-79f2-a5ba-90ba57ec7948)
### keywords
- 用气用户编码, 11111111111, 22222222222

## User preferences
- 部分完成也计入施工量；保留原文件。 [Task 1]
## Reusable knowledge
- 只修改 `C:\Users\Private Name\Desktop\gptribao\ribao_rewrite.py` 的项目副本。
- 1 -> 0 才是本次定义的减量；22 条样例不代表全部业务。 [Task 1]
## Failures and how to do differently
- 症状是空键伪匹配；先排除空键再连接。
'''


def test_grouped_translation_preserves_conditions_and_provenance():
    mod = translator()
    records, review = mod.translate_registry(SAMPLE, now="2026-10-27T16:00:00+08:00")
    assert len(records) == 1
    rec = records[0]
    assert rec.sharing == "local_candidate" and rec.epistemic == "legacy"
    assert rec.kind == "project" and rec.depth == "recent"
    assert "视觉核验未完成" in rec.body and "空键" in rec.body and "1 -> 0" in rec.body
    assert "Private Name" not in rec.to_markdown() and "raw-session" not in rec.to_markdown()
    assert "11111111111" not in rec.to_markdown()
    assert "ribao_rewrite.py" in rec.body
    assert len(rec.evidence) == 1 and rec.evidence[0]["kind"] == "legacy"
    approved = dataclasses.replace(rec, sharing="approved")
    assert strict_record(approved.to_markdown(), "test").content_hash() == approved.content_hash()
    assert review[0]["source_start_line"] == 1


def test_same_source_does_not_create_evidence_by_repetition():
    mod = translator()
    text = SAMPLE.replace("### keywords", "- copy (thread_id=01a16239-7842-79f2-a5ba-90ba57ec7948)\n### keywords")
    records, _ = mod.translate_registry(text, now="2026-10-27T16:00:00+08:00")
    assert len(records[0].evidence) == 1


def test_rerun_retains_id_creation_time_and_semantic_hash():
    mod = translator()
    records, _ = mod.translate_registry(SAMPLE, now="2026-10-27T16:00:00+08:00")
    old = dataclasses.replace(records[0], sharing="approved")
    newer, review = mod.translate_registry(SAMPLE, existing={old.id: old}, now="2026-10-28T16:00:00+08:00")
    current = dataclasses.replace(newer[0], sharing="approved")
    assert current.id == old.id and current.created_at == old.created_at
    assert current.content_hash() == old.content_hash()
    assert review[0]["action"] == "noop"


def test_edited_source_updates_same_id_without_faking_new_evidence():
    mod = translator()
    records, _ = mod.translate_registry(SAMPLE, now="2026-10-27T16:00:00+08:00")
    old = dataclasses.replace(records[0], sharing="approved")
    newer, review = mod.translate_registry(SAMPLE.replace("22 条", "23 条"), existing={old.id: old}, now="2026-10-28T16:00:00+08:00")
    current = dataclasses.replace(newer[0], sharing="approved")
    assert current.id == old.id and current.revision == old.revision + 1
    assert current.content_hash() != old.content_hash()
    assert current.evidence == old.evidence and review[0]["action"] == "update"


def test_secret_in_semantic_body_stops_translation():
    mod = translator()
    secret = "sk-proj-" + "abcXYZ1234" * 4
    with pytest.raises(ValueError, match="凭据|secret"):
        mod.translate_registry(SAMPLE + "\n- api_key=" + secret, now="2026-10-27T16:00:00+08:00")


def test_absolute_paths_and_proxy_and_sample_identifiers_are_redacted():
    mod = translator()
    raw = r'文件 `E:\Life\.agents\skills\life-decision-guide`，代理 127.0.0.1:7897，样例 11111111111。'
    clean = mod.sanitize(raw)
    assert "E:" not in clean and "127.0.0.1" not in clean and "11111111111" not in clean
    assert "life-decision-guide" in clean


def test_non_registry_file_fails_instead_of_silently_publishing_empty():
    mod = translator()
    with pytest.raises(ValueError, match="Task Group"):
        mod.translate_registry("随便一段笔记，不是已审阅的 registry 格式。", now="2026-10-27T16:00:00+08:00")


def test_codex_static_export_instructions_route_to_adapter():
    spec = importlib.util.spec_from_file_location("static_clients", ROOT / "sync-clients.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    note = module.render_install_note("Codex", "codex")
    assert "guides/codex.md" in note and "adapters/codex/cli.py" in note
    assert "把整个 `memory/` 拷进去" not in note


def test_status_qualifiers_in_routing_metadata_are_not_lost():
    mod = translator()
    text = SAMPLE.replace("thread_id=01a16239-7842-79f2-a5ba-90ba57ec7948)",
                          "thread_id=01a16239-7842-79f2-a5ba-90ba57ec7948, visual layout QA incomplete; actual end-to-end flow remains to be verified)")
    records, _ = mod.translate_registry(text, now="2026-10-27T16:00:00+08:00")
    assert "visual layout QA incomplete" in records[0].body
    assert "actual end-to-end flow remains to be verified" in records[0].body
    assert "raw-session" not in records[0].body


def test_source_url_survives_path_redaction_unchanged():
    mod = translator()
    url = "https://example.com/news/2026/article"
    assert mod.sanitize("原始来源 " + url) == "原始来源 " + url


def test_unc_path_requires_review_instead_of_leaking_host():
    mod = translator()
    with pytest.raises(ValueError, match="UNC|路径"):
        mod.sanitize(r"输入 \\fileserver\private\customers.xlsx")
