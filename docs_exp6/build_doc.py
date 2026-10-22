import json, re
from pathlib import Path
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path("..")
R = json.load(open(ROOT / "storage/reports/latest_report.json"))
T, S = R["tables"], R["summaries"]
TRACES = [json.loads(l) for l in open(ROOT / "storage/mcp_logs/mcp_traces.jsonl")]
V9 = json.load(open("var9.json"))
FONT = "Times New Roman"

doc = Document()
st = doc.styles["Normal"]; st.font.name = FONT; st.font.size = Pt(10)
st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
st.paragraph_format.space_after = Pt(3); st.paragraph_format.space_before = Pt(0)


def setup(sec, margin, cols=1):
    sec.page_width, sec.page_height = Inches(8.27), Inches(11.69)
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Inches(margin)
    pr = sec._sectPr
    for c in pr.findall(qn("w:cols")): pr.remove(c)
    c = OxmlElement("w:cols"); c.set(qn("w:num"), str(cols)); c.set(qn("w:space"), "283"); pr.append(c)


setup(doc.sections[0], 1.0)


def run(p, text, bold=False, italic=False, size=None, color=None):
    r = p.add_run(text); r.bold = bold; r.italic = italic; r.font.name = FONT
    r._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)
    if size: r.font.size = Pt(size)
    if color: r.font.color.rgb = RGBColor(*color)
    return r


def para(text="", bold=False, italic=False, size=10, align=WD_ALIGN_PARAGRAPH.JUSTIFY, after=3, before=0, keep=False):
    p = doc.add_paragraph(); p.alignment = align
    p.paragraph_format.space_after = Pt(after); p.paragraph_format.space_before = Pt(before)
    if keep: p.paragraph_format.keep_with_next = True
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part: run(p, part, bold=bold or i % 2 == 1, italic=italic, size=size)
    return p


def h1(t): return para(t, bold=True, size=12, align=WD_ALIGN_PARAGRAPH.LEFT, before=7, after=3, keep=True)
def h2(t): return para(t, bold=True, size=10.5, align=WD_ALIGN_PARAGRAPH.LEFT, before=4, after=2, keep=True)


def bullets(items, size=10):
    for it in items:
        p = para("", size=size, after=1); p.paragraph_format.left_indent = Inches(0.22); p.paragraph_format.first_line_indent = Inches(-0.15)
        run(p, "• ", size=size)
        for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", it)):
            if part: run(p, part, bold=i % 2 == 1, size=size)


FIG = [0]; TAB = [0]


def caption(kind, text):
    if kind == "Fig": FIG[0] += 1; n = FIG[0]
    else: TAB[0] += 1; n = TAB[0]
    return para(f"{kind}. {n}: {text}", bold=True, size=9, align=WD_ALIGN_PARAGRAPH.CENTER, after=5, before=2 if kind == "Fig" else 0, keep=(kind == "Table"))


def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr(); s = OxmlElement("w:shd")
    s.set(qn("w:val"), "clear"); s.set(qn("w:color"), "auto"); s.set(qn("w:fill"), color); tcPr.append(s)


def table(header, rows, widths, size=8.5, center=()):
    t = doc.add_table(rows=1, cols=len(header)); t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False

    def fill(cell, text, bold=False, ci=0):
        cell.text = ""; p = cell.paragraphs[0]; p.paragraph_format.space_after = Pt(0)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (ci in center or bold) else WD_ALIGN_PARAGRAPH.LEFT
        run(p, str(text), bold=bold, size=size)
    for i, h in enumerate(header): fill(t.rows[0].cells[i], h, True, i); shade(t.rows[0].cells[i], "D9D9D9")
    th = OxmlElement("w:tblHeader"); th.set(qn("w:val"), "true"); t.rows[0]._tr.get_or_add_trPr().append(th)
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r): fill(cells[i], v, False, i)
    for i, w in enumerate(widths): t.columns[i].width = Inches(w)
    for row in t.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for i, w in enumerate(widths): row.cells[i].width = Inches(w)
    lay = OxmlElement("w:tblLayout"); lay.set(qn("w:type"), "fixed")
    look = t._tbl.tblPr.find(qn("w:tblLook"))
    (look.addprevious(lay) if look is not None else t._tbl.tblPr.append(lay))
    tw = t._tbl.tblPr.find(qn("w:tblW"))
    if tw is None:
        tw = OxmlElement("w:tblW"); t._tbl.tblPr.append(tw)
    tw.set(qn("w:type"), "dxa"); tw.set(qn("w:w"), str(int(sum(widths) * 1440)))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def image(path, width, caption_text=None):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after = Pt(1); p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(path, width=Inches(width))
    if caption_text: caption("Fig", caption_text)


