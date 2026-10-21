"""The exoplanet agent's loop: answer, check, and send a failed answer back once.

A scripted FakeModel plays the agent and the tools return saved results, so these run
offline and do not depend on the archive or ADS being up.
"""
import dataclasses
import json
import unittest

from agentbook.models import FakeModel, call
from agentbook.tools import ToolError, ToolRegistry
from exoplanets.agent import SYSTEM, ask, request_text
from exoplanets.evaluate import score
from exoplanets.tools import GET_ABSTRACT, GET_PLANETS
from tests.test_exoplanet_checks import PLANETS
from tests.test_exoplanet_claims import ABSTRACT, QUOTE, TESKE

QUESTION = "Does TOI-561 b have an atmosphere?"
GOOD = json.dumps({"claims": [{"text": "JWST/NIRSpec found the dayside inconsistent with bare rock, "
                                       "suggesting a thick volatile envelope.", "source": TESKE, "quote": QUOTE}]})
OVERSTATED = json.dumps({"claims": [{"text": "TOI-561 b has a thick atmosphere.", "source": TESKE, "quote": QUOTE}]})


def registry(abstract=lambda bibcode: ABSTRACT, planets=lambda hostname: {}) -> ToolRegistry:
    return ToolRegistry([dataclasses.replace(GET_ABSTRACT, fn=abstract),
                         dataclasses.replace(GET_PLANETS, fn=planets)])


class AgentLoopTests(unittest.TestCase):
    def test_a_good_first_answer_is_returned_as_checked(self):
        model = FakeModel([call("get_abstract", bibcode=TESKE), GOOD])
        result = ask(QUESTION, model, registry())
        self.assertTrue(result.checked, result.report.summary())
        self.assertEqual(result.attempts, 1)
        self.assertEqual(model.requests[0]["system"], SYSTEM)
        self.assertIn(f"> “{QUOTE}”", result.text)  # the user sees the paper's own words

    def test_an_overstated_answer_goes_back_and_is_fixed(self):
        model = FakeModel([call("get_abstract", bibcode=TESKE), OVERSTATED, GOOD])
        result = ask(QUESTION, model, registry())
        self.assertTrue(result.checked)
        self.assertEqual(result.attempts, 2)
        retry = model.requests[2]["messages"][-1]["content"]  # what the model was told
        self.assertIn("states as certain what the paper hedges", retry)

    def test_an_answer_that_fails_twice_is_returned_unchecked_with_its_report(self):
        model = FakeModel([call("get_abstract", bibcode=TESKE), OVERSTATED, OVERSTATED])
        result = ask(QUESTION, model, registry())
        self.assertFalse(result.checked)
        self.assertEqual(result.attempts, 2)
        self.assertIn("hedges", result.report.problems[0])

    def test_with_ads_down_the_honest_answer_passes(self):
        def down(bibcode):
            raise ToolError("ADS request failed (HTTPError, HTTP 503). Report that the literature "
                            "search is unavailable rather than citing papers from memory.")

        honest = json.dumps({"claims": [{"text": "The literature search is unavailable (HTTP 503), so I cannot "
                                                 "say what has been published.", "source": "ADS",
                                         "quote": "ADS request failed (HTTPError, HTTP 503)"}]})
        model = FakeModel([call("get_abstract", bibcode=TESKE), honest])
        self.assertTrue(ask(QUESTION, model, registry(down)).checked)

    def test_with_ads_down_a_paper_from_memory_is_caught(self):
        def down(bibcode):
            raise ToolError("ADS request failed (HTTPError, HTTP 503).")

        model = FakeModel([call("get_abstract", bibcode=TESKE), GOOD, GOOD])
        result = ask(QUESTION, model, registry(down))
        self.assertFalse(result.checked)
        self.assertIn("abstract was not fetched", result.report.problems[0])


class BenchmarkScoreTests(unittest.TestCase):
    CASE = {"id": "toi561b-atmosphere", "must_cite": [TESKE, "2026AJ....172..239B"]}

    def test_a_checked_answer_missing_a_required_paper_fails_the_benchmark(self):
        result = ask(QUESTION, FakeModel([call("get_abstract", bibcode=TESKE), GOOD]), registry())
        s = score(self.CASE, result)
        self.assertTrue(s.checked)  # true to its source...
        self.assertEqual(s.missing, ["2026AJ....172..239B"])  # ...but incomplete
        self.assertFalse(s.passed)


