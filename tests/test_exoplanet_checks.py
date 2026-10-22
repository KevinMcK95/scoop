"""The checks that every claim in an exoplanet answer comes from a tool result."""
import json
import unittest

from agentbook.models import tool_result_block, tool_use_block
from scoop.exoplanets.checks import check_answer, find_bibcodes, sentences

# What get_planets returns, trimmed to one planet (values as in the archive).
PLANETS = {"hostname": "TRAPPIST-1", "distance_pc": 12.429888, "n_planets": 7, "planets": [{
    "native_id": "TRAPPIST-1 e", "orbital_period_days": 6.101013, "radius_earth": 0.920,
    "mass_earth": 0.692, "mass_type": "Mass", "equilibrium_temp_k": 249.7,
    "discovery_method": "Transit", "discovery_year": 2017, "discovery_facility": "Spitzer Space Telescope",
    "reference": "Gillon et al. 2017", "bibcode": "2017Natur.542..456G",
    "access_url": "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"}]}

MESSAGES = [
    {"role": "user", "content": "Tell me about TRAPPIST-1 e."},
    {"role": "assistant", "content": [tool_use_block("toolu_001", "get_planets", {"hostname": "TRAPPIST-1"})]},
    {"role": "user", "content": [tool_result_block("toolu_001", json.dumps(PLANETS))]},
]


class GoodAnswerTests(unittest.TestCase):
    def test_a_cited_answer_built_from_the_tool_result_passes(self):
        answer = ("TRAPPIST-1 e was discovered by transit in 2017 by Gillon et al. (2017Natur.542..456G). "
                  "It orbits every 6.10 days, with a radius of 0.92 R⊕ and a measured mass of 0.69 M⊕ "
                  "(NASA Exoplanet Archive). Its equilibrium temperature is about 250 K (NASA Exoplanet Archive).")
        report = check_answer(answer, MESSAGES)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.bibcodes, ["2017Natur.542..456G"])

    def test_rounded_values_are_supported(self):
        # 6.101013 -> "6.1", 0.920 -> "0.92", 249.7 -> "250"; all fine as written.
        self.assertTrue(check_answer("Period 6.1 d, radius 0.92 R⊕, T_eq 250 K (NASA Exoplanet Archive).",
                                     MESSAGES).ok)

    def test_a_bibcodes_digits_are_not_read_as_numbers(self):
        report = check_answer("Discovered by Gillon et al. (2017Natur.542..456G).", MESSAGES)
        self.assertEqual(report.unsupported_numbers, [])


class BadAnswerTests(unittest.TestCase):
    def test_a_bibcode_no_tool_returned_is_flagged(self):
        # A real paper on TRAPPIST-1 (Agol et al. 2021), but no tool returned it in this run.
        report = check_answer("Its radius is 0.92 R⊕ (2021PSJ.....2....1A).", MESSAGES)
        self.assertEqual(report.invented_bibcodes, ["2021PSJ.....2....1A"])
        self.assertIn("not returned by any tool", report.problems[0])

    def test_a_number_from_memory_is_flagged(self):
        report = check_answer("Its density is 5.65 g/cm³ (NASA Exoplanet Archive).", MESSAGES)
        self.assertEqual([c.text for c in report.unsupported_numbers], ["5.65"])

    def test_a_value_rounded_too_far_is_flagged(self):
        # 0.920 rounds to "0.9", not to "0.8".
        self.assertFalse(check_answer("Radius 0.8 R⊕ (NASA Exoplanet Archive).", MESSAGES).ok)

    def test_a_number_without_a_source_is_flagged(self):
        report = check_answer("TRAPPIST-1 e orbits every 6.10 days.", MESSAGES)
        self.assertEqual(report.uncited_sentences, ["TRAPPIST-1 e orbits every 6.10 days."])

    def test_the_report_tells_the_model_what_to_fix(self):
        summary = check_answer("TRAPPIST-1 e orbits every 6.10 days.", MESSAGES).summary()
        self.assertIn("no source", summary)
        self.assertIn("Add the bibcode", summary)

    def test_a_claim_without_a_number_still_needs_a_source(self):
        # Found by hand: this passed when only sentences with numbers needed a source.
        report = check_answer("TOI 561 b has an atmosphere.", MESSAGES)
        self.assertEqual(report.uncited_sentences, ["TOI 561 b has an atmosphere."])


class ObjectNameTests(unittest.TestCase):
    """Digits in names (TRAPPIST-1, HD 209458) are not values to check or cite."""

    def test_designations_are_not_numbers(self):
        for answer in ("TRAPPIST-1 e is a rocky planet.", "HD 209458 b was the first transiting planet found.",
                       "TOI-700.01 is confirmed.", "KOI-351 is Kepler-90.", "TIC 150428135 is TOI-700."):
            with self.subTest(answer):
                self.assertEqual(check_answer(answer, MESSAGES).unsupported_numbers, [])

    def test_names_the_tools_returned_are_not_numbers(self):
        evidence = [json.dumps({"hostname": "51 Peg", "planets": [{"native_id": "51 Peg b"}]})]
        self.assertEqual(check_answer("51 Peg b is a hot Jupiter.", evidence).unsupported_numbers, [])

    def test_a_value_next_to_a_name_is_still_checked(self):
        report = check_answer("Kepler-90 has 9 planets (NASA Exoplanet Archive).", MESSAGES)
        self.assertEqual([c.text for c in report.unsupported_numbers], ["9"])


class ParsingTests(unittest.TestCase):
    def test_bibcodes_of_different_shapes(self):
        text = "See 2016Natur.533..221G, 2014ApJ...781...18C and 2021PSJ.....2....1A."
        self.assertEqual(find_bibcodes(text), ["2016Natur.533..221G", "2014ApJ...781...18C",
                                               "2021PSJ.....2....1A"])

    def test_et_al_does_not_end_a_sentence(self):
        self.assertEqual(sentences("Found by Gillon et al. 2017 (2017Natur.542..456G). It is small."),
                         ["Found by Gillon et al. 2017 (2017Natur.542..456G)", "It is small."])


if __name__ == "__main__":
    unittest.main()
