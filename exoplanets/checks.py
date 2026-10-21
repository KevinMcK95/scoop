"""Checks on the exoplanet agent's answers, run in code before the user sees them.

Every claim must be traceable to a tool result:

- every bibcode cited was returned by a tool in this run (not recalled by the model);
- every number appears in some tool result, allowing for rounding ("0.92" matches 0.9198);
- every sentence cites its source: a bibcode, or an archive by name. Not only the ones
  with numbers: "TOI-561 b has an atmosphere" is a claim too.

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
                + [f"This sentence has no source: \"{s}\". Add the bibcode it comes from, or name "
                   f"the archive ({', '.join(ARCHIVES)}); if no tool result supports it, remove it."
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
        if not (find_bibcodes(sentence) or any(a in sentence for a in ARCHIVES)):
            report.uncited_sentences.append(sentence)
    return report


# ------------------------------------------------------------------ structured claims
# The agent answers with a list of claims, so each one is checked exactly instead of
# guessing sentence boundaries:
#
#   {"claims": [{"text": "TOI-561 b's dayside is inconsistent with bare rock, suggesting a
#                         thick volatile envelope.",
#                "source": "2025ApJ...995L..39T",
#                "quote": "inconsistent with a bare-rock surface at high statistical
#                          significance, suggesting instead a thick volatile envelope"},
#               {"text": "Its orbital period is 0.45 days.", "source": "NASA Exoplanet Archive",
#                "quote": "\"orbital_period_days\": 0.446573"}]}
#
# The quote must appear word for word in the cited source's tool result: for a bibcode,
# that paper's get_abstract result; for an archive, a result of one of its tools.

# Which tools' results count as which archive. A bibcode's text is its get_abstract result.
TOOL_SOURCE = {"get_planets": ("NASA Exoplanet Archive",), "exoplanet_cone_search": ("NASA Exoplanet Archive",),
               "toi_cone_search": ("NASA Exoplanet Archive",), "resolve_target": ("Sesame",),
               "find_papers": ("ADS",), "get_abstract": ("ADS",), "hst_jwst_programs": ("MAST",),
               "extractable_light_curves": ("MAST", "TIC"),
               "short_cadence_candidates": ("SCOOP short-cadence criteria", "TIC", "MAST")}
CLAIM_SOURCES = ARCHIVES + ("ADS", "Sesame", "TIC", "SCOOP short-cadence criteria")
# Words that carry a paper's uncertainty; a claim that drops all of them overstates it.
HEDGE_RE = re.compile(r"\b(?:may|might|could|suggest\w*|tentative\w*|possibl\w*|likely|unlikely|"
                      r"consistent with|indicat\w*|hint\w*|candidate|evidence for|if|unless|"
                      r"cannot (?:be )?rule\w* out|marginal\w*|probabl\w*|appear\w*|favou?r\w*)\b"
                      r"|±|σ\b|sigma\b", re.I)
ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


def normalize(text: str) -> str:
    """For comparing a quote with its source: one space, one kind of dash, quote mark and
    tilde, and no case. Nothing else is forgiven."""
    text = re.sub(r"[‐‑‒–—―─−]", "-", text)
    text = re.sub(r"[‘’‛′]", "'", re.sub(r"[“”„″]", '"', text))
    text = text.replace("∼", "~").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_in(quote: str, text: str) -> bool:
    """Is `quote` in `text` word for word? An ellipsis may join fragments, in order."""
    text, position = normalize(text), 0
    for fragment in ELLIPSIS_RE.split(quote):
        fragment = normalize(fragment.strip(' "\''))
        if not fragment:
            continue
        found = text.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return position > 0


def tool_results_by_name(messages: list[dict]) -> list[tuple[str, str]]:
    """(tool name, result text) for every tool result in a message list."""
    names = {b["id"]: b["name"] for m in messages if m["role"] == "assistant" and not isinstance(m["content"], str)
             for b in m["content"] if b["type"] == "tool_use"}
    return [(names.get(b["tool_use_id"], "?"), b["content"] if isinstance(b["content"], str) else json.dumps(b["content"]))
            for m in messages if m["role"] == "user" and not isinstance(m["content"], str)
            for b in m["content"] if b["type"] == "tool_result"]


def _decoded(text: str) -> str:
    """A tool result as the model read it, with JSON escapes undone (\" and \u00b1 in
    the raw text would never match a quote)."""
    try:
        return json.dumps(json.loads(text), indent=1, ensure_ascii=False)
    except ValueError:
        return text


def parse_claims(answer: str) -> list[dict] | None:
    """The claims in an answer (JSON, optionally in a ```json block), or None if malformed."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", answer.strip())
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        claims = data.get("claims") if isinstance(data, dict) else None
        if isinstance(claims, list) and all(isinstance(c, dict) for c in claims):
            return claims
    return None


@dataclass
class ClaimsReport:
    claims: list[dict]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.claims) and not self.problems

    def summary(self) -> str:
        if self.ok:
            return f"OK: {len(self.claims)} claims, each quoted word for word from its source."
        return "\n".join(["Answer failed the checks:"] + [f"- {p}" for p in self.problems])


def check_claims(answer: str, messages: list[dict]) -> ClaimsReport:
    """Check a structured answer against this run's tool results (see the comment above)."""
    claims = parse_claims(answer)
    if claims is None:
        return ClaimsReport([], ['The answer must be JSON: {"claims": [{"text": ..., "source": ..., '
                                 '"quote": ...}, ...]}, and nothing else.'])
    report = ClaimsReport(claims)
    if not claims:
        report.problems.append("The answer has no claims. If the tools found nothing, say so as a claim "
                               "whose quote is the tool result that shows it.")
    results = tool_results_by_name(messages)
    outputs = [text for _, text in results]
    abstracts: dict[str, str] = {}
    for name, text in results:
        if name == "get_abstract":
            try:  # compare with the decoded text: in JSON, "3─5 μm" is "3\u25005 \u03bcm"
                paper = json.loads(text)
                abstracts[paper["bibcode"]] = f"{paper.get('title') or ''}\n{paper.get('abstract') or ''}"
            except (ValueError, KeyError, TypeError):
                continue  # an error message, not an abstract
    names = tool_names(outputs)
    known = [c.value for text in outputs for c in find_numbers(_mask(text, names), context_chars=0)]

    for i, claim in enumerate(claims, 1):
        text, source, quote = (str(claim.get(k) or "").strip() for k in ("text", "source", "quote"))
        label = f'Claim {i} ("{text[:60]}{"..." if len(text) > 60 else ""}")'
        if not text:
            report.problems.append(f"Claim {i} has no text.")
            continue
        if not source:
            report.problems.append(f"{label} has no source. Give the bibcode or archive it comes from.")
            continue
        if not quote:
            report.problems.append(f"{label} has no quote. Copy the words it rests on, exactly, from "
                                   f"the tool result for {source}.")
            continue
        if BIBCODE_RE.fullmatch(source):
            if source not in abstracts:
                report.problems.append(f"{label} cites {source}, but its abstract was not fetched in this "
                                       "conversation. Call get_abstract for it and quote from that, or "
                                       "remove the claim.")
            elif not quote_in(quote, abstracts[source]):
                elsewhere = [b for b, t in abstracts.items() if b != source and quote_in(quote, t)]
                report.problems.append(
                    f"{label}: the quote is not in {source}'s abstract"
                    + (f"; it is from {', '.join(elsewhere)}. Cite that paper instead." if elsewhere
                       else ". Copy the words exactly as the abstract has them."))
            elif HEDGE_RE.search(quote) and not HEDGE_RE.search(text):
                report.problems.append(f"{label} states as certain what the paper hedges (\"{quote[:80]}\"). "
                                       "Keep the paper's conditions and uncertainty in the claim.")
        elif source in CLAIM_SOURCES:
            own = [_decoded(t) for name, t in results if source in TOOL_SOURCE.get(name, ())]
            if not any(quote_in(quote, t) for t in own):
                report.problems.append(f"{label}: the quote is not in any {source} tool result. Copy the "
                                       "value or words exactly as the tool returned them.")
        else:
            report.problems.append(f"{label}: source \"{source}\" is neither a bibcode nor one of "
                                   f"{', '.join(CLAIM_SOURCES)}.")
            continue
        for number in find_numbers(_mask(text, names)):
            if not _supported(number, known):
                report.problems.append(f"{label}: the number {number.text} is not in any tool result.")
    return report


def render_claims(claims: list[dict]) -> str:
    """The answer the user reads: each claim with its source; papers' words quoted."""
    lines = []
    for c in claims:
        source = str(c.get("source") or "no source")
        lines.append(f"- {c.get('text') or ''} ({source})")
        if BIBCODE_RE.fullmatch(source) and c.get("quote"):
            lines.append(f"  > “{c['quote']}”")
    return "\n".join(lines)


# ------------------------------------------------------------------ proposals and highlights
# Besides claims, an answer can ask the user how to treat a vague constraint ("habitable"):
# by a definition (a range of archive values) or by claims in the literature (papers about
# each object). The agent counts the objects first; the wording is rendered in code.
#
#   {"clarify": {"constraints": ["habitable"], "table": "exoplanet_hosts", "count": 7}}
#
# or propose concrete criteria, to confirm before they are applied. A criterion the agent
# suggested from the literature (because the user asked for a suggestion) carries its
# reference and a quote, checked like a claim:
#
#   {"proposal": {"criteria": [{"constraint": "habitable", "means": "insolation 0.2-1.1 Earth",
#                               "uses": "archive", "suggested": true,
#                               "source": "<bibcode>", "quote": "<words from its abstract>"}, ...],
#                 "literature_planets": 3, "question": "Is this definition okay?"}}
#
# A result can also name rows to highlight on the map, by their id in a field table:
#
#   {"claims": [...], "highlight": {"table": "exoplanet_hosts", "ids": ["TRAPPIST-1"],
#                                   "label": "Habitable-zone planets"}}

HIGHLIGHT_TABLES = {"exoplanet_hosts": "host name, e.g. TRAPPIST-1",
                    "tess_tois": "TOI, e.g. TOI-700.01", "tic": "TIC ID, e.g. TIC 150428135"}


def parse_answer(answer: str) -> dict | None:
    """The answer's JSON object, if it is a clarification, a proposal or a claims result; else None."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", answer.strip())
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and ({"clarify", "proposal", "claims"} & set(data)):
            return data
    return None


def check_clarify(clarify, messages: list[dict] | None = None) -> list[str]:
    """A clarification names the constraints and the table, and gives the number of objects
    in it, counted with a tool (so the user knows what checking claims for each would cost)."""
    if not isinstance(clarify, dict) or not clarify.get("constraints") \
            or clarify.get("table") not in HIGHLIGHT_TABLES or not isinstance(clarify.get("count"), int):
        return ['A clarification must be {"clarify": {"constraints": [...], "table": one of '
                f'{", ".join(HIGHLIGHT_TABLES)}, "count": the number of objects in that table in this '
                'field, from a tool}}.']
    if clarify["count"] and not tool_results_by_name(messages or []):
        return ["Count the objects with a tool (e.g. exoplanet_cone_search over the field) before "
                "giving count."]
    return []


# Words with which a user asks for a definition instead of giving one.
SUGGEST_RE = re.compile(r"\b(?:suggest\w*|recommend\w*|propos\w*|no idea|not sure|don'?t know|"
                        r"do not know|from the literature|in the literature|you (?:choose|decide|pick))\b", re.I)


def user_asked_for_suggestion(messages: list[dict]) -> bool:
    """Did the user's own words (not tool results or the checks' retry notes) ask for one?"""
    texts = [m["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], str)
             and not m["content"].startswith("Your answer failed the checks")]
    return any(SUGGEST_RE.search(t) for t in texts)


def check_proposal(proposal, messages: list[dict] | None = None) -> list[str]:
    """A proposal needs criteria, each with what it means and whether it uses the archive or
    papers; a criterion suggested from the literature needs its reference, quoted exactly."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("criteria"), list) or not proposal["criteria"]:
        return ['A proposal must be {"proposal": {"criteria": [{"constraint": ..., "means": ..., "uses": '
                '"archive" or "literature"}], "literature_planets": n, "question": ...}}.']
    problems = []
    for i, c in enumerate(proposal["criteria"], 1):
        if not isinstance(c, dict) or not c.get("constraint") or not c.get("means"):
            problems.append(f"Criterion {i} needs the constraint and what it means concretely.")
        elif c.get("uses") not in ("archive", "literature"):
            problems.append(f"Criterion {i} ({c['constraint']}): say whether it uses the archive or literature.")
        elif c.get("suggested") and messages is not None and not user_asked_for_suggestion(messages):
            problems.append(f"Criterion {i} ({c['constraint']}): the user gave this range and did not ask "
                            "for a suggestion. Restate it as they gave it: no suggested, source or quote.")
        elif c.get("suggested"):
            if not (c.get("source") and c.get("quote")):
                problems.append(f"Criterion {i} ({c['constraint']}) is your suggestion: give the one paper it "
                                "comes from (source: its bibcode) and quote the words that define it.")
            else:
                report = check_claims(json.dumps({"claims": [{"text": c["means"], "source": c["source"],
                                                             "quote": c["quote"]}]}), messages or [])
                problems += [p.replace("Claim 1", f"Criterion {i} ({c['constraint']})", 1) for p in report.problems]
    if any(isinstance(c, dict) and c.get("uses") == "literature" for c in proposal["criteria"]) \
            and not isinstance(proposal.get("literature_planets"), int):
        problems.append("Say how many planets need their papers checked (literature_planets), so the user "
                        "can decide before you start.")
    if not proposal.get("question"):
        problems.append("End the proposal with a question asking the user to confirm or change the criteria.")
    return problems


def check_highlight(highlight, messages: list[dict]) -> list[str]:
    """Rows to highlight must name a field table and ids that a tool returned in this run."""
    if highlight is None:
        return []
    if not isinstance(highlight, dict) or highlight.get("table") not in HIGHLIGHT_TABLES \
            or not isinstance(highlight.get("ids"), list):
        return [f"highlight must be {{\"table\": one of {', '.join(HIGHLIGHT_TABLES)}, \"ids\": [...], "
                "\"label\": ...}."]
    seen = "\n".join(_decoded(text) for _, text in tool_results_by_name(messages))
    missing = [str(i) for i in highlight["ids"] if str(i).removeprefix("TIC ") not in seen]
    return [f"Highlight id {i} was not returned by any tool. Use ids from tool results "
            f"({HIGHLIGHT_TABLES[highlight['table']]})." for i in missing]
