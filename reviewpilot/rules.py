"""Review rules: what each finding means, its severity, which agent owns it and whether it can be fixed automatically."""
from __future__ import annotations

from .config import SEVERITY_ORDER

RULES = {
    # rule: (severity, title, agent, auto-fixable)
    "SEC001": ("critical", "Hard-coded secret", "SecurityReviewer", True),
    "SEC002": ("high", "Dangerous call (code execution, shell or unsafe deserialisation)", "SecurityReviewer", False),
    "SEC003": ("high", "Instruction aimed at the reviewer (prompt injection)", "SecurityReviewer", False),
    "SEC004": ("high", "SQL built by string formatting", "SecurityReviewer", False),
    "TST001": ("high", "Tests fail on the pull request", "TestImpactAgent", False),
    "TST002": ("high", "Tests removed or skipped", "TestImpactAgent", False),
    "TST003": ("medium", "New or changed public function without tests", "TestImpactAgent", False),
    "QLT001": ("low", "Debug output left in code", "QualityReviewer", True),
    "QLT002": ("medium", "Exception swallowed by a bare or broad except", "QualityReviewer", True),
    "QLT003": ("medium", "Function too complex", "QualityReviewer", False),
    "QLT004": ("low", "Unused import introduced", "QualityReviewer", True),
    "QLT005": ("low", "Public function without docstring", "QualityReviewer", False),
    "QLT006": ("info", "TODO / FIXME added", "QualityReviewer", False),
    "QLT007": ("medium", "Mutable default argument", "QualityReviewer", False),
    "DIF001": ("medium", "Large pull request", "DiffAnalyst", False),
}

SEVERITY_WEIGHT = {"critical": 60, "high": 25, "medium": 10, "low": 3, "info": 0}
VERDICT_FOR = {"critical": "BLOCK", "high": "REQUEST_CHANGES", "medium": "COMMENT", "low": "APPROVE", "info": "APPROVE", None: "APPROVE"}


def sev_rank(sev: str | None) -> int:
    return SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else -1


def make_finding(rule: str, path: str, line: int | None, message: str, evidence: str = "", agent: str | None = None) -> dict:
    sev, title, owner, fixable = RULES[rule]
    return {"rule": rule, "severity": sev, "title": title, "path": path, "line": line, "message": message, "evidence": evidence[:240],
            "agent": agent or owner, "autofix": fixable and not (rule == "SEC001" and "private_key" in evidence)}


def verdict_for(findings: list[dict]) -> tuple[str, int, str | None]:
    """Verdict, merge-readiness score (0-100) and the highest severity."""
    top = max((f["severity"] for f in findings), key=sev_rank, default=None)
    score = max(0, 100 - sum(SEVERITY_WEIGHT[f["severity"]] for f in findings))
    return VERDICT_FOR[top], score, top
