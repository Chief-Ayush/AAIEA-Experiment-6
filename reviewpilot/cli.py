"""Command line:  python -m reviewpilot.cli <command>

  tools                         list the tools every MCP server offers (tools/list)
  review  PR_ID [--sequential]  review a pull request (auto approval mode)
  ask     "request" [--repo R]  developer workbench request
  eval    [--sections a,b]      run the evaluation suites and write storage/reports/run_*/
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Settings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reviewpilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools")
    r = sub.add_parser("review")
    r.add_argument("pr_id")
    r.add_argument("--sequential", action="store_true")
    a = sub.add_parser("ask")
    a.add_argument("request")
    a.add_argument("--repo", default="calculator")
    e = sub.add_parser("eval")
    e.add_argument("--sections", default="")
    e.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)

    from .mcp_client import get_hub
    hub = get_hub()
    try:
        if args.cmd == "tools":
            for t in sorted(hub.tools().values(), key=lambda t: (t.server, t.name)):
                print(f"{t.server:<11} {t.name:<22} {t.risk:<7} {t.description.splitlines()[0][:70]}")
        elif args.cmd == "review":
            from . import pipeline
            st = pipeline.review(args.pr_id, Settings(execution_mode="sequential" if args.sequential else "parallel"))
            if st["status"] == "rejected":
                print("rejected:", st["guard"]["reason"])
                return 1
            print(f"{st['trace_id']}  {st['pr_id']}  {st['human']['final_verdict']}  score {st['verdict']['score']}")
            print(st["verdict"]["summary"])
            for f in st["final_findings"]:
                print(f"  [{f['severity']:<8}] {f['rule']} {f['path']}:{f['line'] or '-'}  {f['message']}")
            m = st["exec_metrics"]
            print(f"{m['tool_calls']} MCP calls, {m['phases']} phases, {m['wall_ms']:.0f} ms, DOP {m['dop']}, DOS {m['dos']}  report: {st['report_path']}")
        elif args.cmd == "ask":
            from . import pipeline
            st = pipeline.workbench(args.request, args.repo)
            print(st["status"])
            print(st.get("answer", ""))
        elif args.cmd == "eval":
            from .evaluation.runner import run_all
            rep = run_all([s for s in args.sections.split(",") if s] or None, progress=lambda f, m: print(f"{f:5.0%} {m}", file=sys.stderr),
                          workers=args.workers)
            print(json.dumps(rep["headline"], indent=1))
            print("report:", rep["report_dir"])
    finally:
        hub.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
