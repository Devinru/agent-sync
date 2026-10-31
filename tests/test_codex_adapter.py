"""Codex adapter integration tests; all writes stay inside tmp_path."""
import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sync-core"))
from agent_sync.protocol import Record


def adapter_class():
    path = ROOT / "adapters" / "codex" / "adapter.py"
    assert path.exists(), "Codex adapter has not been implemented"
    spec = importlib.util.spec_from_file_location("codex_adapter_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Adapter


def record(kind="fact", body="测试的一条完整记忆，保留来源和实际适用条件。", **kwargs):
    data = dict(id=str(uuid.uuid4()), kind=kind, depth="core" if kind in {"soul", "rule"} else "stable",
                status="active", title="隔离测试记忆", origin="test", updated_by="test",
                created_at="2026-10-27T12:00:00+08:00", updated_at="2026-10-27T12:00:00+08:00",
                epistemic="inferred", evidence=[{"id": "ev-1", "kind": "file", "locator": "local-ref:test"}],
                body=body, extra={"future_field": {"keep": [1, "原文"]}})
    data.update(kwargs)
    return Record(**data)


@pytest.fixture
def env(tmp_path):
    cls = adapter_class()
    repo, home = tmp_path / "repo", tmp_path / "codex"
    (repo / "memory" / "records").mkdir(parents=True)
    (repo / "identity").mkdir()
    (repo / "protocol.yaml").write_text('protocol_version: "2.0"\n', encoding="utf-8")
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "MEMORY.md").write_text("# 本地\n- 原来的经验必须保留，不能让外端覆盖。\n", encoding="utf-8")
    (home / "AGENTS.md").write_text("旧人格", encoding="utf-8")
    return cls(repo, home)


def authorize(plan):
    return {"bootstrap": True, "confirmation_ref": "test:user-explicit-first-import", "plan_hash": plan["plan_hash"]}


def apply(env):
    plan = env.plan_import()
    return env.apply_import(plan, authorize(plan))


def test_roundtrip_and_ten_reimports_preserve_native(env):
    rec = record()
    rec.save(env.repo / "memory" / "records")
    before = (env.home / "memories" / "MEMORY.md").read_bytes()
    result = apply(env)
    assert env.verify_import(result)["ok"]
    assert env.snapshot_native()[rec.id].content_hash() == rec.content_hash()
    assert env.recall("完整记忆")[0]["id"] == rec.id
    for _ in range(10):
        plan = env.plan_import()
        assert not plan["writes"]
    assert len(list((env.store / "records").glob("*.json"))) == 1
    assert (env.home / "memories" / "MEMORY.md").read_bytes() == before


def test_protected_import_is_once_only_and_body_is_verbatim(env):
    soul, rule = record("soul", body="云端人格的完整正文，逐字保留，不得自动压缩。"), record("rule")
    (env.repo / "identity" / "soul.md").write_text(soul.to_markdown(), encoding="utf-8")
    (env.repo / "identity" / "rules.md").write_text(rule.to_markdown(), encoding="utf-8")
    plan = env.plan_import()
    with pytest.raises(ValueError, match="授权"):
        env.apply_import(plan, {"plan_hash": plan["plan_hash"]})
    result = env.apply_import(plan, authorize(plan))
    agents = (env.home / "AGENTS.md").read_text(encoding="utf-8")
    assert soul.body in agents and rule.body in agents
    assert (Path(result["backup"]) / "AGENTS.md").read_text(encoding="utf-8") == "旧人格"
    soul.body += "\n后续云端人格变更必须重新确认。"
    (env.repo / "identity" / "soul.md").write_text(soul.to_markdown(), encoding="utf-8")
    with pytest.raises(ValueError, match="首次|protected"):
        apply(env)


def test_local_edit_and_remote_edit_are_conflict(env):
    rec = record()
    path = rec.save(env.repo / "memory" / "records")
    apply(env)
    local = env.store / "records" / f"{rec.id}.json"
    d = json.loads(local.read_text(encoding="utf-8"))
    d["record"]["body"] += "本地还有独立的新内容。"
    local.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    rec.body += "远端也增加了一项新的条件。"
    path.write_text(rec.to_markdown(), encoding="utf-8")
    plan = env.plan_import()
    assert any(c["type"] == "both_changed" for c in plan["conflicts"])
    assert not plan["writes"]
    assert env.snapshot_native()[rec.id].body == d["record"]["body"]


def test_plan_is_bound_to_sources_and_targets(env):
    rec = record()
    path = rec.save(env.repo / "memory" / "records")
    plan = env.plan_import()
    rec.body += "计划后来源有变，旧审批不得沿用。"
    path.write_text(rec.to_markdown(), encoding="utf-8")
    with pytest.raises(ValueError, match="过期|变化"):
        env.apply_import(plan, authorize(plan))
    assert not (env.store / "records").exists()


def test_retirement_and_environment_are_not_general_recall(env):
    dormant = record(status="retracted")
    other = record(applicability={"frameworks": ["hermes"], "os": ["linux"], "projects": []})
    for r in [dormant, other]:
        r.save(env.repo / "memory" / "records")
    apply(env)
    assert not env.recall("完整记忆")
    assert len(env.recall("完整记忆", include_inactive=True)) == 2


def test_local_delete_does_not_delete_remote(env):
    rec = record()
    path = rec.save(env.repo / "memory" / "records")
    apply(env)
    (env.store / "records" / f"{rec.id}.json").unlink()
    plan = env.plan_import()
    assert any(c["type"] == "local_missing" for c in plan["conflicts"])
    assert path.exists()
    assert not env.propose_export()["deletes"]