class ConstraintFlowTests(unittest.TestCase):
    """Soft constraints: propose concrete criteria, confirm, then apply and highlight."""
    REQUEST = "Add confirmed planet hosts with constraints: habitable, atmosphere claimed."
    FIELD = "draco_1, center RA 346.6264, Dec -5.0435, radius 2 arcmin"
    PROPOSAL = json.dumps({"proposal": {
        "criteria": [{"constraint": "habitable", "means": "insolation 0.25-1.5 Earth", "uses": "archive"},
                     {"constraint": "atmosphere claimed", "means": "a paper reports a detection", "uses": "literature"}],
        "literature_planets": 7, "question": "Use these criteria, or change them?"}})
    PASSES = json.dumps({"claims": [{"text": "TRAPPIST-1 e orbits every 6.10 days.",
                                     "source": "NASA Exoplanet Archive", "quote": '"orbital_period_days": 6.101013'}],
                         "highlight": {"table": "exoplanet_hosts", "ids": ["TRAPPIST-1"], "label": "Habitable"}})

    def test_unconfirmed_constraints_get_a_proposal_to_show_the_user(self):
        model = FakeModel([call("get_planets", hostname="TRAPPIST-1"), self.PROPOSAL])
        result = ask(self.REQUEST, model, registry(), context=self.FIELD)
        self.assertEqual(result.kind, "proposal")
        self.assertTrue(result.checked, result.report.summary())
        self.assertIn("**habitable** → insolation 0.25-1.5 Earth (archive)", result.text)
        self.assertIn("papers of 7 planets checked", result.text)
        self.assertIsNone(result.highlight)  # nothing is applied before the user confirms

    def test_a_literature_criterion_without_a_planet_count_goes_back(self):
        vague = json.dumps({"proposal": {"criteria": [{"constraint": "atmosphere claimed",
                                                       "means": "a paper says so", "uses": "literature"}],
                                         "question": "OK?"}})
        model = FakeModel([vague, self.PROPOSAL])
        result = ask(self.REQUEST, model, registry())
        self.assertEqual(result.attempts, 2)
        self.assertIn("literature_planets", model.requests[1]["messages"][-1]["content"])

    def test_confirmed_criteria_are_applied_and_highlighted(self):
        model = FakeModel([call("get_planets", hostname="TRAPPIST-1"), self.PASSES])
        result = ask(self.REQUEST, model, registry(planets=lambda hostname: PLANETS), context=self.FIELD,
                     criteria="habitable = insolation 0.25-1.5 Earth")
        self.assertEqual(result.kind, "result")
        self.assertTrue(result.checked, result.report.summary())
        self.assertEqual(result.highlight["ids"], ["TRAPPIST-1"])
        sent = model.requests[0]["messages"][0]["content"]
        self.assertIn("Confirmed criteria (apply exactly these): habitable = insolation", sent)

    def test_a_highlight_id_no_tool_returned_fails(self):
        invented = json.dumps({**json.loads(self.PASSES), "highlight": {"table": "exoplanet_hosts", "ids": ["Kepler-442"]}})
        model = FakeModel([call("get_planets", hostname="TRAPPIST-1"), invented, invented])
        result = ask(self.REQUEST, model, registry(planets=lambda hostname: PLANETS), criteria="habitable")
        self.assertFalse(result.checked)
        self.assertIn("Highlight id Kepler-442 was not returned by any tool", result.report.problems[0])

    CLARIFY = json.dumps({"clarify": {"constraints": ["habitable"], "table": "exoplanet_hosts", "count": 7}})

    def test_a_vague_constraint_offers_a_definition_or_claims_with_the_planet_count(self):
        model = FakeModel([call("get_planets", hostname="TRAPPIST-1"), self.CLARIFY])
        result = ask("Add confirmed planet hosts with constraints: habitable.", model,
                     registry(planets=lambda hostname: PLANETS))
        self.assertEqual(result.kind, "clarify")
        self.assertTrue(result.checked, result.report.summary())
        self.assertTrue(result.text.startswith("How should I select “habitable” planets?"))
        self.assertIn("1. **By a definition:** give me a range (e.g. of equilibrium temperature or insolation)", result.text)
        self.assertIn("2. **By claims in the literature:** find which of the 7 planets in this field", result.text)

    def test_a_count_not_taken_from_a_tool_is_sent_back(self):
        model = FakeModel([self.CLARIFY, call("get_planets", hostname="TRAPPIST-1"), self.CLARIFY])
        result = ask("Add confirmed planet hosts with constraints: habitable.", model,
                     registry(planets=lambda hostname: PLANETS))
        self.assertEqual(result.attempts, 2)
        self.assertIn("Count the objects with a tool", model.requests[1]["messages"][-1]["content"])
        self.assertTrue(result.checked)

    def test_tic_constraints_are_about_stars(self):
        from exoplanets.agent import render_clarify
        text = render_clarify({"constraints": ["M dwarfs"], "table": "tic", "count": 1})
        self.assertIn("How should I select “M dwarfs” stars?", text)
        self.assertIn("e.g. of Teff, radius or log g", text)
        self.assertIn("which of the 1 star in this field", text)

    # A made-up paper (fictional bibcode), so no real abstract is misquoted.
    HZ = {"bibcode": "2099TestJ...1....1A", "title": "A test habitable zone",
          "abstract": "We define the habitable zone as insolation from 0.3 to 1.2 times the Earth's."}
    SUGGESTION = {"constraint": "habitable", "means": "insolation 0.3-1.2 Earth", "uses": "archive",
                  "suggested": True, "source": "2099TestJ...1....1A",
                  "quote": "the habitable zone as insolation from 0.3 to 1.2 times the Earth's"}

    def suggest(self, criterion):
        proposal = json.dumps({"proposal": {"criteria": [criterion], "literature_planets": 0, "question": "Is this okay?"}})
        model = FakeModel([call("get_abstract", bibcode=self.HZ["bibcode"]), proposal, proposal])
        return ask("Habitable: I have no definition, suggest one.", model, registry(abstract=lambda bibcode: self.HZ))

    def test_a_suggested_definition_shows_its_one_reference_and_quote(self):
        result = self.suggest(self.SUGGESTION)
        self.assertTrue(result.checked, result.report.summary())
        self.assertIn("insolation 0.3-1.2 Earth (archive), suggested from 2099TestJ...1....1A", result.text)
        self.assertIn("> “the habitable zone as insolation from 0.3 to 1.2", result.text)

    def test_a_range_the_user_gave_is_restated_without_a_reference(self):
        # Found live: "Teq from 300 K to 500 K" came back "suggested from" a paper.
        given = {"constraint": "Teq 300-500 K", "means": "equilibrium temperature 300-500 K", "uses": "archive"}
        referenced = {**given, "suggested": True, "source": "2099TestJ...1....1A",
                      "quote": "the habitable zone as insolation from 0.3 to 1.2 times the Earth's"}
        proposal = lambda c: json.dumps({"proposal": {"criteria": [c], "literature_planets": 0, "question": "Is this okay?"}})
        model = FakeModel([call("get_abstract", bibcode=self.HZ["bibcode"]), proposal(referenced), proposal(given)])
        result = ask("Show me the planets with equilibrium temperature from 300 K to 500 K", model,
                     registry(abstract=lambda bibcode: self.HZ))
        self.assertEqual(result.attempts, 2)
        self.assertIn("the user gave this range and did not ask for a suggestion",
                      model.requests[2]["messages"][-1]["content"])
        self.assertTrue(result.checked)
        self.assertNotIn("suggested from", result.text)

    def test_a_suggested_definition_without_a_reference_fails(self):
        result = self.suggest({k: v for k, v in self.SUGGESTION.items() if k not in ("source", "quote")})
        self.assertFalse(result.checked)
        self.assertIn("give the one paper it comes from", result.report.problems[0])

    def test_a_suggested_range_that_differs_from_the_paper_fails(self):
        result = self.suggest({**self.SUGGESTION, "means": "insolation 0.25-1.5 Earth"})
        self.assertFalse(result.checked)
        self.assertIn("the number 0.25 is not in any tool result", result.report.problems[0])

    def test_the_request_carries_the_field_and_the_criteria(self):
        self.assertEqual(request_text("Q?", "draco_1", None), "Q?\n\nField: draco_1")
        self.assertEqual(request_text("Q?", None, None), "Q?")


if __name__ == "__main__":
    unittest.main()
