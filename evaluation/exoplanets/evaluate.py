"""Score the exoplanet agent on exoplanets/qa_benchmark.json.

check_claims says whether what an answer says is true to its sources; this says whether
the answer is complete: every case lists papers a good answer must cite.

    PYTHONPATH=. python3 -m exoplanets.evaluate            # every case, with the Settings model
    PYTHONPATH=. python3 -m exoplanets.evaluate toi561b-atmosphere

Live runs spend model credits.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from scoop.exoplanets.agent import CheckedAnswer

BENCHMARK = Path(__file__).resolve().parent / "qa_benchmark.json"


@dataclass
class Score:
    case: str
    checked: bool
    missing: list[str]   # must_cite papers the answer did not cite
    attempts: int

    @property
    def passed(self) -> bool:
        return self.checked and not self.missing


def score(case: dict, answer: CheckedAnswer) -> Score:
    # The claims of the final answer (a clarification or proposal cites nothing).
    cited = {str(c.get("source")) for c in answer.report.claims} if answer.kind == "result" else set()
    return Score(case["id"], answer.checked, [b for b in case.get("must_cite", []) if b not in cited],
                 answer.attempts)


def settings_model():
    """The chat model saved in SCOOP's Settings tab, built as the server builds it."""
    from scoop.server.__main__ import make_model

    s = json.loads((Path.home() / ".config" / "scoop" / "settings.json").read_text())
    return make_model(s)


async def main(ids: list[str]) -> int:
    from scoop.exoplanets.agent import ask

    cases = [c for c in json.loads(BENCHMARK.read_text())["cases"] if not ids or c["id"] in ids]
    model, scores = settings_model(), []
    for case in cases:
        answer = await ask(case["question"], model)
        scores.append(s := score(case, answer))
        print(f"{'PASS' if s.passed else 'FAIL'}  {case['id']}: checked={s.checked} attempts={s.attempts}"
              + (f" missing={s.missing}" if s.missing else ""))
        print("      " + answer.text.replace("\n", "\n      "))
    print(f"\n{sum(s.passed for s in scores)}/{len(scores)} passed")
    return 0 if all(s.passed for s in scores) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
