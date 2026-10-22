"""Numbers in a text, for checking that every number in an answer came from a tool result.

    from scoop.exoplanets.numbers import find_numbers
    [(c.text, c.value) for c in find_numbers("a period of 6.10 days, 1.2e-3 or 10^-5")]

Values are read numerically, so "1e-10" and "10^-10" are the same number. Not treated as
numbers: registry-style IDs ("..._v1"), arXiv IDs, list markers at the start of a line
("1."), digits glued to letters or underscores ("gap_1", "s2", "2theta", "sin^2"), and
exponents written as part of a symbol. Object names and bibcodes are masked by the
checks themselves (checks._mask).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# IDs like 2305_06364_twin_sterile_neutrino_dark_matter_v1, whose digits are not values.
MODEL_ID_RE = re.compile(r"(?<![\w.\-])[A-Za-z0-9][\w.\-]*?_v\d+(?!\w)")
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
    """Blank out IDs, arXiv IDs and list markers, keeping positions."""
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
