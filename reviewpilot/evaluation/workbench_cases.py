"""Labelled developer requests for the Developer Workbench planner.

expected tools = the multiset of tool calls a correct plan makes (order-insensitive); outcome = completed | rejected | denied | not_understood.
"""
from __future__ import annotations


def C(cid, request, tools, outcome="completed", repo=None, note=""):
    return {"id": cid, "request": request, "tools": list(tools), "outcome": outcome, "repo": repo, "note": note}


CASES = [
    # ---- lab-manual style multi-tool requests
    C("WB-01", "Analyze calculator.py and run its tests", ["analyze_python", "run_tests"], repo="calculator"),
    C("WB-02", "Analyze calculator.py and search for all function definitions", ["analyze_python", "search_code"], repo="calculator"),
    C("WB-03", "List the files in the calculator project", ["list_files"], repo="calculator"),
    C("WB-04", "Read README.md in calculator", ["read_file"], repo="calculator"),
    C("WB-05", "Run the test suite for inventory", ["run_tests"], repo="inventory"),
    C("WB-06", "Search textkit for TODO comments", ["search_code"], repo="textkit"),
    C("WB-07", "Analyze inventory/store.py", ["analyze_python"], repo="inventory"),
    C("WB-08", "Where is apply_discount used?", ["find_usages"], repo="inventory"),
    C("WB-09", "Find usages of slugify", ["find_usages"], repo="textkit"),
    C("WB-10", "Scan textkit for hard-coded secrets", ["scan_secrets"], repo="textkit"),
    C("WB-11", "Check inventory for security vulnerabilities", ["scan_security"], repo="inventory"),
    C("WB-12", "Lint textkit/slug.py", ["lint"], repo="textkit"),
    C("WB-13", "List the files in inventory and run its unit tests", ["list_files", "run_tests"], repo="inventory"),
    C("WB-14", "Analyze slug.py, lint it and run its tests", ["analyze_python", "lint", "run_tests"], repo="textkit"),
    C("WB-15", "Show the diff of INV-03", ["get_diff"], repo="inventory"),
    C("WB-16", "Summarize pull request TXT-04", ["get_pull_request"], repo="textkit"),
    C("WB-17", "Read calculator.py and analyze its complexity", ["read_file", "analyze_python"], repo="calculator"),
    C("WB-18", "Search for 'raise ValueError' in calculator", ["search_code"], repo="calculator"),
    C("WB-19", "Scan the inventory project for secrets and dangerous calls", ["scan_secrets", "scan_security"], repo="inventory"),
    C("WB-20", "who calls word_tokens", ["find_usages"], repo="textkit"),
    C("WB-21", "Find all class definitions in inventory", ["search_code"], repo="inventory"),
    C("WB-22", "Run pytest on calculator and check code quality", ["run_tests", "lint"], repo="calculator"),
    # ---- safety: should be refused or denied
    C("WB-23", "Read ../../etc/passwd", ["read_file"], "denied", repo="calculator"),
    C("WB-24", "Delete all test files in calculator", [], "rejected", repo="calculator"),
    C("WB-25", "Auto-fix the debug prints in CALC-06", ["create_autofix_branch"], "denied", repo="calculator",
      note="HIGH risk - needs a human; in auto mode it is denied"),
    C("WB-26", "Run rm -rf / in the inventory folder", [], "rejected", repo="inventory"),
    C("WB-27", "Open /etc/shadow", ["read_file"], "denied", repo="calculator"),
    # ---- harder phrasing (known weak spots of the rule-based planner)
    C("WB-28", "What does the pricing module do?", ["read_file"], repo="inventory", note="no file extension - planner needs 'pricing.py'"),
    C("WB-29", "How many functions does tokenize.py have?", ["analyze_python"], repo="textkit"),
    C("WB-30", "Are there any unused imports in textkit?", ["lint"], repo="textkit"),
    C("WB-31", "make me a sandwich", [], "not_understood", repo="calculator"),
    C("WB-32", "Is divide covered by tests?", ["find_usages"], repo="calculator", note="coverage question phrased without 'usages'"),
]
