"""DSH 适配器：本机 dsh-longterm-memory vault ↔ 共享记录。

这一端和 Hermes 完全不同，别拿 Hermes 的文件当模板改：

  Hermes                              DSH
  ----------------------------------  --------------------------------------------
  records/*.json，一个 JSON 一条        Obsidian vault 里的 *.md，一条一个文件
  原生 id 就是 uuid4                    原生 id 是 `mem_<时间戳>_<hex>`，不是 uuid4
  正文字段 body                        body 在 `## 核心内容` 段里
  没有 kind/depth/epistemic            type 只有 8 个值，没有协议那几个字段
  json 任意加键                        frontmatter 只认固定键，**未知键靠
                                       extraFrontmatter 原样保留**

因此这里最要紧的两件事：

1. **协议 id 与物理 id 分离。** vault 的 `mem_…` 是物理 id（文件名、wikilink 用），
   协议要 uuid4。导入的记录把共享 uuid 写进 `ys_shared_id`；本地新建的记录用
   uuid5(NAMESPACE, mem_id) 派生一个稳定 uuid —— 稳定是必须的，否则每次 snapshot
   算出来的 id 都不一样，映射表和提案全对不上。cli.py 假定 `native_id == shared_id`，
   所以 `snapshot_native()` 一律以**协议 uuid 作键**。

2. **防回声靠存原文。** 字段来回翻译一定有损，hash 对不上就会被当成"本地改过"，
   然后推回远端 —— 自己制造回声。所以导入时把原始共享记录整份塞进
   `## 同步原文` 代码块，读回时以它为基准，只覆盖人真的手改过的字段。

写入策略：Markdown 是这个插件的唯一真源（它自己启动就对账 Markdown），
所以适配器直接写 vault 里的 .md，然后调宿主 RPC `syncIndex` 让索引跟账，
再用 RPC `get` 读回确认宿主真的收下了。插件的私有 `.dsh-memory/index.json`
**从不直改**。宿主没起来时退化为纯文件校验（guide 里写明）。
"""
from __future__ import annotations

import concurrent.futures
import http.client  # noqa: E402
import json
import os
import re
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 适配器和 sync-core 在同一仓库里，但不在同一个包下
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sync-core"))

from agent_sync.protocol import Record, now_iso, normalize_ws  # noqa: E402

ORIGIN = "dsh-windows"
CLIENT = ORIGIN

VAULT = Path(os.environ.get("AGENT_SYNC_VAULT") or r"D:\AgentSync\agent-memory")
RPC_HOST = os.environ.get("AGENT_SYNC_RPC_HOST") or "127.0.0.1"
RPC_PREFIX = "/dsh-longterm-memory/api"

# 桌面版的 GUI 端口每次启动都重新分配（实测 12557 → 12010），所以端口不能写死。
# 顺序：环境变量 → 上次探到的缓存 → 历史端口 → 扫一段区间。
RPC_PORT_ENV = os.environ.get("AGENT_SYNC_RPC_PORT") or ""
RPC_PORT_HINTS = (12557, 12010)
RPC_SCAN_RANGE = range(12000, 13100)
RPC_PORT_CACHE = VAULT / ".dsh-memory" / "agent-sync-rpc.json"
_port_cache: int | None = None

# 不算记忆笔记的目录：插件的私有区、Obsidian 自己的配置、派生索引
SKIP_DIRS = {".dsh-memory", ".obsidian", "Index", "History", ".trash", ".git"}

SEC_SUMMARY = "核心内容"
SEC_EVIDENCE = "来源与依据"
SEC_RELATED = "相关记忆"
SEC_ORIGINAL = "同步原文"          # 我们自己加的，存原始共享记录

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)

