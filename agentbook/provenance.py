"""Number provenance: every number in an answer should come from a tool result.

`check_cited_ids` (guardrails.py) asks whether the model IDs in an answer
were ever retrieved. This module asks the same question of numbers:

    from agentbook.provenance import check_numbers
    report = check_numbers(result.text, result.messages)     # or tool outputs from a trace
    report.ok, report.unsupported

A number counts as supported if the same value appears in some tool result.
Values are compared numerically, so "25 keV" in the answer matches "25" in
a tool result, and "1e-10" matches "10^-10". The check is deliberately
dumb: it does not know units or what a number refers to, so a supported
value can still be misused (25 keV attached to the wrong parameter). What it
catches reliably is a number that appears in no tool result at all, which
is what a bound filled in from the model's memory looks like.

Not treated as numbers: model IDs, arXiv IDs, list markers at the start of
a line ("1."), digits glued to letters or underscores ("gap_1", "s2",
"2theta", "sin^2"), and exponents written as part of a symbol.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from .guardrails import MODEL_ID_RE, tool_outputs

ARXIV_RE = re.compile(r"(?<![\w.])(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+/\d{7})(?![\w.])")
LIST_MARKER_RE = re.compile(r"(?m)^(\s*)\d+[.)](?=\s)")
MINUS = "-−"  # ASCII hyphen and the Unicode minus sign
NUMBER_RE = re.compile(
    rf"(?<![\w.^/\\])(?:"
    rf"10\s*\^\s*\{{?\s*(?P<pow>[{MINUS}+]?\d+)\s*\}}?"                       # 10^-10, 10^{-10}
    rf"|(?P<sign>[{MINUS}])?(?P<mant>\d+(?:\.\d+)?)"                          # 1.2, -3
    rf"(?:\s*(?:x|×|\*)\s*10\s*\^\s*\{{?\s*(?P<exp>[{MINUS}+]?\d+)\s*\}}?"  # 1.2 x 10^-10
    rf"|[eE](?P<eexp>[{MINUS}+]?\d+))?"                                       # 1.2e-10
    rf")(?![^\W\d])(?!\.\d)")


@dataclass
class NumberClaim:
    """One number found in a text."""
    text: str      # as written, e.g. "1e-10" or "5.3"
    value: float
    context: str   # a few words around it, for the report


def _int(s: str) -> int:
    return int(s.replace("−", "-"))


def _mask(text: str) -> str:
    """Blank out model IDs, arXiv IDs and list markers, keeping positions."""
    for pattern in (MODEL_ID_RE, ARXIV_RE):
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return LIST_MARKER_RE.sub(lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1))),
                              text)


def find_numbers(text: str, context_chars: int = 40) -> list[NumberClaim]:
    """Every number in `text`, in order, with its value and surrounding words."""
    masked = _mask(text)
    claims = []
    for m in NUMBER_RE.finditer(masked):
        if m.group("pow") is not None:
            value = 10.0 ** _int(m.group("pow"))
        else:
            value = float(m.group("mant"))
            exp = m.group("exp") or m.group("eexp")
            if exp is not None:
                value *= 10.0 ** _int(exp)
            if m.group("sign"):
                value = -value
        start, end = m.span()
        context = " ".join(text[max(0, start - context_chars): end + context_chars].split())
        claims.append(NumberClaim(m.group(0).strip(), value, context))
    return claims


def _same(a: float, b: float, rel_tol: float) -> bool:
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=0.0) or a == b


@dataclass
class NumberReport:
    """Which numbers in an answer appear in the evidence and which do not."""
    claims: list[NumberClaim]
    supported: list[NumberClaim] = field(default_factory=list)
    unsupported: list[NumberClaim] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsupported

    def summary(self) -> str:
        lines = [f"{len(self.claims)} numbers in the answer, {len(self.supported)} found in "
                 f"tool results, {len(self.unsupported)} not found"]
        for c in self.unsupported:
            lines.append(f"  NOT IN ANY TOOL RESULT: {c.text:<8} ...{c.context}...")
        return "\n".join(lines)


def check_numbers(answer: str, evidence: list[dict] | list[str],
                  ignore: Iterable[float] = (), rel_tol: float = 1e-9) -> NumberReport:
    """Check that every number in `answer` appears in some tool result.

    `evidence` is a message list, or a list of tool-output strings (for a
    trace, use guardrails.tool_outputs_from_trace). Values in `ignore` are
    never flagged (for example counts you ask the model to report).
    """
    outputs = evidence if evidence and isinstance(evidence[0], str) else tool_outputs(evidence)
    known = [c.value for text in outputs for c in find_numbers(text, context_chars=0)]
    ignored = list(ignore)
    report = NumberReport(find_numbers(answer))
    for claim in report.claims:
        if any(_same(claim.value, v, rel_tol) for v in known + ignored):
            report.supported.append(claim)
        else:
            report.unsupported.append(claim)
    return report
