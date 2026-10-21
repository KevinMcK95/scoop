"""Structured claims: each one quoted word for word from the source it cites."""
import json
import unittest

from agentbook.models import tool_result_block, tool_use_block
from exoplanets.checks import check_claims, quote_in, render_claims
from tests.test_exoplanet_checks import PLANETS

TESKE = "2025ApJ...995L..39T"
# From get_abstract (Teske et al. 2025), trimmed to the two sentences the tests use.
ABSTRACT = {"bibcode": TESKE, "title": "A Thick Volatile Atmosphere on the Ultrahot Super-Earth TOI-561 b",
            "abstract": ("Here, we present the first dayside emission spectrum of the low-density "
                         "(4.3 ± 0.4 g cm−3) USP planet TOI-561 b, which orbits an iron-poor, alpha-rich, "
                         "∼10 Gyr old thick-disk star. Our 3─5 μm JWST/NIRSpec observations demonstrate the "
                         "dayside of TOI-561 b is inconsistent with a bare-rock surface at high statistical "
                         "significance, suggesting instead a thick volatile envelope that is cooling the "
                         "dayside to well below the ∼3000 K expected in the bare-rock or thin-atmosphere case."),
            "archive": "ADS"}
OTHER = {"bibcode": "2023A&A...679A..92P", "title": "CHEOPS and TESS view of the ultra-short-period super-Earth TOI-561 b",
         "abstract": "the observations are consistent with a negligible H/He atmosphere", "archive": "ADS"}


def conversation(*results):
    """A message list in which each (tool, input, result) was called and answered."""
    messages = [{"role": "user", "content": "Does TOI-561 b have an atmosphere?"}]
    for i, (name, tool_input, result) in enumerate(results):
        messages.append({"role": "assistant", "content": [tool_use_block(f"t{i}", name, tool_input)]})
        messages.append({"role": "user", "content": [tool_result_block(f"t{i}", json.dumps(result))]})
    return messages


MESSAGES = conversation(("get_abstract", {"bibcode": TESKE}, ABSTRACT),
                        ("get_abstract", {"bibcode": OTHER["bibcode"]}, OTHER),
                        ("get_planets", {"hostname": "TRAPPIST-1"}, PLANETS))
QUOTE = ("Our 3─5 μm JWST/NIRSpec observations demonstrate the dayside of TOI-561 b is inconsistent with "
         "a bare-rock surface at high statistical significance, suggesting instead a thick volatile envelope")


def check(*claims, messages=MESSAGES):
    return check_claims(json.dumps({"claims": list(claims)}), messages)


class GoodClaimTests(unittest.TestCase):
    def test_a_paper_claim_quoted_word_for_word_with_its_hedge_passes(self):
        report = check({"text": "JWST/NIRSpec found the dayside inconsistent with bare rock, suggesting a "
                                "thick volatile envelope.", "source": TESKE, "quote": QUOTE})
        self.assertTrue(report.ok, report.summary())

    def test_an_archive_value_quoted_as_returned_passes(self):
        report = check({"text": "TRAPPIST-1 e orbits every 6.10 days.", "source": "NASA Exoplanet Archive",
                        "quote": '"orbital_period_days": 6.101013'})
        self.assertTrue(report.ok, report.summary())

    def test_fragments_joined_by_an_ellipsis_must_appear_in_order(self):
        self.assertTrue(quote_in("Our 3─5 μm JWST/NIRSpec observations ... suggesting instead a thick volatile "
                                 "envelope", ABSTRACT["abstract"]))
        self.assertFalse(quote_in("suggesting instead a thick volatile envelope ... Our 3─5 μm", ABSTRACT["abstract"]))

    def test_dashes_quote_marks_and_spacing_are_forgiven_but_not_words(self):
        self.assertTrue(quote_in("Our 3-5  μm JWST/NIRSpec", ABSTRACT["abstract"]))
        self.assertFalse(quote_in("Our 3-5 μm JWST observations", ABSTRACT["abstract"]))


class BadClaimTests(unittest.TestCase):
    def test_your_toi_561_b_sentence_fails_without_a_source(self):
        report = check({"text": "TOI 561 b has an atmosphere."})
        self.assertIn("has no source", report.problems[0])

    def test_a_claim_that_drops_the_papers_hedge_fails(self):
        report = check({"text": "TOI-561 b has a thick volatile atmosphere.", "source": TESKE, "quote": QUOTE})
        self.assertIn("states as certain what the paper hedges", report.problems[0])

    def test_a_paraphrased_quote_fails(self):
        report = check({"text": "TOI-561 b may have a thick atmosphere.", "source": TESKE,
                        "quote": "the planet has a thick volatile envelope"})
        self.assertIn("not in 2025ApJ...995L..39T's abstract", report.problems[0])

    def test_a_quote_from_another_paper_names_the_right_one(self):
        report = check({"text": "TOI-561 b is consistent with a negligible H/He atmosphere.", "source": TESKE,
                        "quote": "consistent with a negligible H/He atmosphere"})
        self.assertIn("it is from 2023A&A...679A..92P", report.problems[0])

    def test_an_archive_cannot_be_cited_for_a_papers_finding(self):
        # The hole in the free-text check: "(NASA Exoplanet Archive)" passed for anything.
        report = check({"text": "TOI-561 b has an atmosphere.", "source": "NASA Exoplanet Archive",
                        "quote": "thick volatile envelope"})
        self.assertIn("not in any NASA Exoplanet Archive tool result", report.problems[0])

    def test_a_paper_whose_abstract_was_never_fetched_fails(self):
        report = check({"text": "TRAPPIST-1 e was found by transit.", "source": "2017Natur.542..456G",
                        "quote": "TRAPPIST-1"})
        self.assertIn("abstract was not fetched", report.problems[0])

    def test_a_number_from_memory_fails_even_with_a_good_quote(self):
        report = check({"text": "Its dayside is about 1800 K, suggesting a thick envelope.", "source": TESKE,
                        "quote": QUOTE})
        self.assertIn("the number 1800", report.problems[0])

    def test_free_text_instead_of_json_fails(self):
        report = check_claims("TOI-561 b has an atmosphere (2025ApJ...995L..39T).", MESSAGES)
        self.assertFalse(report.ok)
        self.assertIn("must be JSON", report.problems[0])


class RenderTests(unittest.TestCase):
    def test_paper_claims_show_the_papers_own_words(self):
        text = render_claims([{"text": "Inconsistent with bare rock.", "source": TESKE, "quote": QUOTE},
                              {"text": "Period 6.10 d.", "source": "NASA Exoplanet Archive", "quote": "6.101013"}])
        self.assertEqual(text.splitlines(), [f"- Inconsistent with bare rock. ({TESKE})", f"  > “{QUOTE}”",
                                             "- Period 6.10 d. (NASA Exoplanet Archive)"])


if __name__ == "__main__":
    unittest.main()