# ---------- 类型映射 ----------
# 协议 kind → 原生 type（vault 只认这 8 个）
TYPE_FROM_KIND = {
    "user": "profile",
    "fact": "knowledge",
    "episode": "experience",
    "lesson": "decision",       # 原生没有 lesson 类型，四段放 ys_extra 里原样带走
    "topic": "task",            # topic 有 goal/topic_state，语义上就是一条打开的 task
    "project": "project",
    "reflection": "experience",
    "soul": "knowledge",        # 实际不会走到：soul/rule 不自动导入
    "rule": "knowledge",
    "legacy_bundle": "knowledge",
}
# 原生 type → 协议 kind（只在没有 ys_kind 时用）
KIND_FROM_TYPE = {
    "profile": "user",
    "preference": "fact",
    "decision": "fact",
    "knowledge": "fact",
    "experience": "episode",
    "project": "project",
    "project-state": "fact",
    "task": "topic",
}

# 原生 status（active|pending|stale|superseded|conflict|archived）
STATUS_FROM_NATIVE = {
    "active": "active",
    "pending": "dormant",
    "stale": "dormant",
    "conflict": "active",
    "superseded": "superseded",
    "archived": "retracted",
}
NATIVE_FROM_STATUS = {
    "active": "active",
    "dormant": "stale",
    "superseded": "superseded",
    "retracted": "archived",
}

# 没有 ys_epistemic 时怎么定性质。
# 不给「有证据就是事实」这种升格：preference/profile/decision 是用户说的，
# 其余是这次会话里看到的，剩下的一律 inferred。
EPISTEMIC_FROM_TYPE = {
    "profile": "user_stated",
    "preference": "user_stated",
    "decision": "user_stated",
    "project": "observed",
    "project-state": "observed",
    "knowledge": "observed",
    "experience": "observed",
    "task": "observed",
}
CONFIDENCE_FROM_EPISTEMIC = {
    "user_stated": "high",
    "observed": "medium",
    "inferred": "low",
    "creative": "low",
    "legacy": "low",
}
IMPORTANCE_BY_TYPE = {
    "profile": 90, "preference": 80, "decision": 75, "project": 70,
    "project-state": 60, "knowledge": 55, "experience": 50, "task": 45,
}
DIR_BY_TYPE = {
    "profile": "Profile", "preference": "Preferences", "project": "Projects",
    "project-state": "Projects", "decision": "Decisions", "knowledge": "Knowledge",
    "experience": "Experiences", "task": "Tasks",
}

NS = uuid.UUID("6f3a1c2e-9b4d-4f7a-8c15-2d5e7f9a0b31")


# ---------- 小工具 ----------

def _post(method: str, payload: dict | None, port: int, timeout: float):
    """往指定端口发一次 RPC，成功返回 data。"""
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection(RPC_HOST, port, timeout=timeout)
    try:
        conn.request(
            "POST", f"{RPC_PREFIX}/{method}", body=body,
            headers={"Content-Type": "application/json", "Origin": "dsh-app://app"},
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
    finally:
        conn.close()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"RPC {method} 返回非 JSON（HTTP {resp.status}）：{raw[:200]}")
    if not doc.get("ok"):
        err = doc.get("error") or {}
        raise RuntimeError(f"RPC {method} 失败：{err.get('code')} {err.get('message')}")
    return doc.get("data")


def _same_vault(path) -> bool:
    if not path:
        return False
    a = os.path.normcase(os.path.abspath(str(path).rstrip("\\/")))
    b = os.path.normcase(os.path.abspath(str(VAULT).rstrip("\\/")))
    return a == b


def _probe(port: int, timeout: float = 0.35) -> bool:
    """这个端口上是不是「我们这个 vault」的宿主。"""
    try:
        data = _post("status", {}, port=port, timeout=timeout)
    except Exception:
        return False
    return _same_vault(((data or {}).get("vault") or {}).get("path"))


def _read_cached_port() -> int | None:
    try:
        doc = json.loads(RPC_PORT_CACHE.read_text(encoding="utf-8"))
        return int(doc["port"])
    except Exception:
        return None