def pair(items, width=3.7):
    """2-up screenshots: [(path, caption)]; path None = empty frame for a screenshot to be inserted."""
    t = doc.add_table(rows=2, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False
    for i, (path, cap) in enumerate(items):
        c = t.rows[0].cells[i]; c.width = Inches(width + 0.1); c.text = ""
        p = c.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if path: p.add_run().add_picture(path, width=Inches(width))
        elif cap:
            run(p, "\n\n[Screenshot to be inserted]\n\n", italic=True, size=9, color=(120, 120, 120))
            b = OxmlElement("w:tcBorders")
            for e in ("top", "left", "bottom", "right"):
                x = OxmlElement(f"w:{e}"); x.set(qn("w:val"), "dashed"); x.set(qn("w:sz"), "6"); x.set(qn("w:color"), "888888"); b.append(x)
            c._tc.get_or_add_tcPr().append(b)
        c2 = t.rows[1].cells[i]; c2.width = Inches(width + 0.1); c2.text = ""
        if cap:
            FIG[0] += 1
            q = c2.paragraphs[0]; q.alignment = WD_ALIGN_PARAGRAPH.CENTER; run(q, f"Fig. {FIG[0]}: {cap}", bold=True, size=9)
    for row in t.rows: row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ================================================================================================ front page
for _ in range(2): para("")
para("[College logo placeholder]", italic=True, size=11, align=WD_ALIGN_PARAGRAPH.CENTER, after=14)
para("Department of Computer Engineering", bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER, after=14)
para("EXPERIMENT NO. 6", bold=True, size=20, align=WD_ALIGN_PARAGRAPH.CENTER, after=10)
para("Case Study – Developer Automation System using MCP Protocol (Developer Tools Domain)", bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER, after=6)
para("ReviewPilot: a pull-request review assistant over the Model Context Protocol", italic=True, size=12, align=WD_ALIGN_PARAGRAPH.CENTER, after=30)
t = doc.add_table(rows=0, cols=2); t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
for k, v in [("Course", "Agentic AI Engineering and Enterprise Automation (AAIEA)"), ("Name", "[Student name]"), ("Roll No.", "[Roll number]"),
             ("Experiment No.", "6"), ("Date", "22-10-2026"), ("Grade / Signature", "")]:
    c = t.add_row().cells; c[0].width = Inches(1.8); c[1].width = Inches(4.2)
    for cell, txt, b in ((c[0], k, True), (c[1], v, False)):
        cell.text = ""; p = cell.paragraphs[0]; run(p, txt, bold=b, size=11); p.paragraph_format.space_after = Pt(4); p.paragraph_format.space_before = Pt(4)

doc.add_section(WD_SECTION.NEW_PAGE); setup(doc.sections[-1], 0.3)

# ================================================================================================ 1 aim
h1("1. Aim, Objectives and Case Study")
para("**Aim:** To design and implement a Developer Automation System using the Model Context Protocol (MCP) in which an AI agent communicates with developer tools through a standardised client-server architecture. The tasks covered are file listing and reading, code search, Python (AST) analysis, automated testing, tool discovery, sequential and parallel tool execution, validation, failure handling and recovery, execution tracing and performance monitoring.")
para("**Case study chosen: a pull-request review assistant (ReviewPilot).** A developer opens a pull request (PR) and the system reviews it: it fetches the PR over MCP, plans which tools to run, runs the affected tests first and the whole suite only when they pass, turns tool results into validated findings, proposes a merge verdict (APPROVE / COMMENT / REQUEST_CHANGES / BLOCK) and lets a human decide what is posted and whether an automatic fix is applied. A second graph, the Developer Workbench, turns plain-language requests such as “Analyze calculator.py and run its tests” into multi-tool MCP plans, which reproduces the lab-manual scenario. The case fits “protocol-driven system integration and developer tooling” for three reasons: every capability (repository access, AST analysis, pytest, scanners, review posting) is a separate tool that must be discovered and called through one protocol; tools have very different risk (read-only, process-spawning, writing), which exercises the policy layer; and a review has real data dependencies (usages, then affected tests, then full suite), which exercises dependency-aware parallel scheduling.")
h2("1.1 Objectives")
bullets(["Understand the architecture of the Model Context Protocol (host, client, server, tools, resources, JSON-RPC over stdio).",
         "Implement MCP servers in Python with FastMCP and an MCP client with ClientSession and the stdio transport.",
         "Discover tools dynamically (tools/list) and execute them (tools/call) with typed input and output schemas.",
         "Implement planner and supervisor agents, a safety and policy layer, and a validator.",
         "Demonstrate sequential and parallel execution and dependency-aware workflow execution; report DOP and DOS.",
         "Implement failure classification, controlled retry and recovery, execution tracing (TRACE and TASK ids, JSONL) and performance monitoring.",
         "Compare sequential and parallel execution of four independent tool calls over five runs (student Variation 9)."])
h2("1.2 Tools and technologies")
caption("Table", "Tools and technologies used")
table(["Technology", "Purpose"], [
    ["Python, asyncio", "Core implementation; concurrent tool calls with asyncio.gather on a background event loop"],
    ["MCP Python SDK 1.x (1.30.0 installed), FastMCP", "Four MCP servers; tools registered with the @mcp.tool decorator, typed output schemas, annotations and _meta"],
    ["ClientSession, stdio_client", "One persistent stdio session per server; initialize, list_tools, call_tool, list_resources"],
    ["LangGraph 1.x (1.2.14 installed)", "StateGraph for the review and workbench workflows, parallel fan-out, interrupt() and Command(resume) for human approval, MemorySaver"],
    ["jsonschema", "Client-side validation of arguments (input schema) and results (output schema)"],
    ["pytest, ast", "Automated tests in an isolated checkout; AST analysis, complexity and symbol usage"],
    ["Streamlit, Altair, pandas", "Eleven-page interface, evaluation charts, MCP inspector"],
    ["JSON, JSONL, logging", "Reports, persistent trace records (mcp_traces.jsonl), execution.log"]], [2.4, 5.2])
para("The manual lists Google Colab as the development environment; this implementation runs locally as a Streamlit application and a command-line tool and needs no API key (planning is rule based and deterministic).", size=9.5)

# ================================================================================================ 2 theory
h1("2. Theory")
h2("2.1 Model Context Protocol")
para("MCP is an open protocol that lets an AI application use external capabilities through one interface instead of a custom integration per tool. A **host** (here ReviewPilot) creates one **client** per **server**; each server exposes capabilities of three kinds: **tools** (functions the model or agent can invoke), **resources** (readable data addressed by URI) and **prompts** (reusable templates). Messages are JSON-RPC 2.0. In the stdio transport the client starts the server as a subprocess and exchanges messages over its standard input and output; this is how ReviewPilot launches its four servers (python -m reviewpilot.servers.<name>_server).")
para("A session begins with **initialize**: client and server exchange protocol version, capabilities and identity, which is called capability negotiation. In this project the MCP Inspector page shows each server answering with protocol version 2025-11-25, serverInfo reviewpilot-repo, -analysis, -quality and -automation, and the capabilities experimental, prompts, resources and tools. The client then calls **tools/list**, which returns for each tool a name, description, a JSON Schema for the input (inputSchema), optionally a schema for the result (outputSchema) and **annotations**. A call is **tools/call** with the tool name and arguments; the result carries content (text) and, when the tool declares an output schema, **structuredContent**, a JSON object the client can validate and use without parsing text. A tool failure is returned as a result with isError set, which is different from a protocol error. Annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) are hints about behaviour; ReviewPilot reads them together with an explicit risk level stored in the tool metadata (_meta) when it decides what a tool may do. The servers define resources (repo://{repo}/readme, pr://{pr_id}/description, autofix://rules) but no prompt templates.")
h2("2.2 MCP compared with plain function calling and REST")
para("Plain function calling binds tool definitions to one application and one model API: the definitions live in the prompt-building code and the functions run in the same process, so adding a tool means changing the agent. With MCP the tools live in separate servers and are **discovered at run time**; the Supervisor in this project calls tools/list before every review and rejects any task whose tool is not offered or whose arguments do not match the schema. A REST API offers similar remote access but has no common discovery, schema, annotation or session convention, so every client needs bespoke code for each service. MCP standardises exactly those parts, at the cost of a session (process start, about 0.7 s per server here) and one more layer to secure; the measured effect of keeping sessions open is shown in Section 5.3.")
h2("2.3 Multi-agent orchestration with LangGraph")
para("The workflow is a LangGraph **StateGraph**: each agent is a node that reads and writes a shared typed state (ReviewState), and edges define the order. After the Executor the graph **fans out** to four reviewer nodes that run in the same super-step, and a join edge waits for all four before the Validator. **Human approval** uses interrupt(): the graph pauses and returns a payload (verdict, findings, fixable rules); the application resumes it later with Command(resume=decision) using the MemorySaver checkpointer, so nothing is posted or written until a person decides. A conditional edge after approval routes to the FixAgent only when a person approved at least one auto-fix rule.")
h2("2.4 Tool risk levels and safety")
para("Every tool has a risk level: **LOW** (read-only), **MEDIUM** (starts a process or posts a review) and **HIGH** (writes files or pushes a commit). The PolicyGuard allows LOW and MEDIUM tools, checks every path argument with safe_project_path() (which rejects absolute paths, drive letters, NUL bytes and any path that resolves outside the repository), rejects test targets with shell metacharacters, and requires a human for HIGH. The automatic review never calls a HIGH tool. Text inside a pull request (title, description, commit messages, added comments) is treated as untrusted data: the IntakeGuard scans it for instructions aimed at the reviewer (override, forced verdict, role-play) and the SecurityReviewer reports them as finding SEC003; the text is never used as an instruction.")
h2("2.5 Failure classification and controlled retry")
para("Errors carry a category prefix and the client classifies each result before deciding what to do. Only transient categories are retried, with a limit and exponential back-off; retrying an input or security error cannot succeed and only wastes calls. This is the manual’s Table 4 as implemented.")
caption("Table", "Failure classification and recovery action (as implemented)")
table(["Category", "Example", "Action in ReviewPilot"], [
    ["INPUT", "File not found, argument of the wrong type, unknown tool", "Stop, no retry. Schema errors are rejected by the client before any round trip (attempts = 0)"],
    ["TOOL", "Tool returned an error result", "Stop and report; the review lists the evidence gap"],
    ["SECURITY", "Path outside the project, unsafe test target", "Stop, no retry"],
    ["TIMEOUT", "pytest or a tool exceeds its timeout", "Retry, at most 2 times, back-off 0.05 s doubled each attempt"],
    ["EXECUTION", "Unexpected failure, transport failure, server process crash", "Retry (limited); after a crash the client reconnects (new stdio session) and repeats the call"]], [0.9, 3.0, 3.7])
h2("2.6 Degree of parallelism (DOP) and degree of sequential execution (DOS)")
para("DOP is the number of independent tool calls that can run at the same time; DOS is the amount of work that must stay sequential because of dependencies. In this project the Executor groups tasks into phases by their dependencies: calls inside a phase run with asyncio.gather (bounded by a semaphore, max_parallel = 8) and phases run one after another. It reports DOP as the widest phase and DOS as the number of phases; in sequential mode every call is its own step (DOP 1, DOS = number of calls). Parallelism helps when calls take long or wait; for very short calls, scheduling and the single stdio connection per server dominate, which the Variation 9 measurements show.")

# ================================================================================================ 3 design
h1("3. System Design")
image("images/d_architecture.png", 5.0, "Architecture of ReviewPilot: UI, LangGraph agents, MCP client, four MCP servers and the repositories behind them")
image("images/d_workflow.png", 5.2, "Review workflow (LangGraph): guard, plan, supervise, check policy, execute, four parallel reviewers, validate, verdict, human approval, optional fix, report")
image("images/d_phases.png", 5.6, "Executor phases for a typical PR; independent calls share a phase and run with asyncio.gather")
h2("3.1 MCP tools")
para("The four servers expose 18 tools (the manual asks for five: list_files, read_file, search_code, analyze_python, run_tests, all of which are present). Risk is declared by the server in each tool’s _meta and annotations and read by the client at discovery.")
caption("Table", "MCP tools, servers, purpose and risk level")
TOOLS = [("list_files", "repo", "List files of a repository or of a PR head", "LOW"), ("read_file", "repo", "Read a file or line range (path-confined)", "LOW"),
         ("search_code", "repo", "Text or regex search over source files", "LOW"), ("list_pull_requests", "repo", "List open pull requests", "LOW"),
         ("get_pull_request", "repo", "PR metadata and changed files", "LOW"), ("get_diff", "repo", "Added and removed lines of every changed file", "LOW"),
         ("analyze_python", "analysis", "AST analysis: functions, complexity, imports, TODOs", "LOW"), ("compare_versions", "analysis", "Main vs PR: functions, complexity delta, unused imports, tests", "LOW"),
         ("find_usages", "analysis", "Where a symbol is used; whether tests use it", "LOW"), ("run_tests", "quality", "pytest in an isolated checkout with timeout", "MEDIUM"),
         ("lint", "quality", "Debug calls, bare except, mutable defaults, complexity", "LOW"), ("scan_secrets", "quality", "Hard-coded credentials (values masked)", "LOW"),
         ("scan_security", "quality", "eval/exec, shell calls, unsafe deserialisation, SQL by string", "LOW"), ("create_autofix_branch", "automation", "Apply safe fixes on a review branch", "HIGH"),
         ("push_autofix_commit", "automation", "Push the fix as one commit to the PR branch", "HIGH"), ("delete_autofix_branch", "automation", "Revert a fix branch", "MEDIUM"),
         ("post_review", "automation", "Post verdict and inline comments (GitHub or stored)", "MEDIUM"), ("get_review", "automation", "Read the stored review of a PR", "LOW")]
table(["Tool", "Server", "Purpose", "Risk"], [list(x) for x in TOOLS], [1.6, 0.9, 4.3, 0.8], center=(1, 3))

# ================================================================================================ 4 implementation
h1("4. Implementation")
h2("4.1 Agents")
para("Fourteen agents form the review graph; the workbench graph reuses the Supervisor, PolicyGuard and Executor and adds RequestGuard, TaskPlanner, ResultValidator and Summarizer. A reviewer owns its rules, so a finding can be traced to the agent and the tool result that produced it.")
caption("Table", "Agents of the review workflow")
table(["Agent", "Role", "Input", "Output"], [
    ["IntakeGuard", "Validates the PR id, fetches the PR, screens its text, checks paths", "PR id", "pr, guard (injection hits)"],
    ["Planner", "Changed files to tool tasks with dependencies", "pr", "plan (depends_on, resolver, condition)"],
    ["Supervisor", "Fresh tools/list; each task must name an offered tool and match its schema", "plan", "approved plan, checks"],
    ["PolicyGuard", "Risk level and path/target checks per task", "plan, tool risks", "allow / deny decisions"],
    ["Executor", "Runs the plan over MCP in phases, parallel within a phase", "plan, denied set", "results, timeline, DOP/DOS metrics"],
    ["DiffAnalyst", "Files, size bucket, DIF001 for very large PRs", "get_diff result", "diff summary, findings"],
    ["TestImpactAgent", "TST001 failing tests (base re-run separates pre-existing), TST002 removed tests, TST003 untested functions", "run_tests, find_usages", "test report, findings"],
    ["SecurityReviewer", "SEC001 secrets, SEC002 dangerous calls, SEC004 SQL, SEC003 reviewer-directed text", "scan results, guard", "findings"],
    ["QualityReviewer", "QLT001 to QLT007 on added lines", "lint, compare_versions", "findings"],
    ["Validator", "Tool status and output schema; finding must cite a known rule, a changed file and an added line", "results, findings", "validated findings, dropped, gaps"],
    ["VerdictAgent", "Severity policy to verdict, score, confidence; never approves without test evidence", "validated findings", "verdict"],
    ["HumanApproval", "interrupt(): approve, reject, override, dismiss, approve auto-fix rules", "verdict, findings", "human decision, final findings"],
    ["FixAgent", "Fix branch, re-run tests, push or roll back (HIGH risk)", "approved rules", "fix result"],
    ["Reporter", "post_review and developer_automation_report.json", "everything above", "report, posted review"]], [1.1, 3.5, 1.4, 1.6], size=8)

h2("4.2 Important source code")
para("Excerpts are copied from the repository; the file name precedes each excerpt.", size=9.5)
doc.add_section(WD_SECTION.CONTINUOUS); setup(doc.sections[-1], 0.3, 2)


def src(file, *ranges):
    lines = (ROOT / file).read_text(encoding="utf-8").splitlines()
    out = []
    for i, (a, b) in enumerate(ranges):
        if i: out.append("    ...")
        out += lines[a - 1:b]
    return out


def code(file, title, *ranges):
    para(f"{file} – {title}", bold=True, size=8, align=WD_ALIGN_PARAGRAPH.LEFT, after=1, before=3, keep=True)
    for ln in src(file, *ranges):
        q = doc.add_paragraph(); q.alignment = WD_ALIGN_PARAGRAPH.LEFT
        q.paragraph_format.space_after = Pt(0); q.paragraph_format.line_spacing = 1.0
        run(q, re.sub(r"^ +", lambda m: " " * len(m.group()), ln), size=8)


code("reviewpilot/servers/repo_server.py", "FastMCP server and a LOW-risk tool", (19, 19), (136, 144))
code("reviewpilot/servers/common.py", "annotations and tagged errors", (22, 24), (31, 33))
code("reviewpilot/servers/automation_server.py", "a HIGH-risk tool", (75, 78))
code("reviewpilot/mcp_client.py", "classification and schema validation", (33, 34), (79, 89), (260, 269))
code("reviewpilot/mcp_client.py", "call path: validate, call, classify, retry", (323, 328), (332, 339), (351, 364))
code("reviewpilot/planning.py", "dependency-aware scheduling", (80, 100))
code("reviewpilot/executor.py", "condition and phase execution", (73, 88), (141, 146))
code("reviewpilot/agents/review.py", "Supervisor", (101, 120))
code("reviewpilot/agents/review.py", "PolicyGuard decision", (124, 146))
code("reviewpilot/agents/review.py", "Validator (finding checks)", (362, 378))
code("reviewpilot/graph.py", "graph wiring", (91, 103))

doc.add_section(WD_SECTION.CONTINUOUS); setup(doc.sections[-1], 0.3, 1)

# ================================================================================================ 5 observations
h1("5. Observations and Results")
para("**Data source and reconstruction notice.** The benchmark folder of the repository (reviewpilot/benchmark, git-ignored) was missing, so it was rebuilt for this report: three sample projects (calculator from the lab manual, inventory, textkit) with passing tests, and 62 labelled pull requests (15 calculator, 24 inventory, 23 textkit) whose labels were written by hand from the intent of each change (reviewpilot/benchmark/repos.py, catalog.py, build.py; python -m reviewpilot.benchmark.build). Every figure below comes from running the system on this reconstruction: python -m pytest (34 tests, all passed), python -m reviewpilot.cli eval (storage/reports/run_20261022_125905, 248 s) and the running application. Numbers obtained with another benchmark would differ.", size=9.5)
h2("5.1 Tool-call trace (mcp_traces.jsonl)")
para("One complete review: PR CALC-01 (“Add power() with tests”), clean, final verdict APPROVE, run from the command line with an automatic approval. The executor ran 9 calls in 4 phases (DOP 6, DOS 4) and skipped one task (no base re-run was needed); the Reporter then called post_review. Each call is one JSON record in the trace file.", size=9.5)
caption("Table", "Tool-call trace of TRACE-C2F9EF2C (review of CALC-01)")
rows = [[x["trace_id"], x["task_id"], x["tool"], x["server"], x["phase"], x["risk"], x["status"], f'{x["execution_time_ms"]:.1f}'] for x in TRACES if x["trace_id"] == "TRACE-C2F9EF2C"]
table(["Trace ID", "Task ID", "Tool", "Server", "Phase", "Risk", "Status", "Time (ms)"], rows, [1.35, 0.85, 1.4, 0.8, 0.55, 0.7, 0.75, 0.8], center=(1, 3, 4, 5, 6, 7))
para("Tasks 002 to 007 share phase 1 and start within 7 ms of each other (39.9 to 46.1 ms after the first call), while find_usages, the affected tests and the full suite start only after their dependencies have finished. The two run_tests calls (1.12 s and 1.19 s) dominate the 2.41 s wall time, so the busy time (2.60 s) exceeds the wall time only by a factor of 1.08. post_review (phase 99) ran last. The files written by the system are listed below.", size=9.5)
caption("Table", "Artifacts generated in storage/mcp_logs/ and storage/reports/")
table(["File", "Purpose"], [["execution.log", "One line per agent step and per tool call (trace id, task id, status, time)"],
    ["mcp_traces.jsonl", "One JSON record per tool call: trace_id, task_id, tool, server, arguments, status, execution_time_ms, timestamp, error, error_category, attempt, phase, agent, risk"],
    ["developer_automation_report.json, runs/TRACE-*.json", "Full report of the last review and of every persisted review (plan, supervisor checks, policy, execution, findings, human decision)"],
    ["server_repo.log, server_analysis.log, server_quality.log, server_automation.log", "stderr of each MCP server process"],
    ["reports/run_*/ (report.json, one CSV per suite), latest_report.json", "Evaluation output"]], [3.0, 4.6], size=8.5)
para("The manual lists experiment_06_complete_report.json and experiment_06_execution_summary.json. These two files are not produced by this implementation; their content is covered by developer_automation_report.json and reports/latest_report.json.", size=9.5)

h2("5.2 Results per test suite")
b, wb, sec, fl, hi = S["benchmark"], S["workbench"], S["security"], S["failure"], S["hitl"]
caption("Table", "Results of the five evaluation suites")
table(["Suite", "Size", "Result"], [
    ["Review benchmark (labelled PRs, offline samples)", "62 PRs", f"verdict accuracy {b['verdict_accuracy_pct']}%; finding precision {b['finding_precision_pct']}%, recall {b['finding_recall_pct']}%, F1 {b['finding_f1_pct']}% (TP {b['tp']}, FP {b['fp']}, FN {b['fn']}); false alarms on clean PRs {b['clean_pr_false_alarm_pct']}%; BLOCK recall {b['block_recall_pct']}%; unsafe merges {b['unsafe_merge_rate_pct']}%; failing tests caught {b['failing_tests_caught_pct']}%; tool results valid {b['tool_validity_pct']}%; average {b['avg_latency_s']} s (p95 {b['p95_latency_s']} s), {b['avg_tool_calls']} tool calls, average DOP {b['avg_dop']}"],
    ["Developer Workbench (natural-language requests)", "32 requests", f"plan accuracy {wb['plan_accuracy_pct']}%; tool precision {wb['tool_precision_pct']}%, recall {wb['tool_recall_pct']}%, F1 {wb['tool_selection_f1_pct']}%; outcome accuracy {wb['outcome_accuracy_pct']}%; {wb['avg_latency_s']} s per request"],
    ["Security tests", "26 tests", f"{sec['passed']} passed, 1 failed ({sec['pass_rate_pct']}%)"],
    ["Failure-injection scenarios", "16 scenarios", f"{fl['passed']} passed ({fl['pass_rate_pct']}%)"],
    ["Human-in-the-loop scenarios", "12 scenarios", f"{hi['passed']} passed ({hi['pass_rate_pct']}%)"],
    ["Automated tests (pytest, tests/)", "34 tests", "34 passed (unit, MCP protocol, workflows, GitHub end-to-end against a fake GitHub API)"],
    ["Quality index (weighted mean of the above)", "-", f"{R['headline']['quality_index']}"]], [2.4, 0.9, 4.3], size=8.5)
para("Verdict confusion matrix of the benchmark (label to ReviewPilot verdict): APPROVE 23 correct and 2 given COMMENT; COMMENT 5 correct and 1 given REQUEST_CHANGES; REQUEST_CHANGES 26 of 26; BLOCK 5 of 5. Accuracy by category is 100% for every category except prompt injection (75%) and untested behaviour change (0 of 2); see Section 7.", size=9.5)
caption("Table", "Failure-injection scenarios (FL-01 to FL-16, all passed): behaviour per failure type")
table(["Failure type", "Scenario and observed behaviour"], [
    ["Execution error", "Transient lint failure: retried once, succeeds (attempts 2). Persistent failure: stops after 3 attempts. Server process crash mid-call and during post_review: client reconnects and retries (attempts 2)"],
    ["Timeout", "Tool hangs past its timeout: TIMEOUT, retried, second attempt succeeds. Diff tool keeps timing out: limited retries, review completes with verdict COMMENT and the gap listed"],
    ["Tool / dependency error", "Tool error: stop and report, attempts 1. Scan unavailable: review completes and reports the gap. Tests cannot run on a clean PR: APPROVE is downgraded to COMMENT. All compare_versions fail: find_usages skipped, full suite still runs"],
    ["Input / security error", "Missing file, missing argument (attempts 0), path outside project, invalid pytest timeout: stopped with INPUT or SECURITY, no retry. Naive policy retries a security error 5 times, which shows why classification matters"]], [1.4, 6.2], size=8.5)

h2("5.3 Optimisation techniques")
caption("Table", "Optimisation techniques of the manual (nine), measured with the basic and the optimised approach")
rows = []
for o in T["optimization"]:
    imp = "-" if o["improvement_pct"] is None else f"{o['improvement_pct']}%"
    rows.append([o["technique"], o["metric"], f"{o['basic_approach']}: {o['basic_value']:g}", f"{o['optimised_approach']}: {o['optimised_value']:g}", imp])
table(["Technique", "Metric", "Basic: value", "Optimised: value", "Change"], rows, [1.15, 1.7, 1.9, 2.1, 0.6], size=7.5, center=(4,))
para("Reading the table: parallel execution and dependency-aware scheduling reduce wall time and avoid work (the full suite is skipped when the affected tests already fail: 9 to 6 pytest runs on three failing PRs); client-side schema validation removes five server round trips for five malformed calls; controlled retry spends 5 calls instead of 17 on four failure scenarios; a 1 s timeout returns in about 1.1 s from a tool that hangs for 3 s; path security, structured logging, persistent tracing and monitoring are measured as counts of what is captured or stopped (for example all six traversal attempts stopped before reaching a server). Persistent sessions, an extra technique beyond the manual, cut four calls from 3510 ms to 99 ms.", size=9.5)

h2("5.4 Student variation 9: performance comparison (sequential vs parallel)")
para("The same four independent tool calls (list_files, read_file, search_code, analyze_python on the calculator project) were run through the MCP client sequentially and in parallel; the experiment was repeated five times. The code is run_performance and VAR9_CALLS in evaluation/suites.py.", size=9.5)
caption("Table", "Sequential vs parallel execution time (4 tool calls, 5 runs)")
seq, par = V9["seq"], V9["par"]
rows = [[i + 1, f"{seq[i]:.1f}", f"{par[i]:.1f}"] for i in range(5)] + [["Mean", f"{V9['mean_seq']:.1f}", f"{V9['mean_par']:.1f}"]]
table(["Run", "Sequential time (ms)", "Parallel time (ms)"], rows, [1.2, 2.2, 2.2], center=(0, 1, 2))
image("images/d_var9.png", 4.4, "Comparison of sequential and parallel execution time over five runs")
caption("Table", "Performance comparison summary")
sp = S["performance"]
table(["Metric", "Value"], [["Number of tasks", "4"], ["Number of runs", "5"], ["Average sequential time", f"{V9['mean_seq']:.1f} ms"],
                            ["Average parallel time", f"{V9['mean_par']:.1f} ms (standard deviation {sp['var9_parallel_std']} ms vs {sp['var9_sequential_std']} ms sequential)"],
                            ["Average time per tool (sequential)", f"{V9['mean_seq'] / 4:.1f} ms"], ["Speed-up (sequential / parallel)", f"{V9['mean_seq'] / V9['mean_par']:.2f}x"],
                            ["Time reduction", f"{(V9['mean_seq'] - V9['mean_par']) / V9['mean_seq'] * 100:.1f} %"]], [3.0, 4.6])
para("Observation: parallel execution was faster in all five runs and by about 20 % on average (1.24x), but the absolute saving is only about 14 ms, and the spread of the five runs is similar for both modes. The calls take about 18 ms each, so scheduling, the thread hand-off inside the server and a single stdio connection per server are comparable to the work itself. When the tool time is large the benefit is clear: four independent pytest runs took 4613.8 ms sequentially and 1652.3 ms in parallel (2.79x), while the review workflow, where most of the time is the two pytest calls that depend on earlier phases, improved by only 1.03x.", size=9.5)

h2("5.5 DOP and DOS")
caption("Table", "DOP and DOS of the measured workloads")
table(["Workload", "DOP", "DOS", "Execution type"], [
    ["Variation 9, parallel (4 independent calls)", "4", "1 phase", "Fully parallel"], ["Variation 9, sequential", "1", "4 steps", "Sequential"],
    ["Review phase 1 (get_diff, compare_versions per file, scan_secrets, scan_security, lint)", "5 to 6 (sampled PRs)", "-", "Parallel"],
    ["Review phases 2 to 4 (find_usages, affected tests, full suite, base re-run)", "1 each", "2 to 3 phases", "Sequential, dependency-aware"],
    ["Review workflow overall (8 sampled PRs, parallel mode)", f"average {sp['review_avg_dop']}", f"average {sp['review_avg_dos']} phases", "Hybrid (parallel + sequential)"]], [3.6, 1.4, 1.2, 1.4], size=8.5)
caption("Table", "Review workflow, sequential vs parallel executor (8 sampled PRs)")
rv = {}
for x in T["performance"]:
    if x["experiment"] == "Review workflow": rv.setdefault(x["run"], {})[x["mode"]] = x
rows = [[k, v["parallel"]["calls"], int(v["parallel"]["dop"]), int(v["parallel"]["dos"]), f'{v["sequential"]["wall_ms"]:.0f}', f'{v["parallel"]["wall_ms"]:.0f}', f'{v["sequential"]["wall_ms"] / v["parallel"]["wall_ms"]:.2f}x'] for k, v in rv.items()]
table(["PR", "Calls", "DOP", "DOS", "Sequential (ms)", "Parallel (ms)", "Speed-up"], rows, [0.9, 0.8, 0.7, 0.7, 1.4, 1.4, 1.0], center=tuple(range(7)), size=8.5)
para("A higher DOP only pays off when the parallel calls are long enough and the machine has the capacity; the semaphore (max_parallel = 8) bounds server load, and in this workload the longest tasks (pytest) sit in later, dependent phases, so the overall gain stays small.", size=9.5)

# ================================================================================================ 6 screenshots
h1("6. Screenshots of the Running Application")
para("Taken with Playwright against streamlit run Home.py on the offline sample repositories (the rebuilt benchmark). The pull request shown is CALC-02, which removes the zero check from divide().", size=9.5)
pair([("images/s_pulls.png", "Pull requests page after a review: verdict banner, finding and the Changes tab"), ("images/s_tests.png", "Tests tab: affected tests fail on the PR, the same tests pass on the base, full suite skipped")])
pair([("images/s_how.png", "How it ran tab: phases, parallel calls (DOP 5) and timeline of the MCP calls"), ("images/s_ask.png", "Ask page (Developer Workbench): “Analyze calculator.py and run its tests”")])
pair([("images/s_code.png", "Code page: files of the sample project and its test run"), ("images/s_batch.png", "Batch testing: the 62-PR benchmark, verdicts and disagreements with the label")])
pair([("images/s_overview.png", "Overview page (How it works): MCP servers with their tools, the review stages and the review graph"), ("images/s_report.png", "Evaluation report: scorecard, verdict confusion matrix and findings by rule")])
pair([("images/s_inspector_tools.png", "MCP inspector: sessions (initialize result per server) and tools/list with risk, hints and schemas"), ("images/s_inspector_call.png", "MCP inspector: tools/call get_diff for INV-09, structured result, output schema valid, TRACE and TASK id")])
pair([("images/s_security.png", "Security tests: a path traversal request is denied by the PolicyGuard before any MCP call"), ("images/s_traces.png", "Traces and logs: mcp_traces.jsonl records and median time per tool")])

# ================================================================================================ 7 discussion
h1("7. Discussion")
para("**What worked.** All four MCP servers connect over stdio, negotiate capabilities and expose 18 tools whose input and output schemas are used by the client; 100% of tool results in the benchmark were valid against their output schema. The review benchmark gave no false positive (precision 100%), no false alarm on clean PRs, caught every PR that must be blocked (5 of 5) and every PR with failing tests, and approved no PR that should not merge. Dependency-aware scheduling avoided running the full suite after failing affected tests, controlled retry recovered from transient failures and a crashed server (16 of 16 failure scenarios), and the human gate behaved as designed in all 12 scenarios (pause, approve, reject, override, dismiss, approved fix, rollback, and no fix without a person).")
para("**Known limits.** The report lists these itself. (1) INV-15 and TXT-12 are untested behaviour changes to functions that already have tests; the review approves them (expected COMMENT) because TST003 only checks that a changed function is referenced by some test, not whether the tests cover the new behaviour. (2) TXT-16 is a pull request whose description politely asks the reviewer to approve without any trigger phrase; SEC003 is not raised and the verdict is COMMENT instead of REQUEST_CHANGES. (3) In the workbench, two phrasings without a file name or the word “usages” are not understood (WB-28 “What does the pricing module do?”, WB-32 “Is divide covered by tests?”), which is why plan accuracy is 93.8%. The planner is rule based, so it asks the user to rephrase rather than guess.")
para("**The failing security test.** Of 26 security tests, 25 pass and SEC-23 fails (96.2%): a paraphrased request to approve, with none of the trigger words, is not flagged as SEC003 and the verdict is COMMENT. Detection of reviewer-directed text is pattern based, so a sufficiently rephrased sentence passes the screen. The damage is contained, not removed: PR text is never executed as an instruction, no write tool runs in an automatic review, and approving or posting needs a human in manual mode, but the injection attempt is not reported to the reviewer. The other 25 tests (path traversal at the policy layer and, with the policy switched off, at the server; shell metacharacters in test targets; HIGH-risk tools without a human; destructive and shell requests; unknown tools and wrong argument types; role-play and override injection; secret masking) pass.")
para("**Performance.** Parallel execution of very short tool calls gave a small gain (1.24x, about 14 ms), in line with the manual’s own observation that parallelism helps little for short tasks; the larger gains come from running independent long tools in parallel (2.79x for four pytest runs), from skipping work through dependency-aware scheduling (one third fewer pytest runs on failing PRs) and from keeping MCP sessions open (about 35x for four calls). **Implementation notes.** The manual’s five tools are a subset of the 18 implemented; the README states 17 tools in two places while the servers register 18, and the Inspector lists 18. Two manual artifact files (experiment_06_*) are not generated, as stated in Section 5.1.")

# ================================================================================================ 8 conclusion
h1("8. Conclusion")
para("The experiment implemented a developer automation system on the Model Context Protocol: four FastMCP servers with 18 typed, risk-labelled tools, a ClientSession-based client that discovers tools, validates arguments and results, classifies failures and retries only transient ones, and a LangGraph workflow of fourteen agents with a planner, supervisor, policy guard, validator and human approval. On the evaluation of the rebuilt benchmark it reached a quality index of 97.1, with no unsafe merge, 100% on the failure-handling and human-in-the-loop scenarios and 96.2% on the security tests, and the measurements show that parallel execution helps most for long-running tools and little for very short ones. The known gaps, paraphrased instructions and untested behaviour changes, are left visible instead of being tuned away.")
h1("References")
bullets(["Model Context Protocol, specification (lifecycle, transports, tools, resources, prompts), https://modelcontextprotocol.io/specification",
         "Model Context Protocol Python SDK (FastMCP, ClientSession, stdio_client), https://github.com/modelcontextprotocol/python-sdk",
         "LangGraph documentation (StateGraph, parallel branches, interrupts and human-in-the-loop), https://langchain-ai.github.io/langgraph/",
         "Lab Manual, Experiment 6: Developer Automation System using MCP Protocol (Developer Tools Domain), VESIT."], size=9.5)
h1("Inferences")
for _ in range(5):
    para("_" * 120, size=9, align=WD_ALIGN_PARAGRAPH.LEFT, after=9, before=4)

doc.save("Experiment_6.docx")
print("saved; figures", FIG[0], "tables", TAB[0])
