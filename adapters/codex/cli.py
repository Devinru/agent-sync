"""Manual Codex bridge. No publish, approval creation, scheduler or daemon."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter import Adapter, encoded


def main():
    # Codex tools consume UTF-8 JSON even when Windows' parent console is GBK.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="Codex home; default CODEX_HOME or ~/.codex")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("command", choices=["inspect", "plan", "apply", "verify", "recall", "export-plan", "translate", "fetch"])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--include-inactive", action="store_true")
    args = parser.parse_args()
    ad = Adapter(args.repo, args.home)
    try:
        if args.command == "inspect":
            result = ad.inspect()
        elif args.command == "plan":
            result = ad.plan_import()
        elif args.command == "export-plan":
            result = ad.propose_export()
        elif args.command == "translate":
            import dataclasses
            from translate import translate_registry
            source = ad.home / "memories" / "MEMORY.md"
            records, review = translate_registry(source.read_text(encoding="utf-8"), existing=ad.shared()[0])
            result = {"status": "local_candidates_needs_review", "records": [dataclasses.asdict(r) for r in records],
                      "review": review, "published": False}
        elif args.command == "apply":
            if not args.plan or not args.authorization:
                parser.error("apply requires --plan and --authorization (explicit user confirmation reference)")
            result = ad.apply_import(json.loads(args.plan.read_text(encoding="utf-8")), json.loads(args.authorization.read_text(encoding="utf-8")))
        elif args.command == "verify":
            result = ad.verify_import()
        elif args.command == "recall":
            if not args.query:
                parser.error("recall requires query")
            result = ad.recall(args.query, args.include_inactive)
        else:
            # Read remote data only. Local code/docs may be modified, so don't
            # git pull/reset the adapter; safely fast-forward only shared paths.
            if args.output:
                parser.error("fetch does not accept --output")
            def git(*parts):
                return subprocess.run(["git", *parts], cwd=ad.repo, check=True, capture_output=True, text=True, encoding="utf-8")
            git("fetch", "origin", "master")
            head = git("rev-parse", "HEAD").stdout.strip()
            remote = git("rev-parse", "origin/master").stdout.strip()
            subprocess.run(["git", "merge-base", "--is-ancestor", head, remote], cwd=ad.repo, check=True, capture_output=True)
            # pull --ff-only preserves untracked adapter files and refuses conflicts.
            git("merge", "--ff-only", "origin/master")
            result = {"fetched_commit": remote, "imported": False, "next": "plan"}
        data = encoded(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(data)
            print(json.dumps({"saved": str(args.output), "plan_hash": result.get("plan_hash") if isinstance(result, dict) else None}, ensure_ascii=False))
        else:
            print(data.decode("utf-8"), end="")
        return 1 if isinstance(result, dict) and result.get("ok") is False else 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"停止：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