def _write_cached_port(port: int) -> None:
    try:
        RPC_PORT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RPC_PORT_CACHE.write_text(
            json.dumps({"port": port, "found_at": now_iso()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _scan_ports() -> int | None:
    """并发扫一段本地端口，认「vault 路径对得上」的那个。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        futs = {pool.submit(_probe, p): p for p in RPC_SCAN_RANGE}
        try:
            for fut in concurrent.futures.as_completed(futs):
                if fut.result():
                    return futs[fut]
        finally:
            for fut in futs:
                fut.cancel()
    return None


def rpc_port(force: bool = False) -> int | None:
    """找到宿主 RPC 的真实端口；宿主没起来返回 None。"""
    global _port_cache
    if RPC_PORT_ENV:
        return int(RPC_PORT_ENV)
    if _port_cache and not force:
        return _port_cache
    for port in ([_read_cached_port()] if not force else []) + list(RPC_PORT_HINTS):
        if port and _probe(port):
            _port_cache = port
            _write_cached_port(port)
            return port
    found = _scan_ports()
    if found:
        _port_cache = found
        _write_cached_port(found)
    return found


def _rpc(method: str, payload: dict | None = None, timeout: float = 10.0):
    """调插件自己的 HTTP 接口。成功返回 data，失败抛异常。"""
    port = rpc_port()
    if port is None:
        raise RuntimeError("宿主 RPC 不可达（没找到 dsh-longterm-memory 在听哪个端口）")
    return _post(method, payload, port=port, timeout=timeout)


def rpc_alive() -> bool:
    try:
        return rpc_port() is not None
    except Exception:
        return False


def native_id_for(shared_id: str) -> str:
    """共享 id → 稳定的原生 id（形状要过插件的 isSafeMemoryId）。"""
    h = str(shared_id).replace("-", "")
    return f"mem_{h[:12]}_{h[12:24]}"


def shared_id_for(native_id: str) -> str:
    """本地原生 id → 稳定协议 uuid（uuid5，稳定才谈得上幂等）。"""
    return str(uuid.uuid5(NS, native_id))


def slugify(title: str, fallback: str = "memory") -> str:
    """跟插件 memoryFileName() 的 slug 规则一致，否则它认不出这个文件。"""
    stem = unicodedata.normalize("NFC", title or "")
    stem = re.sub(r"[\x00-\x1f\x7f]", "", stem)
    stem = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    stem = re.sub(r"^[.\s]+|[.\s]+$", "", stem)
    stem = re.sub(r"-{2,}", "-", stem)
    stem = re.sub(r"^[-.\s]+|[-.\s]+$", "", stem).strip()
    if len(stem) > 80:
        stem = stem[:80].strip()
    if not stem:
        stem = fallback
    if stem.lower() in {"con", "prn", "aux", "nul", "readme", "index", "memory home"}:
        stem = f"{stem}-note"
    return stem


def memory_filename(title: str, native_id: str) -> str:
    """插件只认 `<slug>-mem_xxx.md` 这种名字。"""
    return f"{slugify(title)}-{native_id}.md"


# ---------- 读 vault ----------

def _split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """把正文拆成 (前言, [(标题, 内容)])。围栏里的 `##` 不算标题 —— 同 parseBody。"""
    lines = body.replace("\r\n", "\n").split("\n")
    preamble: list[str] = []
    heads: list[tuple[str, list[str]]] = []
    fence: str | None = None
    for line in lines:
        stripped = line.strip()
        if fence is not None:
            if re.fullmatch(r"(`{3,}|~{3,})", stripped) and stripped[0] == fence:
                fence = None
            (heads[-1][1] if heads else preamble).append(line)
            continue
        m_fence = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if m_fence:
            fence = m_fence.group(1)[0]
            (heads[-1][1] if heads else preamble).append(line)
            continue
        m_h2 = re.match(r"^##[ \t]+(.+?)[ \t]*$", line)
        if m_h2:
            heads.append((m_h2.group(1).strip(), []))
            continue
        if re.match(r"^#[ \t]+(.+?)[ \t]*$", line):
            continue  # H1 是标题，不参与正文
        (heads[-1][1] if heads else preamble).append(line)
    return ("\n".join(preamble).strip(),
            [(h, "\n".join(ls).strip()) for h, ls in heads])


def _section(sections: list[tuple[str, str]], name: str) -> str | None:
    for h, b in sections:
        if h == name or h.startswith(name):
            return b
    return None


def _parse_note(path: Path) -> tuple[dict, str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    header, body = m.group(1), m.group(2)
    try:
        import yaml
        meta = yaml.safe_load(header)
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    mid = meta.get("id")
    if not isinstance(mid, str) or not mid.startswith("mem_"):
        return None
    if "type" not in meta or "status" not in meta:
        return None
    return meta, body


def iter_notes():
    """遍历 vault 里所有记忆笔记：产出 (路径, frontmatter, 正文)。"""
    if not VAULT.exists():
        return
    for p in sorted(VAULT.rglob("*.md")):
        rel = p.relative_to(VAULT)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        parsed = _parse_note(p)
        if parsed is None:
            continue
        yield p, parsed[0], parsed[1]


def _original_md(body: str) -> str | None:
    """把 `## 同步原文` 里的原始共享记录取出来。"""
    _pre, sections = _split_sections(body)
    block = _section(sections, SEC_ORIGINAL)
    if not block:
        return None
    m = re.search(r"```[a-zA-Z]*\n(.*?)\n```", block, re.S)
    if not m:
        return None
    text = m.group(1).strip()
    return text if text.startswith("---") else None


def _evidence_from_text(text: str, native_id: str) -> list[dict]:
    """原生 evidence 是个字符串，没有结构。不编造 kind：一律 session + local-ref。"""
    items = []
    for i, line in enumerate([l.strip(" -•\t") for l in (text or "").splitlines()]):
        if not line:
            continue
        items.append({
            "id": f"ev-{native_id[-8:]}-{i}",
            "kind": "session",
            "origin": ORIGIN,
            "locator": line if line.startswith("local-ref:") else f"local-ref:{line}",
        })
    return items


def _norm(text: str) -> str:
    """行尾空白、多余空行统一掉 —— 插件存盘会重排这些，不能算成人手改。"""
    out: list[str] = []
    blank = False
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        line = line.rstrip()
        if not line.strip():
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def _h1(body: str) -> str | None:
    for line in body.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^#[ \t]+(.+?)[ \t]*$", line)
        if m:
            return m.group(1).strip()
    return None


def _content_parts(body: str) -> list[str]:
    """笔记正文里属于「内容」的部分：前言 + 核心内容 + 其余自建小节。

    插件自己管的三个小节（核心内容/来源与依据/相关记忆）和 `## 同步原文` 之外的
    小节都按原顺序拼回来 —— 正文里本来就带 `## 适用范围` 这种标题时，插件存盘会把它
    拆成小节，拼回去必须还是原来那段文字。
    """
    pre, sections = _split_sections(body)
    parts: list[str] = []
    if pre.strip():
        parts.append(pre.strip())
    summary = _section(sections, SEC_SUMMARY)
    if summary:
        parts.append(summary.strip())
    for head, block in sections:
        if head in (SEC_SUMMARY, SEC_EVIDENCE, SEC_RELATED, SEC_ORIGINAL):
            continue
        parts.append(f"## {head}\n\n{block}".rstrip() if block else f"## {head}")
    return parts


def _signature(title: str, body: str) -> str:
    return _norm("\n\n".join([title] + _content_parts(body)))


def _build_record(meta: dict, body: str, path: Path) -> Record | None:
    """一条笔记 → 共享 Record。导入过和本地新建的走同一条路径。"""
    native_id = str(meta["id"])
    original = _original_md(body)
    _pre, sections = _split_sections(body)

    if original:
        try:
            base = Record.parse(original, where=str(path))
        except Exception:
            base = None
        if base is not None:
            # 原文是基准。只有**人手真改了内容**才覆盖它，而且只覆盖改过的部分。
            # 判"改没改"：把笔记现有的内容部分拼回去，跟原文渲染出来的比 ——
            # 一致就原样返回 base（连 updated_by 都不动，否则 hash 会假性变化）。
            again = _signature(_h1(body) or base.title, body)
            wanted = _signature(base.title, base.body)
            if again == wanted:
                try:
                    base.validate(str(path))
                    return base
                except Exception:
                    return None
            base.title = (_h1(body) or base.title)[:60]
            base.body = "\n\n".join(_content_parts(body)) or base.body
            want_status = STATUS_FROM_NATIVE.get(meta.get("status", "active"), "active")
            if want_status != base.status:
                base.status = want_status
            base.updated_by = CLIENT
            if meta.get("updated_at"):
                base.updated_at = str(meta["updated_at"])
            try:
                base.validate(str(path))
                return base
            except Exception:
                return None

    # ---- 本地新建的：从 ys_* 和原生字段拼一条 ----
    kind = meta.get("ys_kind") or KIND_FROM_TYPE.get(meta.get("type", ""), "fact")
    if kind not in {"soul", "rule", "user", "episode", "fact", "lesson",
                    "topic", "project", "reflection", "legacy_bundle"}:
        kind = "fact"
    summary = _section(sections, SEC_SUMMARY)
    if summary is None:
        summary = _pre
    evidence = meta.get("ys_evidence") or _evidence_from_text(meta.get("evidence", ""), native_id)
    extra = meta.get("ys_extra") or {}
    if kind == "topic":
        extra.setdefault("goal", meta.get("ys_goal") or meta.get("title", ""))
        extra.setdefault("topic_state", STATUS_FROM_NATIVE.get(meta.get("status", "active"), "active"))
    try:
        rec = Record(
            id=str(meta.get("ys_shared_id") or shared_id_for(native_id)),
            kind=kind,
            depth=meta.get("ys_depth") or ("core" if kind in ("soul", "rule") else "recent"),
            status=meta.get("ys_pstatus") or STATUS_FROM_NATIVE.get(meta.get("status", "active"), "active"),
            title=(meta.get("title") or native_id)[:60],
            origin=meta.get("ys_origin") or ORIGIN,
            updated_by=meta.get("ys_updated_by") or CLIENT,
            created_at=str(meta.get("created_at") or now_iso()),
            updated_at=str(meta.get("updated_at") or now_iso()),
            epistemic=meta.get("ys_epistemic") or EPISTEMIC_FROM_TYPE.get(meta.get("type", ""), "inferred"),
            applicability=meta.get("ys_applicability") or {"frameworks": [], "os": [], "projects": []},
            revision=int(meta.get("ys_revision") or 1),
            evidence=evidence,
            related_ids=meta.get("ys_related_ids") or [],
            supersedes=[str(x) for x in (meta.get("ys_supersedes") or [])],
            review_after=meta.get("ys_review_after"),
            body=summary or "",
            extra=extra if isinstance(extra, dict) else {},
        )
        rec.validate(str(path))
        return rec
    except Exception:
        return None


# ---------- 接口 1：inspect ----------

def inspect(config: dict | None = None) -> dict:
    notes = list(iter_notes()) if VAULT.exists() else []
    alive = rpc_alive()
    port = rpc_port() if alive else None
    return {
        "framework": "dsh-longterm-memory (DeepSeek Harness desktop)",
        "client": CLIENT,
        "vault": str(VAULT),
        "vault_exists": VAULT.exists(),
        "native_count": len(notes),
        "host_rpc": {
            "endpoint": f"http://{RPC_HOST}:{port}{RPC_PREFIX}" if port else None,
            "port": port,
            "port_source": "env" if RPC_PORT_ENV else ("discovered" if port else None),
            "alive": alive,
        },
        "status": "verified" if (VAULT.exists() and notes) else "blocked",
        "blocked_reason": None if (VAULT.exists() and notes)
        else ("vault 不存在" if not VAULT.exists() else "vault 里没有记忆笔记"),
        "notes": [
            {"file": str(p.relative_to(VAULT)), "id": m.get("id"),
             "type": m.get("type"), "status": m.get("status"),
             "has_shared": bool(_original_md(b))}
            for p, m, b in notes
        ],
    }


# ---------- 接口 2：snapshot_native ----------

def snapshot_native(cursor: str | None = None) -> dict[str, Record]:
    """vault 里的全部记忆 → {协议 uuid: Record}。

    键是**协议 uuid**，不是文件名里的 mem_ 号 —— cli.py 的映射表按这个对。
    """
    out: dict[str, Record] = {}
    for p, meta, body in iter_notes():
        try:
            rec = _build_record(meta, body, p)
        except Exception:
            rec = None
        if rec is None:
            continue
        out[rec.id] = rec
    return out


# ---------- 接口 3：propose_export / 4：plan_import ----------

def plan_import(shared: dict[str, Record], state: dict) -> dict:
    """算要导进 vault 的记录。只算，不写。

    和 Hermes 那份同构，但这里没有"本地 id == 共享 id"的巧合，
    所以映射表读的是协议 uuid。
    """
    mappings = state.get("mappings", {})
    native = snapshot_native()
    native_to_shared = {v.get("native_id"): k for k, v in mappings.items()}

    new, updated, conflict, skip, retire = [], [], [], [], []

    for sid, rec in sorted(shared.items()):
        # 人格核心不自动写，只能提案人确认 —— 协议的红线，这里照办
        if rec.kind in ("soul", "rule"):
            skip.append((sid, "soul/rule 不自动导入，只能提案"))
            continue

        if rec.status in ("superseded", "retracted"):
            nid_r = mappings.get(sid, {}).get("native_id")
            if nid_r and nid_r in native:
                local_r = native[nid_r]
                if local_r.content_hash() == mappings[sid].get("native_hash"):
                    retire.append((sid, rec, local_r))
                else:
                    conflict.append((sid, rec, local_r))
            elif nid_r:
                skip.append((sid, "远端已退役，本地也没有了"))
            else:
                skip.append((sid, "远端已退役，从未导入过"))
            continue

        nid = mappings.get(sid, {}).get("native_id") or (sid if sid in native else None)
        local = native.get(nid) if nid else None

        if nid is None:
            new.append((sid, rec, None))
            continue
        if local is None:
            skip.append((sid, "本地已删除，不自动恢复"))
            continue
        if local.content_hash() == rec.content_hash():
            skip.append((sid, "和本地一致"))
            continue
        base = mappings.get(sid, {}).get("native_hash")
        if local.content_hash() == base:
            updated.append((sid, rec, local))
        else:
            conflict.append((sid, rec, local))

    return {"new": new, "updated": updated, "conflict": conflict,
            "skip": skip, "retire": retire}


def export_candidates(shared: dict[str, Record], state: dict) -> dict:
    """哪些本地记录该推上去。"""
    mappings = state.get("mappings", {})
    native_to_shared = {v.get("native_id"): k for k, v in mappings.items()}
    new, changed, skipped = [], [], []
    for nid, rec in snapshot_native().items():
        sid = native_to_shared.get(nid)
        if sid is None:
            new.append((nid, rec))
            continue
        remote = shared.get(sid)
        if remote is None:
            skipped.append((nid, "映射指向的共享记录不存在了"))
            continue
        if rec.content_hash() != remote.content_hash():
            changed.append((nid, rec, remote))
        else:
            skipped.append((nid, "和远端一致"))
    return {"new": new, "changed": changed, "skipped": skipped}


propose_export = export_candidates


# ---------- 渲染 ----------

def to_native(rec: Record, native_id: str | None = None) -> tuple[str, str]:
    """共享 Record → 笔记文件名 + 全文。"""
    nid = native_id or native_id_for(rec.id)
    mtype = TYPE_FROM_KIND.get(rec.kind, "knowledge")
    ev_lines = []
    for e in rec.evidence:
        loc = e.get("locator") or e.get("id") or ""
        src = e.get("origin") or ""
        ev_lines.append(f"- `{e.get('kind', 'session')}` {loc}" + (f"（{src}）" if src else ""))

    related = "\n".join(f"- [[{r}]]" for r in rec.related_ids) if rec.related_ids else ""

    meta = {
        "id": nid,
        "type": mtype,
        "status": NATIVE_FROM_STATUS.get(rec.status, "active"),
        "title": rec.title,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "tags": [mtype] + (["agent-sync"] if "agent-sync" not in rec.body else []),
        "confidence": CONFIDENCE_FROM_EPISTEMIC.get(rec.epistemic, "medium"),
        "importance": IMPORTANCE_BY_TYPE.get(mtype, 50),
        "pinned": False,
        "evidence": "\n".join(ev_lines),
        "schema_version": 1,
        # ---- 协议专属字段：插件不认这些键，但会原样保留（extraFrontmatter）----
        "ys_shared_id": rec.id,
        "ys_native_id": nid,
        "ys_kind": rec.kind,
        "ys_depth": rec.depth,
        "ys_pstatus": rec.status,
        "ys_epistemic": rec.epistemic,
        "ys_origin": rec.origin,
        "ys_revision": rec.revision,
        "ys_applicability": rec.applicability,
        "ys_evidence": rec.evidence,
        "ys_related_ids": rec.related_ids,
        "ys_supersedes": rec.supersedes,
        "ys_updated_by": rec.updated_by,
    }
    if rec.review_after:
        meta["ys_review_after"] = rec.review_after
    if rec.extra:
        meta["ys_extra"] = rec.extra

    import yaml
    header = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False,
                            default_flow_style=False, width=10000)
    body_parts = [
        f"# {rec.title}",
        "",
        f"## {SEC_SUMMARY}",
        rec.body.strip(),
        "",
        f"## {SEC_EVIDENCE}",
        "\n".join(ev_lines) if ev_lines else "（无）",
        "",
        f"## {SEC_RELATED}",
        related if related else "（无）",
        "",
        f"## {SEC_ORIGINAL}",
        "",
        "下面是导入时的原始共享记录，**别手改这一段**：读回时以它为基准，",
        "手改了它，本地和远端就会被判成不一致。改上面几段。",
        "",
        "```yaml",
        rec.to_markdown().rstrip(),
        "```",
        "",
    ]
    return memory_filename(rec.title, nid), f"---\n{header}---\n\n" + "\n".join(body_parts)


def _write_note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _note_path_for(sid: str, native: dict[str, Record]) -> Path | None:
    """找这条共享记录在 vault 里的文件：先看 ys_shared_id，再看派生的文件名。"""
    for p, meta, body in iter_notes():
        if str(meta.get("ys_shared_id") or "") == sid:
            return p
    want = native_id_for(sid)
    for p, meta, _body in iter_notes():
        if str(meta.get("id")) == want:
            return p
    # 本地新建、还没导入过的：它的 uuid5 可能正好等于这条 sid
    for p, meta, _body in iter_notes():
        nid = str(meta.get("id") or "")
        if nid.startswith("mem_") and "ys_shared_id" not in meta and shared_id_for(nid) == sid:
            return p
    return None


# ---------- 接口 5：apply_import ----------

def apply_import(plan: dict, authorization: dict | None = None) -> dict:
    """真的写进 vault。写前备份，写后读回校验，幂等。"""
    VAULT.mkdir(parents=True, exist_ok=True)
    backup = VAULT / ".dsh-memory" / "agent-sync-backup"
    written, failed, retired, broken = [], [], [], []
    touched: list[str] = []

    _index = snapshot_native()

    # 退役先做：保留 id，只改状态和图
    for sid, rec, _local in plan.get("retire", []):
        try:
            path = _note_path_for(sid, _index)
            if path is None:
                failed.append((sid, "找不到本地笔记，无法跟随退役"))
                continue
            backup.mkdir(parents=True, exist_ok=True)
            (backup / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            meta, body = _parse_note(path)
            nid = str(meta["id"])
            fname, text = to_native(rec, native_id=nid)
            target = path.parent / fname
            _write_note(target, text)
            if target != path:
                path.unlink(missing_ok=True)
            retired.append((sid, rec))
            touched.append(nid)
        except Exception as e:
            failed.append((sid, f"退役失败: {e}"))

    todo = list(plan.get("new", [])) + list(plan.get("updated", []))
    for sid, rec, local_at_plan_time in todo:
        try:
            path = _note_path_for(sid, _index)
            if local_at_plan_time is not None and path is not None:
                cur = _parse_note(path)
                if cur is not None:
                    cur_rec = _build_record(cur[0], cur[1], path)
                    if cur_rec is not None and cur_rec.content_hash() != local_at_plan_time.content_hash():
                        failed.append((sid, "计划之后本地被改过，跳过不覆盖"))
                        continue
            nid = str(_parse_note(path)[0]["id"]) if path is not None else native_id_for(sid)
            fname, text = to_native(rec, native_id=nid)
            if path is not None:
                backup.mkdir(parents=True, exist_ok=True)
                (backup / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                target = path.parent / fname
            else:
                target = VAULT / DIR_BY_TYPE.get(TYPE_FROM_KIND.get(rec.kind, "knowledge"), "Knowledge") / fname
            _write_note(target, text)
            if path is not None and target != path:
                path.unlink(missing_ok=True)
            written.append((sid, rec))
            touched.append(nid)
        except Exception as e:
            failed.append((sid, str(e)))

    # 让宿主的索引跟账（Markdown 是它的真源，索引是派生物）
    index_ok = True
    index_note = ""
    if touched:
        try:
            _rpc("syncIndex", {"full": False}, timeout=30.0)
        except Exception as e:
            index_ok = False
            index_note = str(e)

    # 读回校验：文件能解析回来，而且 hash 和远端一致
    verified = []
    for sid, rec in written:
        try:
            path = _note_path_for(sid, {})
            if path is None:
                broken.append((sid, "写完找不到文件"))
                continue
            meta, body = _parse_note(path)
            back = _build_record(meta, body, path)
            if back is None:
                broken.append((sid, "读回解析失败"))
            elif back.content_hash() != rec.content_hash():
                broken.append((sid, f"读回 hash 不符 {back.content_hash()[:10]}≠{rec.content_hash()[:10]}"))
            else:
                verified.append(sid)
        except Exception as e:
            broken.append((sid, f"读回失败: {e}"))

    return {"written": written, "failed": failed, "verified": verified,
            "broken": broken, "retired": retired,
            "index": {"ok": index_ok, "note": index_note}}


# ---------- 索引重建钩子 ----------

def reindex_local() -> bool:
    """写进 Markdown 之后让宿主的索引跟账。

    cli.py 的 reindex_local() 是 shell 调 `python3 <脚本> reindex`，
    本机 `python3` 是 WindowsApps 的空壳 —— 所以 run.py 直接把这个函数
    挂到 cli.reindex_local 上，不经 shell。
    """
    try:
        _rpc("syncIndex", {}, timeout=60.0)
        return True
    except Exception as e:  # 宿主没起来：索引留到下次启动对账
        print(f"  ! 宿主索引同步失败：{e}", file=sys.stderr)
        return False


# ---------- 接口 6：verify_import ----------

def verify_import(result: dict, recall: bool = True) -> dict:
    """导入之后能不能真被用上：宿主认不认这条记忆、检索得不得。

    文件写进去不等于能被回忆出来 —— 这条要单独验。
    """
    out = {"verified": [], "broken": [], "recall": {}, "host": rpc_alive()}
    ids = [sid for sid, _rec in result.get("written", [])]
    for sid in ids:
        path = _note_path_for(sid, {})
        if path is None:
            out["broken"].append((sid, "找不到文件"))
            continue
        meta, body = _parse_note(path)
        rec = _build_record(meta, body, path) if meta else None
        if rec is None or rec.id != sid:
            out["broken"].append((sid, "读回不一致"))
        else:
            out["verified"].append(sid)
    if recall and out["host"]:
        for sid in out["verified"]:
            path = _note_path_for(sid, {})
            meta, _body = _parse_note(path)
            nid = str(meta["id"])
            try:
                got = _rpc("get", {"id": nid}, timeout=10.0)
                mem = (got or {}).get("memory") or {}
                out["recall"][sid] = {"native_id": nid, "host_ok": mem.get("id") == nid,
                                      "title": mem.get("title")}
            except Exception as e:
                out["recall"][sid] = {"native_id": nid, "host_ok": False, "error": str(e)}
    return out
