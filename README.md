# ReviewPilot — Pull-Request Review Assistant over the Model Context Protocol

**Experiment 6 · Case study: Developer Automation System using the MCP protocol (developer-tools domain).**
Focus: protocol-driven system integration and developer tooling.

A developer opens a pull request; ReviewPilot reviews it. Fourteen LangGraph agents plan the review, call **17 tools on four MCP
servers over stdio**, run the affected tests first and the full suite only when they pass, turn the results into validated
findings and a merge verdict (APPROVE / COMMENT / REQUEST_CHANGES / BLOCK), and let a human approve what is posted and any
automatic fix before anything is written. A Developer Workbench turns plain-language requests ("Analyze calculator.py and run its
tests") into multi-tool MCP plans.

Stack: **MCP Python SDK 1.x** (FastMCP servers, `ClientSession` + `stdio_client`) · **LangGraph 1.x** (StateGraph, parallel fan-out,
`interrupt` / `Command(resume=...)`, MemorySaver) · asyncio · jsonschema · pytest · Streamlit · Altair. No API key is needed.

## Quick start

Requires Python 3.10 or newer (tested with 3.11 and 3.13; Windows and Linux).

```bash
pip install -r requirements.txt
streamlit run Home.py
```

1. **Settings -> GitHub**: paste a personal access token ([create one](https://github.com/settings/tokens/new?scopes=repo&description=ReviewPilot),
   classic with the `repo` scope) or run `gh auth login` once - the GitHub CLI token is used automatically. The token is stored only in
   `.env` in this folder (git-ignored).
2. **Settings -> Demo repositories -> Create demo** (optional): creates `reviewpilot-calculator`, `reviewpilot-inventory` and
   `reviewpilot-textkit` in your account with 13 open pull requests (clean changes, failing tests, a shell call, a hard-coded password,
   a prompt-injection attempt, ...).
3. Pick a repository in the sidebar, open **Pull requests**, select a PR (or paste a PR link) and press **Review**. Untick nothing,
   choose fixes if you want them, then **Post to GitHub**: the review appears on the PR with inline comments, and approved fixes are
   pushed as one commit to the PR branch after the test suite passed on the fixed code.

Without a token the app works on the bundled sample repositories (offline).

```bash
python -m reviewpilot.cli review owner/repo#12     # or a sample id such as INV-09
python -m reviewpilot.cli ask "Analyze calculator.py and run its tests"
python -m reviewpilot.cli eval                     # full evaluation on the offline benchmark -> storage/reports/run_*/
python -m pytest                                   # 34 tests, incl. GitHub end-to-end against a fake GitHub API
```

Notes for real repositories: tests run with the Python environment ReviewPilot runs in, so install the project's own dependencies
there. Failing tests are re-run on the base branch, so failures that already exist on `main` are reported as pre-existing instead of
being blamed on the PR. GitHub does not allow approving or requesting changes on your own PR, so on your own PRs the review is posted
as a comment that states the verdict.

## MCP servers (`reviewpilot/servers/`)

| Server | Tools | Risk |
|---|---|---|
| Repository (`repo_server`) | `list_files`, `read_file`, `search_code`, `list_pull_requests`, `get_pull_request`, `get_diff` (GitHub or samples) + resources | LOW |
| Analysis (`analysis_server`) | `analyze_python` (AST, cyclomatic complexity), `compare_versions` (main vs PR), `find_usages` | LOW |
| Quality (`quality_server`) | `run_tests` (pytest in an isolated checkout, timeout), `lint`, `scan_secrets`, `scan_security` | run_tests MEDIUM, others LOW |
| Automation (`automation_server`) | `create_autofix_branch` (HIGH), `push_autofix_commit` (HIGH), `delete_autofix_branch`, `post_review` (posts on GitHub), `get_review` | HIGH / MEDIUM / LOW |

Every tool has a typed output schema (`structuredContent`), MCP annotations (`readOnlyHint`, `destructiveHint` ...) and a risk level in
`_meta`. Errors carry a category prefix — `[INPUT]`, `[SECURITY]`, `[TOOL]`, `[TIMEOUT]`, `[EXECUTION]` — so the client can classify them.
Every file path passes `safe_project_path()` (`../../etc/passwd`, absolute paths, drive letters and NUL bytes are rejected).

The client (`reviewpilot/mcp_client.py`) keeps one persistent stdio session per server (each session lives in its own owner task on
a background event loop), validates arguments against the input schema before `tools/call`, applies timeouts, retries only
TIMEOUT / EXECUTION errors (max 2, exponential back-off), reconnects after a server crash, and validates the output schema.

## Review workflow (`reviewpilot/graph.py`, `reviewpilot/agents/`)

```
IntakeGuard → Planner → Supervisor → PolicyGuard → Executor
   → DiffAnalyst | TestImpactAgent | SecurityReviewer | QualityReviewer   (parallel)
   → Validator → VerdictAgent → HumanApproval (interrupt) → [FixAgent] → Reporter
```

Executor phases for a typical PR: **1** `get_diff`, `compare_versions` ×n, `scan_secrets`, `scan_security`, `lint` (parallel) →
**2** `find_usages` of the changed public functions → **3** `run_tests` on the affected tests → **4** full suite, only if the
affected tests passed. The Developer Workbench uses RequestGuard → TaskPlanner → Supervisor → PolicyGuard → Executor →
ResultValidator → Summarizer.

| Lab-manual requirement | Where |
|---|---|
| MCP tools: list_files, read_file, search_code, analyze_python, run_tests | `servers/repo_server.py`, `analysis_server.py`, `quality_server.py` |
| Planner, Supervisor (tool exists, arguments valid), Safety/Policy (risk levels) | `agents/review.py` `planner`, `supervisor`, `policy_guard` |
| Parallel execution with asyncio.gather, dependency-aware execution | `executor.py`, `planning.schedule` |
| Validator (status, structure, expected output) | `agents/review.py` `validator`, `mcp_client.validate_output` |
| Failure classification and controlled retry | `mcp_client.classify`, `RETRYABLE`, `acall` |
| Path security | `workspace.safe_project_path` + PolicyGuard |
| TRACE-XXXXXXXX / TASK-001, mcp_traces.jsonl, execution.log, developer_automation_report.json | `tracing.py`, `storage/mcp_logs/` |
| DOP / DOS, sequential vs parallel, Variation 9 (4 calls × 5 runs) | `executor.py` metrics, `evaluation/suites.run_performance` |
| Optimisation table (9 techniques) | `evaluation/suites.run_optimization` |

## Pages

| Page | What it does |
|---|---|
| Pull requests | Open PRs of the selected repository; review one; ignore findings, choose fixes, post to GitHub. |
| Ask | Chat-style code tasks ("Where is divide used?", "Run the test suite") answered with MCP tool calls. |
| Code | Browse files with AST metrics; run the test suite. |
| Settings | GitHub token / CLI status, one-click demo repositories, review defaults. |
| Lab: How it works | MCP servers, workflow graph, every agent, failure handling, tool risk, review rules. |
| Lab: Batch testing / Evaluation / Run evaluation | Many PRs at once (benchmark, picked, CSV, or a GitHub repository's open PRs) and the full evaluation. |
| Lab: Security tests / MCP inspector / Traces | Attack console, raw tools/list and tools/call, mcp_traces.jsonl and logs. |

## Data and evaluation

* `data/repos/` — three sample projects (`calculator` from the lab manual, `inventory`, `textkit`), all tests passing on main.
* `data/prs/` — 62 pull requests built from `reviewpilot/benchmark/catalog.py`, each labelled with the expected verdict and findings
  (`data/eval/pr_labels.json`). Includes clean features and refactors, failing tests, removed tests, secrets, dangerous calls, SQL
  injection, prompt injection in the PR text, and a few cases the rule-based reviewer is expected to miss.
* `reviewpilot/evaluation/` — review benchmark, 32 labelled workbench requests, 26 security tests, 16 fault-injection scenarios
  (faults are injected through `storage/mcp_logs/faults.json`), 12 human-in-the-loop scenarios, performance and optimisation.

Known limits (kept visible in the report): behaviour changes to functions that already have tests (INV-15, TXT-12) and a
paraphrased instruction to approve (TXT-16) are not detected; two workbench phrasings without file names are not understood.
