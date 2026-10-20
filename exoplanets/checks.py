"""Checks on the exoplanet agent's answers, run in code before the user sees them.

Every claim must be traceable to a tool result:

- every bibcode cited was returned by a tool in this run (not recalled by the model);
- every number appears in some tool result, allowing for rounding ("0.92" matches 0.9198);
- every sentence with a number cites its source: a bibcode, or an archive by name.

    from exoplanets.checks import check_answer
    report = check_answer(result.text, result.messages)
    report.ok, report.problems

Like agentbook's check_numbers, these checks are deliberately simple. They catch a
value or reference that came from nowhere; they cannot tell that a real value was
attached to the wrong planet. A failed check is something to send back to the model
or show the user, never something to hide.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agentbook.guardrails import tool_outputs
from agentbook.provenance import NumberClaim, find_numbers

# ADS bibcodes are 19 characters: year, journal, volume, qualifier, page, first author's
# initial, e.g. 2016Natur.533..221G, 2014ApJ...781...18C, 2021PSJ.....2....1A.
BIBCODE_RE = re.compile(r"(?<![\w.])\d{4}[A-Za-z&][\w&.]{13}[A-Z](?![\w])")
# Object designations whose digits are part of a name, not a value: TRAPPIST-1, Kepler-90,
# KOI-351, TOI-700.01, HAT-P-11, K2-18; HD 209458, TIC 150428135, Gaia DR3 2635476908753563008.
DESIGNATION_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z]+)*-\d+(?:\.\d+)?[A-Za-z]?\b"
    r"|\b(?:HD|HIP|HR|GJ|Gl|LHS|LP|TIC|TOI|KOI|KIC|EPIC|WASP|Gaia DR[23]|2MASS)\s?J?[+-]?\d[\d.+-]*\b")
# Fields of tool results that hold object names ("51 Peg" fits no pattern but is returned).
NAME_KEYS = {"hostname", "native_id", "pl_name", "name", "main_id", "target", "id"}
# Sources that may be cited by name for facts that come from an archive, not a paper.
ARCHIVES = ("NASA Exoplanet Archive", "MAST", "ExoFOP")
# Sentence ends, but not "et al. 2016" or "e.g. 7".
SENTENCE_END_RE = re.compile(r"(?<!\bal)(?<!\be\.g)(?<!\bi\.e)\.\s+(?=[A-Z(])|[!?]\s+|\n+")


def find_bibcodes(text: str) -> list[str]:
    """ADS bibcodes in a text, in order, without duplicates."""
    return list(dict.fromkeys(BIBCODE_RE.findall(text)))


def _blank(match: re.Match) -> str:
    return " " * len(match.group(0))


def tool_names(outputs: list[str]) -> list[str]:
    """Object names in tool results (values of NAME_KEYS), longest first."""
    names: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in NAME_KEYS and isinstance(item, str) and any(ch.isdigit() for ch in item):
                    names.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for text in outputs:
        try:
            walk(json.loads(text))
        except ValueError:  # an error message or other plain text
            continue
    return sorted(names, key=len, reverse=True)


def _mask(text: str, names: list[str] = ()) -> str:
    """Blank out bibcodes and object names (keeping positions), so their digits are not
    read as numbers."""
    text = BIBCODE_RE.sub(_blank, text)
    for name in names:
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(?![\w])", _blank, text)
    return DESIGNATION_RE.sub(_blank, text)


def _decimals(claim: NumberClaim) -> int | None:
    """Decimal places as written ("0.92" -> 2, "7" -> 0); None for 1e-3 and 10^5 forms."""
    if re.search(r"[eE^×x*]", claim.text):
        return None
    return len(claim.text.split(".")[1]) if "." in claim.text else 0


def _supported(claim: NumberClaim, known: list[float]) -> bool:
    """Does some tool value round to the number as written? (Sign is ignored: "-5.04" and
    "5.04 deg south" are both fine.)"""
    places = _decimals(claim)
    target = abs(claim.value)
    for value in known:
        value = abs(value)
        if places is None:
            if abs(value - target) <= 1e-3 * max(abs(target), 1e-300):
                return True
        elif abs(value - target) <= 0.5 * 10 ** -places + 1e-12:
            return True
    return False


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END_RE.split(text) if s and s.strip()]


@dataclass
class AnswerReport:
    """What the checks found in one answer."""
    bibcodes: list[str]
    invented_bibcodes: list[str] = field(default_factory=list)
    unsupported_numbers: list[NumberClaim] = field(default_factory=list)
    uncited_sentences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.invented_bibcodes or self.unsupported_numbers or self.uncited_sentences)

    @property
    def problems(self) -> list[str]:
        """One line per problem, worded so the model can fix it."""
        return ([f"Bibcode {b} was not returned by any tool. Cite only bibcodes from tool "
                 "results, or remove the claim." for b in self.invented_bibcodes]
                + [f"The number {c.text} (\"...{c.context}...\") is not in any tool result. Use the "
                   "tool's value, or remove it." for c in self.unsupported_numbers]
                + [f"This sentence has a number but no source: \"{s}\". Add the bibcode the value "
                   f"comes from, or name the archive ({', '.join(ARCHIVES)})."
                   for s in self.uncited_sentences])

    def summary(self) -> str:
        if self.ok:
            return f"OK: {len(self.bibcodes)} bibcodes cited, all numbers and references found in tool results."
        return "\n".join(["Answer failed the checks:"] + [f"- {p}" for p in self.problems])


def check_answer(answer: str, evidence: list[dict] | list[str]) -> AnswerReport:
    """Check `answer` against the tool results in `evidence` (a message list, or a list
    of tool-output strings)."""
    outputs = evidence if evidence and isinstance(evidence[0], str) else tool_outputs(evidence)
    seen = "\n".join(outputs)
    report = AnswerReport(find_bibcodes(answer))
    report.invented_bibcodes = [b for b in report.bibcodes if b not in seen]

    names = tool_names(outputs)
    known = [c.value for text in outputs for c in find_numbers(_mask(text, names), context_chars=0)]
    report.unsupported_numbers = [c for c in find_numbers(_mask(answer, names))
                                  if not _supported(c, known)]

    for sentence in sentences(answer):
        has_number = bool(find_numbers(_mask(sentence, names), context_chars=0))
        cited = bool(find_bibcodes(sentence)) or any(a in sentence for a in ARCHIVES)
        if has_number and not cited:
            report.uncited_sentences.append(sentence)
    return report
