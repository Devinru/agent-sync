"""命令级测试 RR01–RR06。

每个场景都真的建临时仓库、临时 bare remote、假原生目录，然后跑 CLI，
断言命令的退出码和磁盘结果。不查源码字符串。

跑：python3 tests/test_commands.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f"   [{detail}]" if detail and not cond else ""))


class Env:
    """一个隔离的测试环境：临时工作仓库 + 裸 remote + 假原生目录。"""

    def __init__(self, name):
        self.name = name
        self.tmp = Path(tempfile.mkdtemp(prefix=f"agent_sync_{name}_"))
        self.work = self.tmp / "repo"
        self.remote = self.tmp / "remote.git"
        self.native = self.tmp / "native"
        self.state = self.tmp / "state.json"
        self.script = self.tmp / "fake_index.py"
        self.native.mkdir(parents=True)
        self._setup()

    def _setup(self):
        # 裸 remote
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        # 工作仓库：只复制必要文件
        shutil.copytree(ROOT, self.work,
                        ignore=shutil.ignore_patterns(".git", "changes", "dedupe-log",
                                                      "clients", "__pycache__", "*.pyc"))
        g = lambda *a: subprocess.run(["git", *a], cwd=self.work, capture_output=True, text=True, check=True)
        g("init", "-q")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        g("add", "-A")
        g("commit", "-q", "-m", "init")
        g("remote", "add", "origin", str(self.remote))
        g("push", "-q", "-u", "origin", "master")
        # 假索引脚本（成功）
        self._fake_index(ok=True)

    def _fake_index(self, ok=True):
        code = "import sys; print('✅ 重建索引完成，0 条' if 0 else '✅ 重建索引完成'); sys.exit(0)" if ok \
            else "import sys; print('boom', file=sys.stderr); sys.exit(1)"
        self.script.write_text(f"#!{PY}\n{code}\n", encoding="utf-8")
        self.script.chmod(0o755)

    def fail_index(self):
        self._fake_index(ok=False)

    def env(self, **over):
        e = dict(os.environ)
        e.update({
            "AGENT_SYNC_REPO": str(self.work),
            "AGENT_SYNC_STATE": str(self.state),
            "AGENT_SYNC_HERMES_RECORDS": str(self.native),
            "AGENT_SYNC_RECORDS_SCRIPT": str(self.script),
            "PYTHONPATH": str(self.work / "sync-core"),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        })
        e.update(over)
        return e

    def run(self, *args, **over):
        r = subprocess.run([PY, "-m", "agent_sync.cli", *args],
                           cwd=self.work / "sync-core", capture_output=True,
                           text=True, env=self.env(**over))
        return r

    def native_rec(self, rid, status="active", body="这是测试记录的内容，长度足够通过校验。",
                   epistemic=None, title="测试记录"):
        d = {
            "id": rid, "revision": 1, "kind": "fact", "status": status,
            "title": title, "body": body,
            "evidence": [{"ref": f"session:test/{rid[:6]}", "at": "2026-10-27T10:00:00+08:00"}],
            "created_at": "2026-10-27T10:00:00+08:00",
            "updated_at": "2026-10-27T10:00:00+08:00",
            "review_after": None,
        }
        if epistemic:
            d["epistemic"] = epistemic
        (self.native / f"{rid}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return d

    def shared_rec(self, rid, status="active", body="这是共享记录的内容，长度也足够。", title="共享记录"):
        """直接往共享仓库写一条记录（模拟另一个端推上来的）。"""
        sys.path.insert(0, str(self.work / "sync-core"))
        from agent_sync.protocol import Record
        r = Record(
            id=rid, kind="fact", depth="recent", status=status, title=title,
            origin="other-machine", updated_by="other-machine",
            created_at="2026-10-27T10:00:00+08:00",
            updated_at="2026-10-27T10:00:00+08:00",
            epistemic="observed",
            evidence=[{"id": "ev-s", "kind": "session", "locator": "local-ref:other"}],
            body=body,
        )
        p = self.work / "memory" / "records" / f"{rid}.md"
        p.write_text(r.to_markdown(), encoding="utf-8")
        return r

    def git_push_shared(self):
        g = lambda *a: subprocess.run(["git", *a], cwd=self.work, capture_output=True, text=True)
        g("add", "-A")
        g("commit", "-q", "-m", "shared change")
        g("push", "-q", "origin", "master")

    def remote_head(self):
        r = subprocess.run(["git", "rev-parse", "master"], cwd=self.remote,
                           capture_output=True, text=True)
        return r.stdout.strip()

    def read_native(self, rid):
        p = self.native / f"{rid}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------- RR01 只有远端退役 ----------

def rr01():
    print("\nRR01 只有远端退役，无新增/更新")
    e = Env("rr01")
    try:
        rid = str(uuid.uuid4())
        e.native_rec(rid)
        e.shared_rec(rid, body="这是共享记录的内容，长度也足够。")
        e.git_push_shared()
        # 先导一次，建立映射
        r1 = e.run("pull", "--apply")
        check("首次导入成功", r1.returncode == 0, f"exit={r1.returncode} {r1.stderr[:120]}")
        check("本地拿到记录", e.read_native(rid) is not None)

        # 远端只把这条改成 retracted
        e.shared_rec(rid, status="retracted")
        e.git_push_shared()

        r2 = e.run("pull", "--apply")
        out = r2.stdout + r2.stderr
        check("退役场景不提前返回「没有要导入的」",
              "没有要导入的" not in out, out[:150])
        local = e.read_native(rid)
        check("本地状态变成 retracted",
              local is not None and local.get("status") == "retracted",
              str(local.get("status") if local else None))
    finally:
        e.cleanup()


# ---------- RR02 合并后经确认发布 ----------

def rr02():
    print("\nRR02 合并后经确认发布（第二个 clone 验证）")
    e = Env("rr02")
    try:
        keep_id, drop_id = str(uuid.uuid4()), str(uuid.uuid4())
        e.shared_rec(keep_id, body="保留的那条记录正文内容，长度足够。", title="保留条")
        e.shared_rec(drop_id, body="被合并的那条，带一句独有的细节：他喜欢小音量听人声。", title="被合并条")
        e.git_push_shared()

        r = e.run("merge", keep_id, drop_id, "--note", "测试合并")
        out = r.stdout + r.stderr
        check("merge 生成提案（不直推）", r.returncode == 0 and "review" in out, out[:200])

        # 找到提案
        props = sorted((e.work / "changes").glob("*.json"))
        props = [p for p in props if not p.name.endswith(".done.json")]
        check("提案文件存在", len(props) == 1, f"{len(props)} 个")
        if not props:
            return
        pid = props[0].stem

        # 没确认就发布 → 必须拒绝
        r_no = e.run("publish", pid)
        check("未确认 publish 被拒绝", r_no.returncode == 3, f"exit={r_no.returncode}")

        head_before = e.remote_head()
        r_ap = e.run("approve", pid)
        check("approve 成功", r_ap.returncode == 0, r_ap.stderr[:120])
        r_pub = e.run("publish", pid)
        out2 = r_pub.stdout + r_pub.stderr
        check("publish 成功", r_pub.returncode == 0, f"exit={r_pub.returncode} {out2[:200]}")
        check("远端 commit 变了", e.remote_head() != head_before, e.remote_head()[:8])

        # 第二个 clone 读结果
        c2 = e.tmp / "clone2"
        subprocess.run(["git", "clone", "-q", str(e.remote), str(c2)], check=True)
        keep_md = (c2 / "memory" / "records" / f"{keep_id}.md").read_text(encoding="utf-8")
        drop_md = (c2 / "memory" / "records" / f"{drop_id}.md").read_text(encoding="utf-8")
        check("keep 包含被合并方的独有内容",
              "小音量听人声" in keep_md, keep_md[-200:])
        check("drop 状态是 superseded", "status: superseded" in drop_md,
              drop_md[:200])
    finally:
        e.cleanup()


# ---------- RR03 依赖组一项失败，整组不提交 ----------

def rr03():
    print("\nRR03 依赖组一项缺失 → 整组不提交")
    e = Env("rr03")
    try:
        keep_id, drop_id = str(uuid.uuid4()), str(uuid.uuid4())
        e.shared_rec(keep_id, body="保留的那条记录正文内容，长度足够。", title="保留条")
        e.shared_rec(drop_id, body="被合并的那条，带独有细节：这是一个不会通过的细节。", title="被合并条")
        e.git_push_shared()

        r = e.run("merge", keep_id, drop_id, "--note", "测试")
        props = [p for p in sorted((e.work / "changes").glob("*.json"))
                 if not p.name.endswith(".done.json")]
        if not props:
            check("提案存在", False)
            return
        pid = props[0].stem
        e.run("approve", pid)

        # 把提案里 keep 的 frozen_md 改坏，制造「依赖组一项失败」。
        # 直接改正文最可靠 —— yaml.safe_dump 的输出格式不固定，替换字符串容易扑空。
        pf = e.work / "changes" / f"{pid}.json"
        d = json.loads(pf.read_text(encoding="utf-8"))
        touched = False
        for c in d["changes"]:
            if c["action"] == "update" and c.get("frozen_md"):
                lines = c["frozen_md"].split("\n")
                # 在 front matter 结束后的第一行正文加一句
                for i, ln in enumerate(lines):
                    if i > 2 and ln.strip() and not ln.startswith(("---", " ")):
                        lines.insert(i, "被测试篡改的一句话。")
                        break
                c["frozen_md"] = "\n".join(lines)
                touched = True
        pf.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        check("测试成功篡改 frozen_md", touched)

        head_before = e.remote_head()
        r_pub = e.run("publish", pid)
        out = r_pub.stdout + r_pub.stderr
        check("依赖组不完整 → 发布失败", r_pub.returncode != 0, f"exit={r_pub.returncode}")
        check("远端 commit 没变", e.remote_head() == head_before)

        c2 = e.tmp / "clone2"
        subprocess.run(["git", "clone", "-q", str(e.remote), str(c2)], check=True)
        drop_md = (c2 / "memory" / "records" / f"{drop_id}.md").read_text(encoding="utf-8")
        check("drop 没有被单独发布成退役", "status: superseded" not in drop_md,
              "远端 drop 状态被改了")
    finally:
        e.cleanup()


# ---------- RR04 索引失败后第二次无变化仍重建 ----------

def rr04():
    print("\nRR04 索引先失败，第二次零变化 pull 仍补建")
    e = Env("rr04")
    try:
        rid = str(uuid.uuid4())
        e.native_rec(rid)
        e.shared_rec(rid)
        e.git_push_shared()

        e.fail_index()
        r1 = e.run("pull", "--apply")
        check("索引失败 → 非零退出", r1.returncode != 0, f"exit={r1.returncode}")

        st = json.loads(e.state.read_text(encoding="utf-8")) if e.state.exists() else {}
        check("留下 pending_reindex 标记", bool(st.get("pending_reindex")),
              str(list(st.keys())))

        # 恢复索引，不改变任何记录
        e._fake_index(ok=True)
        r2 = e.run("pull", "--apply")
        out = r2.stdout + r2.stderr
        check("第二次不提前返回", "没有要导入的" not in out, out[:150])
        check("第二次重建成功", r2.returncode == 0, f"exit={r2.returncode} {out[:150]}")
        st2 = json.loads(e.state.read_text(encoding="utf-8"))
        check("标记被清除", not st2.get("pending_reindex"))
    finally:
        e.cleanup()


# ---------- RR05 有来源但缺 epistemic ----------

def rr05():
    print("\nRR05 有来源 ≠ observed")
    e = Env("rr05")
    try:
        rid = str(uuid.uuid4())
        e.native_rec(rid, epistemic=None, body="这是我猜的，可能是这样，长度足够通过校验。")
        r = e.run("plan")
        out = r.stdout
        check("缺 epistemic 的本地记录可提案", "提案" in out or "没有要推" in out, out[:150])

        # 直接验转换
        code = (
            "import sys, json; sys.path.insert(0,'" + str(e.work / "sync-core") + "');"
            "sys.path.insert(0,'" + str(e.work / "adapters" / "hermes") + "');"
            "import importlib.util as u;"
            "s=u.spec_from_file_location('a','" + str(e.work / "adapters" / "hermes" / "adapter.py") + "');"
            "m=u.module_from_spec(s); s.loader.exec_module(m);"
            "d=json.load(open('" + str(e.native / f"{rid}.json") + "'));"
            "r=m._build_record(d,'" + rid + "','t');"
            "print(r.epistemic)"
        )
        rr = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=e.env())
        epi = rr.stdout.strip()
        check("有 evidence 但缺 epistemic → 不是 observed", epi != "observed", f"得到 {epi!r}")

        # 显式 inferred 要保留
        rid2 = str(uuid.uuid4())
        e.native_rec(rid2, epistemic="inferred", body="这是推断出来的结论，长度足够通过校验。")
        code2 = code.replace(rid, rid2)
        rr2 = subprocess.run([PY, "-c", code2], capture_output=True, text=True, env=e.env())
        check("显式 inferred 原样保留", rr2.stdout.strip() == "inferred", rr2.stdout.strip())
    finally:
        e.cleanup()


# ---------- RR07 手改元数据必须可见 ----------

def rr07():
    print("\nRR07 导入后手改 status/epistemic/applicability 主流程要看得见")
    e = Env("rr07")
    try:
        rid = str(uuid.uuid4())
        e.shared_rec(rid, body="这条会被导入，然后在本地被手改。")
        e.git_push_shared()
        e.run("pull", "--apply")

        local = e.read_native(rid)
        check("导入成功", local is not None)
        if not local:
            return

        # 只手改元数据，标题和正文不动
        local["status"] = "dormant"
        local["epistemic"] = "inferred"
        local["applicability"] = {"frameworks": ["dsh"], "os": ["windows"], "projects": []}
        (e.native / f"{rid}.json").write_text(
            json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8")

        # 走真实 snapshot_native
        code = (
            "import sys, json; sys.path.insert(0,'" + str(e.work / "sync-core") + "');"
            "import importlib.util as u;"
            "s=u.spec_from_file_location('a','" + str(e.work / "adapters" / "hermes" / "adapter.py") + "');"
            "m=u.module_from_spec(s); s.loader.exec_module(m);"
            "r=m.snapshot_native()['" + rid + "'];"
            "print(json.dumps({'status':r.status,'epistemic':r.epistemic,'app':r.applicability}))"
        )
        out = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=e.env())
        try:
            got = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            got = {}
        check("手改 status 可见", got.get("status") == "dormant", str(got))
        check("手改 epistemic 可见", got.get("epistemic") == "inferred", str(got))
        check("手改 applicability 可见",
              (got.get("app") or {}).get("os") == ["windows"], str(got))
    finally:
        e.cleanup()


# ---------- RR08 依赖组失败不抹掉用户手改 ----------

def rr08():
    print("\nRR08 依赖组失败 → 用户原有手改必须保留")
    e = Env("rr08")
    try:
        keep_id, drop_id = str(uuid.uuid4()), str(uuid.uuid4())
        e.shared_rec(keep_id, body="保留的那条，稍后我会手动改它。", title="保留条")
        e.shared_rec(drop_id, body="被合并的那条，带独有细节：这句会并进去。", title="被合并条")
        e.git_push_shared()

        r = e.run("merge", keep_id, drop_id, "--note", "测试")
        props = [p for p in sorted((e.work / "changes").glob("*.json"))
                 if not p.name.endswith(".done.json")]
        if not props:
            check("提案存在", False, r.stdout[:150])
            return
        pid = props[0].stem
        e.run("approve", pid)

        # 模拟：用户在 keep 文件上有一段未提交的手改
        keep_file = e.work / "memory" / "records" / f"{keep_id}.md"
        user_edit = keep_file.read_text(encoding="utf-8") + "\n这是一段用户自己加的话，还没提交。\n"
        keep_file.write_text(user_edit, encoding="utf-8")

        # 制造组内失败：篡改 drop 的 target_status 成非法值 → 预检会拦
        pf = e.work / "changes" / f"{pid}.json"
        d = json.loads(pf.read_text(encoding="utf-8"))
        for c in d["changes"]:
            if c["action"] == "retire":
                c["target_status"] = "bogus"
        pf.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

        head_before = e.remote_head()
        r_pub = e.run("publish", pid)
        check("失败退出", r_pub.returncode != 0, f"exit={r_pub.returncode}")
        check("远端没变", e.remote_head() == head_before)

        after = keep_file.read_text(encoding="utf-8")
        check("用户手改的那句话还在", "这是一段用户自己加的话" in after,
              after[-120:])
    finally:
        e.cleanup()


# ---------- RR09 check 能发现远端新提交 ----------

def rr09():
    print("\nRR09 另一端推了新提交 → check 要发现")
    e = Env("rr09")
    try:
        e.run("pull", "--apply")
        r0 = e.run("check")
        check("初始状态无差异", r0.returncode == 0, f"exit={r0.returncode} {r0.stdout[-120:]}")

        # 模拟另一端：直接往 bare remote 推一个新提交
        other = e.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(e.remote), str(other)], check=True)
        nid = str(uuid.uuid4())
        sys.path.insert(0, str(e.work / "sync-core"))
        from agent_sync.protocol import Record as R2
        rec = R2(id=nid, kind="fact", depth="recent", status="active", title="另一端的记录",
                 origin="other", updated_by="other",
                 created_at="2026-10-27T11:00:00+08:00", updated_at="2026-10-27T11:00:00+08:00",
                 epistemic="observed",
                 evidence=[{"id": "ev-o", "kind": "session", "locator": "local-ref:other"}],
                 body="这是另一端推上来的记录正文，长度足够。")
        (other / "memory" / "records" / f"{nid}.md").write_text(rec.to_markdown(), encoding="utf-8")
        for k, v in (("user.name", "o"), ("user.email", "o@o")):
            subprocess.run(["git", "config", k, v], cwd=other, check=True)
        subprocess.run(["git", "add", "-A"], cwd=other, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "other push"], cwd=other, check=True)
        subprocess.run(["git", "push", "-q", "origin", "master"], cwd=other, check=True)

        # 本地不 pull，直接 check
        r1 = e.run("check")
        out = r1.stdout + r1.stderr
        check("发现远端有新提交", r1.returncode == 1, f"exit={r1.returncode}")
        check("提示远端已更新", "远端" in out and ("已更新" in out or "git pull" in out), out[-200:])
    finally:
        e.cleanup()


# ---------- RR10 默认 pull（无 --apply）不写 ----------

def rr10():
    print("\nRR10 默认 pull 不带 --apply 时不得写索引或状态")
    e = Env("rr10")
    try:
        rid = str(uuid.uuid4())
        e.native_rec(rid)
        e.shared_rec(rid)
        e.git_push_shared()

        e.fail_index()
        e.run("pull", "--apply")
        st_before = e.state.read_bytes() if e.state.exists() else b""
        check("有 pending_reindex", json.loads(st_before or b"{}").get("pending_reindex"))

        e._fake_index(ok=True)
        r_default = e.run("pull")          # 不带 --apply
        st_after = e.state.read_bytes() if e.state.exists() else b""
        check("默认 pull 没改 state", st_before == st_after,
              f"state 变了 {len(st_before)}→{len(st_after)}")
        out = r_default.stdout + r_default.stderr
        check("默认 pull 提示了欠账", "索引" in out, out[:150])

        r_apply = e.run("pull", "--apply")
        st3 = json.loads(e.state.read_text(encoding="utf-8"))
        check("--apply 才补建并清标记",
              r_apply.returncode == 0 and not st3.get("pending_reindex"),
              f"exit={r_apply.returncode} pending={st3.get('pending_reindex')}")
    finally:
        e.cleanup()


# ---------- RR11 远端查询失败不能报"没有差异" ----------

def rr11():
    print("\nRR11 查不到远端 → 退出 2，不能报没差异")
    e = Env("rr11")
    try:
        e.run("pull", "--apply")           # 先建立"无差异"状态
        r0 = e.run("check")
        check("正常时无差异返回 0", r0.returncode == 0, f"exit={r0.returncode}")

        mem_before = {p.name: p.read_bytes() for p in (e.work / "memory" / "records").glob("*.md")}
        st_before = e.state.read_bytes() if e.state.exists() else b""

        # origin 指向不存在的路径
        g = lambda *a: subprocess.run(["git", *a], cwd=e.work, capture_output=True, text=True)
        g("remote", "set-url", "origin", str(e.tmp / "does-not-exist.git"))

        r1 = e.run("check")
        out = r1.stdout + r1.stderr
        check("远端不可达 → 退出 2", r1.returncode == 2, f"exit={r1.returncode}")
        check("明确说远端状态未知", "远端" in out and ("未知" in out or "失败" in out), out[-250:])
        check("没有误报「没有差异」", "没有差异" not in out, out[-250:])

        mem_after = {p.name: p.read_bytes() for p in (e.work / "memory" / "records").glob("*.md")}
        st_after = e.state.read_bytes() if e.state.exists() else b""
        check("记忆文件没被改", mem_before == mem_after)
        check("state 没被改", st_before == st_after)

        # 恢复 origin，应回到 0
        g("remote", "set-url", "origin", str(e.remote))
        r2 = e.run("check")
        check("恢复后回到 0", r2.returncode == 0, f"exit={r2.returncode}")
    finally:
        e.cleanup()


# ---------- RR06 push 失败后补推再核对 ----------

def rr06():
    print("\nRR06 push 失败 → 补推后基线准确")
    e = Env("rr06")
    try:
        # 先把仓库自带的记录同步一遍，建立基线 —— 否则它们会被当成"远端新记录"
        e.run("pull", "--apply")

        rid = str(uuid.uuid4())
        e.native_rec(rid, body="本地新记录，准备推送上去，长度足够通过校验。")

        r_plan = e.run("plan")
        props = [p for p in sorted((e.work / "changes").glob("*.json"))
                 if not p.name.endswith(".done.json")]
        if not props:
            check("提案生成", False, r_plan.stdout[:150])
            return
        pid = props[0].stem
        e.run("approve", pid)

        # 把 remote 指到一个不存在的路径，制造 push 失败
        g = lambda *a: subprocess.run(["git", *a], cwd=e.work, capture_output=True, text=True)
        g("remote", "set-url", "origin", str(e.tmp / "nowhere.git"))
        r_fail = e.run("publish", pid)
        check("push 失败 → 非零退出", r_fail.returncode != 0, f"exit={r_fail.returncode}")

        st = json.loads(e.state.read_text(encoding="utf-8")) if e.state.exists() else {}
        # 前面 pull 过，基线本来就有内容。要看的是**这条新记录**有没有被记进去
        check("失败时新记录没有进 mappings",
              rid not in (st.get("mappings") or {}),
              str([k[:8] for k in (st.get("mappings") or {})])[:120])
        snap = st.get("last_snapshot") or {}
        check("失败时远端快照不含这条新记录",
              rid not in snap, str([k[:8] for k in snap])[:120])

        # 恢复 remote，手动补推
        g("remote", "set-url", "origin", str(e.remote))
        g("push", "-q", "origin", "master")
        check("补推后远端有内容",
              subprocess.run(["git", "cat-file", "-e", f"master:memory/records/{rid}.md"],
                             cwd=e.remote, capture_output=True).returncode == 0)

        # 补推后跑 reconcile 收尾
        r_rec = e.run("reconcile", pid)
        out_rec = r_rec.stdout + r_rec.stderr
        check("reconcile 成功", r_rec.returncode == 0, f"exit={r_rec.returncode} {out_rec[:200]}")

        st2 = json.loads(e.state.read_text(encoding="utf-8"))
        check("reconcile 后新记录进了 mappings", rid in (st2.get("mappings") or {}))
        check("reconcile 后进了基线快照", rid in (st2.get("last_snapshot") or {}))

        # 再改本地同一条，plan 应该出 update 而不是 create
        nf = e.native / f"{rid}.json"
        d = json.loads(nf.read_text(encoding="utf-8"))
        d["body"] = d["body"] + " 又补了一句。"
        nf.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        r2 = e.run("plan")
        out2 = r2.stdout + r2.stderr
        check("再次 plan 是 update 不是 create",
              "修改 1" in out2 or "新增 0" in out2, out2[:200])
    finally:
        e.cleanup()


for fn in (rr01, rr02, rr03, rr04, rr05, rr06, rr07, rr08, rr09, rr10, rr11):
    try:
        fn()
    except Exception as ex:
        check(f"{fn.__name__} 异常", False, f"{type(ex).__name__}: {ex}")

fails = [r for r in results if not r[1]]
print(f"\n{'全部通过' if not fails else f'失败 {len(fails)} 项'}  （{len(results)} 条检查）")
for n, ok, d in fails:
    print(f"  ! {n}   {d}")
sys.exit(1 if fails else 0)