def test_duplicate_candidates_block_until_reviewed(env):
    original = "原来的经验必须保留，不能让外端覆盖。"
    rec = record(body=original, title="本地")
    rec.save(env.repo / "memory" / "records")
    plan = env.plan_import()
    assert plan["duplicates"]
    with pytest.raises(ValueError, match="判重"):
        env.apply_import(plan, authorize(plan))
    decisions = {p["pair_key"]: {"decision": "linked", "reason": "同一条事实，原生来源保留；共享副本保留溯源和未知字段。"} for p in plan["duplicates"]}
    result = env.apply_import(plan, {**authorize(plan), "duplicate_decisions": decisions})
    assert env.verify_import(result)["ok"]


def test_export_is_draft_and_import_does_not_add_evidence(env):
    rec = record()
    rec.save(env.repo / "memory" / "records")
    apply(env)
    snapshot = env.snapshot_native()
    assert snapshot[rec.id].evidence == rec.evidence
    proposal = env.propose_export()
    assert rec.id not in [x["id"] for x in proposal["changes"]]
    assert proposal["status"] == "draft_needs_user_review"
    assert proposal["native_candidates"]
    assert all(c["sharing"] == "local_candidate" for c in proposal["native_candidates"])


def test_failed_write_rolls_back(env, monkeypatch):
    rec = record()
    rec.save(env.repo / "memory" / "records")
    original = env._write_atomic
    def fail_index(path, data):
        if path.name == "index.md":
            raise OSError("injected disk failure")
        return original(path, data)
    monkeypatch.setattr(env, "_write_atomic", fail_index)
    with pytest.raises(OSError, match="disk failure"):
        apply(env)
    assert not (env.store / "records" / f"{rec.id}.json").exists()
    assert not (env.store / "state.json").exists()


def test_summary_profile_is_in_duplicate_pool(env):
    body = "用户写材料要保留原始数字，表达朴素，不能凭空编造效率和成绩。"
    (env.home / "memories" / "memory_summary.md").write_text("## User preferences\n- " + body + "\n", encoding="utf-8")
    record(body=body, title="User preferences").save(env.repo / "memory" / "records")
    assert env.plan_import()["duplicates"]


def test_note_is_written_after_validation_and_never_deleted(env, monkeypatch):
    record().save(env.repo / "memory" / "records")
    note_writes = []
    original_write = env._write_atomic
    def track_write(path, data):
        if path.parent.name == "notes":
            note_writes.append(path)
        return original_write(path, data)
    monkeypatch.setattr(env, "_write_atomic", track_write)
    monkeypatch.setattr(env, "verify_import", lambda result: {"ok": False, "problems": ["test failure"]})
    with pytest.raises(ValueError, match="读回"):
        apply(env)
    assert not list((env.home / "memories" / "extensions" / "ad_hoc" / "notes").glob("*.md"))
    assert not note_writes


def test_duplicate_yaml_keys_are_rejected(env):
    rec = record()
    raw = rec.to_markdown().replace("schema_version: 2", "schema_version: 2\nschema_version: 2")
    (env.repo / "memory" / "records" / f"{rec.id}.md").write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        env.plan_import()


def test_cli_chinese_output_is_utf8_even_in_ascii_parent(env):
    record().save(env.repo / "memory" / "records")
    apply(env)
    child_env = dict(os.environ, PYTHONIOENCODING="ascii")
    result = subprocess.run([sys.executable, str(ROOT / "adapters" / "codex" / "cli.py"),
                             "--home", str(env.home), "--repo", str(env.repo), "recall", "测试"],
                            capture_output=True, encoding="utf-8", env=child_env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["title"] == "隔离测试记忆"


def test_protected_id_cannot_be_downgraded_to_bypass_once_guard(env):
    rec = record("soul")
    source = env.repo / "identity" / "soul.md"
    source.write_text(rec.to_markdown(), encoding="utf-8")
    apply(env)
    before = (env.store / "records" / f"{rec.id}.json").read_bytes()
    source.unlink()
    rec.kind, rec.depth = "fact", "stable"
    rec.save(env.repo / "memory" / "records")
    plan = env.plan_import()
    assert plan["writes"][0]["protected"]
    with pytest.raises(ValueError, match="protected"):
        env.apply_import(plan, authorize(plan))
    assert (env.store / "records" / f"{rec.id}.json").read_bytes() == before


def test_converged_local_and_remote_advance_baseline_before_next_pull(env):
    rec = record()
    remote = rec.save(env.repo / "memory" / "records")
    apply(env)
    path = env.store / "records" / f"{rec.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rec.body += "本地已经编辑并由用户另行批准发布这段内容。"
    data["record"] = dataclasses.asdict(rec)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    remote.write_text(rec.to_markdown(), encoding="utf-8")
    plan = env.plan_import()
    assert plan["baseline_updates"]
    env.apply_import(plan, authorize(plan))
    assert env.verify_import()["ok"]
    rec.body += "随后远端单独追加一个条件。"
    remote.write_text(rec.to_markdown(), encoding="utf-8")
    plan = env.plan_import()
    assert len(plan["writes"]) == 1 and not plan["conflicts"]


def test_selected_import_is_bound_and_does_not_install_unselected_persona(env):
    rec, soul = record(), record("soul", body="未选中的人格必须留在远端，不得顺便导入。")
    rec.save(env.repo / "memory" / "records")
    (env.repo / "identity" / "soul.md").write_text(soul.to_markdown(), encoding="utf-8")
    plan = env.plan_import(records=[rec])
    env.apply_import(plan, authorize(plan))
    assert list(env.snapshot_native()) == [rec.id]
    agents = (env.home / "AGENTS.md").read_text(encoding="utf-8")
    assert "旧人格" in agents and soul.body not in agents
