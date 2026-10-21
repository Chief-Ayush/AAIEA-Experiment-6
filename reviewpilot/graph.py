"""LangGraph workflows.

Review graph
    intake_guard -> planner -> supervisor -> policy_guard -> executor
        -> diff_analyst | test_impact | security_reviewer | quality_reviewer   (fan-out, run in the same super-step)
        -> validator -> verdict -> human_approval -> [fix_agent] -> reporter
Workbench graph
    request_guard -> task_planner -> wb_supervisor -> wb_policy -> wb_executor -> wb_validator -> summarizer
"""
from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents import review as R
from .agents import workbench as W


class ReviewState(TypedDict, total=False):
    trace_id: str
    pr_id: str
    settings: dict
    status: str
    pr: dict
    guard: dict
    plan: list
    plan_ctx: dict
    supervisor: dict
    policy: dict
    results: dict
    timeline: list
    exec_metrics: dict
    diff_summary: dict
    test_report: dict
    findings: Annotated[list, operator.add]
    validated: list
    validation: dict
    verdict: dict
    human: dict
    final_findings: list
    fix: dict
    posted: Any
    report_path: str
    steps: Annotated[list, operator.add]


class WorkbenchState(TypedDict, total=False):
    trace_id: str
    request: str
    default_repo: str
    repo: str
    settings: dict
    status: str
    guard: dict
    plan: list
    plan_info: dict
    supervisor: dict
    policy: dict
    results: dict
    timeline: list
    exec_metrics: dict
    validation: dict
    answer: str
    steps: Annotated[list, operator.add]


REVIEWERS = ["diff_analyst", "test_impact", "security_reviewer", "quality_reviewer"]


def build_review_graph(checkpointer=None):
    g = StateGraph(ReviewState)
    g.add_node("intake_guard", R.intake_guard)
    g.add_node("planner", R.planner)
    g.add_node("supervisor", R.supervisor)
    g.add_node("policy_guard", R.policy_guard)
    g.add_node("executor", R.executor)
    g.add_node("diff_analyst", R.diff_analyst)
    g.add_node("test_impact", R.test_impact)
    g.add_node("security_reviewer", R.security_reviewer)
    g.add_node("quality_reviewer", R.quality_reviewer)
    g.add_node("validator", R.validator)
    g.add_node("verdict", R.verdict_agent)
    g.add_node("human_approval", R.human_approval)
    g.add_node("fix_agent", R.fix_agent)
    g.add_node("reporter", R.reporter)
    g.add_edge(START, "intake_guard")
    g.add_conditional_edges("intake_guard", R.route_after_intake, {"planner": "planner", "reporter": "reporter"})
    g.add_edge("planner", "supervisor")
    g.add_edge("supervisor", "policy_guard")
    g.add_edge("policy_guard", "executor")
    for n in REVIEWERS:
        g.add_edge("executor", n)
    g.add_edge(REVIEWERS, "validator")
    g.add_edge("validator", "verdict")
    g.add_edge("verdict", "human_approval")
    g.add_conditional_edges("human_approval", R.route_after_human, {"fix_agent": "fix_agent", "reporter": "reporter"})
    g.add_edge("fix_agent", "reporter")
    g.add_edge("reporter", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def build_workbench_graph(checkpointer=None):
    g = StateGraph(WorkbenchState)
    g.add_node("request_guard", W.request_guard)
    g.add_node("task_planner", W.task_planner)
    g.add_node("wb_supervisor", W.wb_supervisor)
    g.add_node("wb_policy", W.wb_policy)
    g.add_node("wb_executor", W.wb_executor)
    g.add_node("wb_validator", W.wb_validator)
    g.add_node("summarizer", W.summarizer)
    g.add_edge(START, "request_guard")
    g.add_conditional_edges("request_guard", W.route_after_guard, {"task_planner": "task_planner", "summarizer": "summarizer"})
    g.add_conditional_edges("task_planner", W.route_after_plan, {"wb_supervisor": "wb_supervisor", "summarizer": "summarizer"})
    g.add_edge("wb_supervisor", "wb_policy")
    g.add_edge("wb_policy", "wb_executor")
    g.add_edge("wb_executor", "wb_validator")
    g.add_edge("wb_validator", "summarizer")
    g.add_edge("summarizer", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def mermaid(kind: str = "review") -> str:
    return (build_review_graph() if kind == "review" else build_workbench_graph()).get_graph().draw_mermaid()


GROUP_COLOR = {"Planning": "#2a78d6", "Control": "#eda100", "Execution": "#1baf7a", "Review": "#eb6834", "Validation": "#6250d6"}
NODE_AGENT = {"intake_guard": "IntakeGuard", "planner": "Planner", "supervisor": "Supervisor", "policy_guard": "PolicyGuard", "executor": "Executor",
              "diff_analyst": "DiffAnalyst", "test_impact": "TestImpactAgent", "security_reviewer": "SecurityReviewer", "quality_reviewer": "QualityReviewer",
              "validator": "Validator", "verdict": "VerdictAgent", "human_approval": "HumanApproval", "fix_agent": "FixAgent", "reporter": "Reporter",
              "request_guard": "RequestGuard", "task_planner": "TaskPlanner", "wb_supervisor": "Supervisor", "wb_policy": "PolicyGuard",
              "wb_executor": "Executor", "wb_validator": "ResultValidator", "summarizer": "Summarizer"}


def graph_dot(kind: str = "review") -> str:
    """Graphviz rendering of the compiled LangGraph (nodes and edges are read from the graph itself)."""
    from .agent_catalog import AGENT_GROUP
    g = (build_review_graph() if kind == "review" else build_workbench_graph()).get_graph()
    lines = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.32;',
             'node [shape=box, style="rounded,filled", fillcolor="#ffffff", color="#c9cdd3", fontname="Helvetica", fontsize=10, height=0.32];',
             'edge [color="#8a909a", arrowsize=0.6, fontname="Helvetica", fontsize=8, fontcolor="#5d636e"];']
    for nid in g.nodes:
        if nid in ("__start__", "__end__"):
            lines.append(f'"{nid}" [label="{"start" if nid == "__start__" else "end"}", shape=circle, width=0.35, fixedsize=true, fillcolor="#f4f5f7", fontsize=8];')
            continue
        agent = NODE_AGENT.get(nid, nid)
        col = GROUP_COLOR.get(AGENT_GROUP.get(agent, "Execution"), "#8a909a")
        extra = ', penwidth=1.6, color="#eda100"' if agent == "HumanApproval" else ""
        lines.append(f'"{nid}" [label="{agent}", fillcolor="{col}18", color="{col}"{extra}];')
    if kind == "review":
        lines.append('subgraph cluster_r { label="parallel reviewers"; fontsize=9; fontcolor="#5d636e"; style="dashed"; color="#b9c6d8"; '
                     '"diff_analyst"; "test_impact"; "security_reviewer"; "quality_reviewer"; }')
    for e in g.edges:
        style = ', style="dashed"' if e.conditional else ""
        lines.append(f'"{e.source}" -> "{e.target}" [{style.lstrip(", ")}];')
    lines.append("}")
    return "\n".join(lines)
