import json, statistics as st
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
plt.xkcd(scale=0.7, length=80, randomness=1.5)
plt.rcParams["font.family"] = "Ink Free"; plt.rcParams["axes.unicode_minus"] = False
COL = {"p": "#cfe0fb", "c": "#fde9b0", "e": "#c8efdc", "r": "#fbd3c2", "v": "#ddd6fa", "g": "#eeeeee"}
def box(ax, x, y, w, h, t, k="g", fs=11, lw=1.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=COL[k], ec="#222", lw=lw))
    ax.text(x + w/2, y + h/2, t, ha="center", va="center", fontsize=fs, linespacing=1.15)
def arr(ax, a, b, rad=0.0, ls="-"):
    ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="-|>", lw=1.4, color="#222", ls=ls, connectionstyle=f"arc3,rad={rad}"))
def fig(w, h):
    f, ax = plt.subplots(figsize=(w, h)); ax.set_xlim(0, w); ax.set_ylim(0, h); ax.axis("off"); return f, ax
def save(f, n): f.savefig(f"images/{n}.png", dpi=220, bbox_inches="tight", facecolor="white"); plt.close(f)

# 1 architecture
f, ax = fig(10, 6.6)
box(ax, 3.2, 5.8, 3.6, 0.7, "Streamlit UI  (11 pages) / CLI", "g")
box(ax, 0.5, 3.9, 9, 1.5, "", "v"); ax.text(0.7, 5.15, "LangGraph agents (review graph + workbench graph)", fontsize=11, va="center")
for i, t in enumerate(["IntakeGuard\nPlanner", "Supervisor\nPolicyGuard", "Executor", "4 reviewers\n(parallel)", "Validator\nVerdict", "Human\nFix, Report"]):
    box(ax, 0.7 + i*1.45, 4.05, 1.3, 0.85, t, ["p", "c", "e", "r", "v", "c"][i], fs=9)
box(ax, 2.2, 2.7, 5.6, 0.8, "MCP client (MCPHub): ClientSession per server\nschema check, timeout, classify, retry, reconnect", "e", fs=10)
ax.text(8.0, 3.1, "stdio\nJSON-RPC", fontsize=10, color="#444")
for i, (n, t) in enumerate([("repo", "6 tools\nLOW"), ("analysis", "3 tools\nLOW"), ("quality", "4 tools\nLOW/MED"), ("automation", "5 tools\nHIGH/MED/LOW")]):
    box(ax, 0.5 + i*2.35, 1.1, 2.1, 1.0, f"{n} server\n{t}", "c", fs=10)
    arr(ax, (3.0 + i*0.65, 2.7), (1.55 + i*2.35, 2.1))
box(ax, 1.0, 0.0, 3.6, 0.6, "Sample repos / PRs (data/)", "g", fs=10)
box(ax, 5.4, 0.0, 3.6, 0.6, "GitHub REST API (optional)", "g", fs=10)
arr(ax, (1.5, 1.1), (2.0, 0.6)); arr(ax, (8.5, 1.1), (7.5, 0.6)); arr(ax, (4.8, 1.1), (3.8, 0.6))
arr(ax, (5, 5.8), (5, 5.4)); arr(ax, (5, 3.9), (5, 3.5))
save(f, "d_architecture")

# 2 workflow
f, ax = fig(10, 6.5)
names = [("IntakeGuard", "c"), ("Planner", "p"), ("Supervisor", "p"), ("PolicyGuard", "c"), ("Executor", "e")]
x0 = 0.2
for i, (n, k) in enumerate(names):
    box(ax, x0 + i*1.95, 5.3, 1.7, 0.6, n, k, fs=11)
    if i: arr(ax, (x0 + i*1.95 - 0.25, 5.6), (x0 + i*1.95, 5.6))
rv = ["DiffAnalyst", "TestImpact\nAgent", "Security\nReviewer", "Quality\nReviewer"]
for i, n in enumerate(rv):
    box(ax, 0.9 + i*2.2, 3.7, 1.9, 0.85, n, "r", fs=10)
    arr(ax, (8.7, 5.3), (1.85 + i*2.2, 4.55), rad=0.0)
    arr(ax, (1.85 + i*2.2, 3.7), (5.0, 3.05))
