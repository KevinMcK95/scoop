"""The exoplanet agent's loop: answer, check, and send a failed answer back once.

A scripted FakeModel plays the agent and the tools return saved results, so these run
offline and do not depend on the archive or ADS being up.
"""
import dataclasses
import json
import unittest

from agentbook.models import FakeModel, call
from agentbook.tools import ToolError, ToolRegistry
from exoplanets.agent import SYSTEM, ask
from exoplanets.evaluate import score
from exoplanets.tools import GET_ABSTRACT, GET_PLANETS
from tests.test_exoplanet_claims import ABSTRACT, QUOTE, TESKE

QUESTION = "Does TOI-561 b have an atmosphere?"
GOOD = json.dumps({"claims": [{"text": "JWST/NIRSpec found the dayside inconsistent with bare rock, "
                                       "suggesting a thick volatile envelope.", "source": TESKE, "quote": QUOTE}]})
OVERSTATED = json.dumps({"claims": [{"text": "TOI-561 b has a thick atmosphere.", "source": TESKE, "quote": QUOTE}]})


def registry(abstract=lambda bibcode: ABSTRACT) -> ToolRegistry:
    return ToolRegistry([dataclasses.replace(GET_ABSTRACT, fn=abstract),
                         dataclasses.replace(GET_PLANETS, fn=lambda hostname: {})])


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


if __name__ == "__main__":
    unittest.main()