ax.text(0.2, 4.25, "parallel\nfan-out", fontsize=10, color="#444")
box(ax, 4.0, 2.55, 2.0, 0.6, "Validator (join)", "v")
for a, b, t, k in [((5, 2.55), (5, 2.2), "VerdictAgent", "v"), ((5, 1.6), (5, 1.4), "HumanApproval  (interrupt)", "c")]:
    arr(ax, a, b)
box(ax, 4.0, 1.6, 2.0, 0.6, "VerdictAgent", "v"); box(ax, 3.6, 0.8, 2.8, 0.6, "HumanApproval (interrupt)", "c")
box(ax, 0.8, 0.05, 2.2, 0.6, "FixAgent  (HIGH risk)", "e"); box(ax, 7.0, 0.05, 2.2, 0.6, "Reporter", "e")
arr(ax, (4.3, 0.8), (3.0, 0.45)); arr(ax, (5.7, 0.8), (7.0, 0.45)); arr(ax, (3.0, 0.3), (7.0, 0.3), rad=0.0)
ax.text(0.9, 1.0, "approved\nauto-fix", fontsize=9, color="#444"); ax.text(6.6, 1.0, "reject / approve", fontsize=9, color="#444")
ax.text(0.2, 6.1, "invalid or unsafe PR: IntakeGuard sends the run straight to Reporter (status rejected)", fontsize=9, color="#a33")
save(f, "d_workflow")

# 3 executor phases
f, ax = fig(10, 4.6)
cols = [("Phase 1  (parallel)", 0.2, 3.4), ("Phase 2", 3.9, 1.7), ("Phase 3", 5.9, 1.8), ("Phase 4-5 (conditional)", 8.0, 1.9)]
for t, x, w in cols: ax.text(x + w/2, 4.3, t, ha="center", fontsize=11)
p1 = ["get_diff", "compare_versions xN", "scan_secrets", "scan_security", "lint"]
for i, t in enumerate(p1): box(ax, 0.3, 3.5 - i*0.7, 3.2, 0.55, t, "e", fs=10)
box(ax, 4.0, 2.4, 1.5, 0.8, "find_usages\n(changed funcs)", "e", fs=10)
box(ax, 6.0, 2.4, 1.6, 0.8, "run_tests\n(affected)", "c", fs=10)
box(ax, 8.1, 3.1, 1.7, 0.8, "run_tests\nfull suite", "c", fs=10)
box(ax, 8.1, 1.7, 1.7, 0.8, "run_tests on base\n(only if failed)", "c", fs=9)
arr(ax, (3.5, 2.3), (4.0, 2.8)); arr(ax, (5.5, 2.8), (6.0, 2.8)); arr(ax, (7.6, 2.8), (8.1, 3.4)); arr(ax, (7.6, 2.8), (8.1, 2.1))
ax.text(7.6, 1.2, "full suite runs only if affected tests pass", fontsize=9, color="#444", ha="center")
ax.text(5.0, 0.3, "DOP = widest phase (up to 6 calls); DOS = number of phases", fontsize=10, ha="center")
save(f, "d_phases")

# 4 variation 9 chart
r = json.load(open("../storage/reports/latest_report.json"))
rows = [x for x in r["tables"]["performance"] if x["experiment"].startswith("Variation 9")]
seq = [x["wall_ms"] for x in rows if x["mode"] == "sequential"]; par = [x["wall_ms"] for x in rows if x["mode"] == "parallel"]
f, ax = plt.subplots(figsize=(6.2, 3.2)); import numpy as np
i = np.arange(1, 6); ax.bar(i - 0.2, seq, 0.4, color="#555", ec="#222", label="Sequential"); ax.bar(i + 0.2, par, 0.4, color="#bbb", ec="#222", label="Parallel")
ax.set_xlabel("Run number"); ax.set_ylabel("Total time (ms)"); ax.legend(); ax.set_title("Sequential vs parallel: 4 MCP tool calls")
save(f, "d_var9")
json.dump({"seq": seq, "par": par, "mean_seq": round(st.mean(seq), 1), "mean_par": round(st.mean(par), 1)}, open("var9.json", "w"))
print(seq, par, st.mean(seq), st.mean(par), st.mean(seq)/st.mean(par))
